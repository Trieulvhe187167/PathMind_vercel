"""Contributions: educators propose documents (/api/contribute), R&D reviews them (/api/rnd).

Also the educator view of open knowledge gaps and rewards, and the R&D view of gaps, answer
feedback and the rewards leaderboard.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import get_db, require_roles
from ..models import (
    AnswerFeedback, ChatMessage, Contribution, ContributionEvent, ContributionFile, KnowledgeDomain,
    KnowledgeGap, RewardLedger, StoredFile, User, UserBadge, UsageDaily, VerifiedDocument, utcnow,
)
from ..services import audit, billing, extract, storage
from ..services import knowledge_tree as kt
from ..services.app_settings import get_setting
from ..services.notify import notify
from .auth import _client_ip
from .files import public_file, read_upload

router = APIRouter()  # /api/contribute  (educators)
rnd_router = APIRouter()  # /api/rnd
educator = require_roles("educator")
rnd = require_roles("rnd")
rnd_or_admin = require_roles("rnd", "admin")

EDITABLE = ("draft", "changes_requested")


def _user_name(db: Session, uid: str | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return (u.full_name or u.username) if u else None


def contribution_public(c: Contribution, db: Session, full: bool = False) -> dict:
    d = db.get(KnowledgeDomain, c.domain_id) if c.domain_id else None
    out = {"id": c.id, "title": c.title, "description": c.description, "status": c.status, "revision": c.revision,
           "domain_id": c.domain_id, "domain": d.name if d else None, "gap_id": c.gap_id,
           "suggested_node_id": c.suggested_node_id, "contributor_id": c.contributor_id,
           "contributor": _user_name(db, c.contributor_id), "reviewer": _user_name(db, c.reviewer_id),
           "submitted_at": kt.iso(c.submitted_at), "decided_at": kt.iso(c.decided_at), "decision_note": c.decision_note,
           "created_at": kt.iso(c.created_at), "updated_at": kt.iso(c.updated_at)}
    if full:
        files = db.execute(select(ContributionFile, StoredFile).join(StoredFile, StoredFile.id == ContributionFile.file_id)
                           .where(ContributionFile.contribution_id == c.id).order_by(ContributionFile.created_at)).all()
        out["files"] = [{**public_file(f), "revision": cf.revision, "note": cf.note, "link_id": cf.id} for cf, f in files]
        events = db.scalars(select(ContributionEvent).where(ContributionEvent.contribution_id == c.id)
                            .order_by(ContributionEvent.id)).all()
        out["events"] = [{"id": e.id, "kind": e.kind, "from_status": e.from_status, "to_status": e.to_status,
                          "body": e.body, "actor": _user_name(db, e.actor_id), "actor_id": e.actor_id,
                          "created_at": kt.iso(e.created_at)} for e in events]
        out["documents"] = [kt.document_public(v) for v in db.scalars(
            select(VerifiedDocument).where(VerifiedDocument.contribution_id == c.id))]
        if c.gap_id:
            g = db.get(KnowledgeGap, c.gap_id)
            out["gap"] = kt.gap_public(g, db) if g else None
    return out


def _upload_revision(c: Contribution) -> int:
    """Files added while changes are requested belong to the next round (the revision bumps on resubmit)."""
    return c.revision + 1 if c.status == "changes_requested" else c.revision


def _event(db: Session, c: Contribution, actor_id: str | None, kind: str, body: str = "", to: str = "") -> None:
    db.add(ContributionEvent(contribution_id=c.id, actor_id=actor_id, kind=kind, body=body[:5000],
                             from_status=c.status if to else "", to_status=to))


def _move(db: Session, c: Contribution, actor: User, to: str, body: str = "") -> None:
    _event(db, c, actor.id, "status", body, to)
    c.status = to
    c.updated_at = utcnow()


# ── Educator side ─────────────────────────────────────────────────────────────


@router.get("/domains")
def domains(db: Session = Depends(get_db), _: User = Depends(educator)):
    """Only names: educators choose where a contribution belongs without seeing the tree."""
    rows = db.scalars(select(KnowledgeDomain).where(KnowledgeDomain.status == "active").order_by(KnowledgeDomain.name))
    return {"domains": [{"id": d.id, "name": d.name, "description": d.description} for d in rows]}


@router.get("/gaps")
def open_gaps(db: Session = Depends(get_db), _: User = Depends(educator)):
    rows = db.scalars(select(KnowledgeGap).where(KnowledgeGap.status.in_(("open", "requested")))
                      .order_by(KnowledgeGap.status.desc(), KnowledgeGap.ask_count.desc()).limit(200)).all()
    return {"gaps": [kt.gap_public(g, db) for g in rows]}


@router.get("/contributions")
def my_contributions(db: Session = Depends(get_db), user: User = Depends(educator)):
    rows = db.scalars(select(Contribution).where(Contribution.contributor_id == user.id).order_by(Contribution.updated_at.desc()))
    return {"contributions": [contribution_public(c, db) for c in rows]}


class ContributionBody(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=20000)
    domain_id: str | None = None
    gap_id: str | None = None


def _check_refs(db: Session, body: ContributionBody) -> None:
    if body.domain_id and db.get(KnowledgeDomain, body.domain_id) is None:
        raise HTTPException(status_code=422, detail="Unknown domain.")
    if body.gap_id and db.get(KnowledgeGap, body.gap_id) is None:
        raise HTTPException(status_code=422, detail="Unknown knowledge gap.")


@router.post("/contributions", status_code=201)
def create_contribution(body: ContributionBody, db: Session = Depends(get_db), user: User = Depends(educator)):
    _check_refs(db, body)
    c = Contribution(contributor_id=user.id, title=body.title.strip(), description=body.description,
                     domain_id=body.domain_id, gap_id=body.gap_id, status="draft")
    db.add(c)
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


def _mine(db: Session, user: User, cid: str) -> Contribution:
    c = db.get(Contribution, cid)
    if c is None or c.contributor_id != user.id:
        raise HTTPException(status_code=404, detail="Contribution not found.")
    return c


@router.get("/contributions/{cid}")
def get_mine(cid: str, db: Session = Depends(get_db), user: User = Depends(educator)):
    return {"contribution": contribution_public(_mine(db, user, cid), db, True)}


@router.put("/contributions/{cid}")
def update_mine(cid: str, body: ContributionBody, db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    if c.status not in EDITABLE:
        raise HTTPException(status_code=409, detail="It can only be edited as a draft or when changes were requested.")
    _check_refs(db, body)
    c.title, c.description, c.domain_id, c.gap_id = body.title.strip(), body.description, body.domain_id, body.gap_id
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


@router.post("/contributions/{cid}/files", status_code=201)
def add_file(cid: str, request: Request, file: UploadFile = File(...), note: str = Form(""),
             db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    if c.status not in EDITABLE:
        raise HTTPException(status_code=409, detail="Files can only be added to a draft or when changes were requested.")
    data = read_upload(file, 50 * 1024 * 1024)
    try:
        extract.extract(file.filename or "", data, file.content_type or "")  # make sure R&D can read it
    except extract.ExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    f = storage.save_bytes(db, request.app.state.settings.data_dir, owner_id=user.id, filename=file.filename or "file",
                           data=data, mime_type=file.content_type or "", purpose="contribution")
    db.add(ContributionFile(contribution_id=c.id, file_id=f.id, revision=_upload_revision(c), note=note[:500]))
    _event(db, c, user.id, "file", f"Added {f.filename}")
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


@router.delete("/contributions/{cid}/files/{link_id}")
def remove_file(cid: str, link_id: str, db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    link = db.get(ContributionFile, link_id)
    if link is None or link.contribution_id != c.id:
        raise HTTPException(status_code=404, detail="File not found.")
    if c.status not in EDITABLE or link.revision != _upload_revision(c):
        raise HTTPException(status_code=409, detail="Files sent in an earlier round stay as a record.")
    db.delete(link)
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


@router.post("/contributions/{cid}/submit")
def submit(cid: str, db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    if c.status not in EDITABLE:
        raise HTTPException(status_code=409, detail="Already sent.")
    if not db.scalar(select(ContributionFile.id).where(ContributionFile.contribution_id == c.id).limit(1)):
        raise HTTPException(status_code=422, detail="Attach at least one document.")
    if not c.domain_id:
        raise HTTPException(status_code=422, detail="Choose the domain it belongs to.")
    resubmit = c.status == "changes_requested"
    if resubmit:
        c.revision += 1
    _move(db, c, user, "submitted", "Resubmitted with changes." if resubmit else "Submitted for review.")
    c.submitted_at = utcnow()
    if c.gap_id:
        g = db.get(KnowledgeGap, c.gap_id)
        if g is not None and g.status == "open":
            g.status = "requested"
    for rid in db.scalars(select(User.id).where(User.role == "rnd", User.is_active.is_(True))):
        notify(db, rid, "contribution_submitted", f"New contribution: {c.title}", actor_id=user.id,
               target_type="contribution", target_id=c.id)
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


@router.post("/contributions/{cid}/withdraw")
def withdraw(cid: str, db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    if c.status in ("approved", "integrated", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail="It can no longer be withdrawn.")
    _move(db, c, user, "withdrawn", "Withdrawn by the contributor.")
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


@router.delete("/contributions/{cid}")
def delete_draft(cid: str, db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    if c.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft that was never sent can be deleted.")
    db.delete(c)
    db.commit()
    return {"ok": True}


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


def _comment(db: Session, c: Contribution, user: User, text: str) -> None:
    _event(db, c, user.id, "comment", text)
    c.updated_at = utcnow()
    if user.id == c.contributor_id:
        if c.reviewer_id:
            notify(db, c.reviewer_id, "contribution_comment", f"{user.username} replied on '{c.title}'", actor_id=user.id,
                   target_type="contribution", target_id=c.id)
    else:
        notify(db, c.contributor_id, "contribution_comment", f"R&D commented on '{c.title}'", actor_id=user.id,
               target_type="contribution", target_id=c.id)


@router.post("/contributions/{cid}/comments")
def comment_mine(cid: str, body: CommentBody, db: Session = Depends(get_db), user: User = Depends(educator)):
    c = _mine(db, user, cid)
    _comment(db, c, user, body.body)
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


# ── Rewards (educator) ───────────────────────────────────────────────────────


def leaderboard(db: Session, days: int | None = 30, limit: int = 10) -> list[dict]:
    stmt = select(RewardLedger.user_id, func.sum(RewardLedger.points)).where(RewardLedger.points > 0)
    if days:
        stmt = stmt.where(RewardLedger.created_at >= utcnow() - timedelta(days=days))
    rows = db.execute(stmt.group_by(RewardLedger.user_id).order_by(func.sum(RewardLedger.points).desc()).limit(limit)).all()
    out = []
    for uid, pts in rows:
        n = db.scalar(select(func.count()).select_from(Contribution).where(
            Contribution.contributor_id == uid, Contribution.status == "integrated")) or 0
        out.append({"user_id": uid, "name": _user_name(db, uid), "points": int(pts or 0), "integrated": n})
    return out


def _ledger(db: Session, user_id: str, limit: int = 100) -> list[dict]:
    rows = db.scalars(select(RewardLedger).where(RewardLedger.user_id == user_id).order_by(RewardLedger.id.desc()).limit(limit))
    return [{"id": r.id, "points": r.points, "reason": r.reason, "note": r.note, "contribution_id": r.contribution_id,
             "created_at": kt.iso(r.created_at)} for r in rows]


@router.get("/rewards")
def my_rewards(db: Session = Depends(get_db), user: User = Depends(educator)):
    rw = get_setting(db, "rewards")
    badges = db.scalars(select(UserBadge).where(UserBadge.user_id == user.id).order_by(UserBadge.awarded_at)).all()
    return {"balance": kt.balance(db, user.id), "ledger": _ledger(db, user.id),
            "badges": [{"badge": b.badge, "awarded_at": kt.iso(b.awarded_at)} for b in badges],
            "leaderboard": leaderboard(db, 30), "leaderboard_all_time": leaderboard(db, None),
            "rates": {k: rw.get(k) for k in ("points_integrated", "points_cited", "points_gap_resolved", "redeem_enabled",
                                             "points_per_plan_day", "redeem_plan_id", "points_per_1k_credits")}}


class RedeemBody(BaseModel):
    kind: Literal["plan_days", "credits"]
    amount: int = Field(ge=1, le=10000)  # days, or thousands of credits


@router.post("/rewards/redeem")
def redeem(body: RedeemBody, request: Request, db: Session = Depends(get_db), user: User = Depends(educator)):
    rw = get_setting(db, "rewards")
    if not rw.get("redeem_enabled"):
        raise HTTPException(status_code=409, detail="Redeeming points is turned off.")
    with request.app.state.accounts_lock:
        if body.kind == "plan_days":
            cost = body.amount * int(rw.get("points_per_plan_day") or 0)
        else:
            cost = body.amount * int(rw.get("points_per_1k_credits") or 0)
        if cost <= 0:
            raise HTTPException(status_code=409, detail="This reward is not available.")
        if kt.balance(db, user.id) < cost:
            raise HTTPException(status_code=409, detail="Not enough points.")
        if body.kind == "plan_days":
            plan_id = rw.get("redeem_plan_id") or "plus"
            current, _ = billing.current_plan(db, user)
            # Extend the plan they already have when it is at least as good; else the redeem plan.
            target = current.id if current and current.id != "free" else plan_id
            billing.activate(db, user.id, target, days=body.amount, source="system")
            kt.award(db, user.id, -cost, "redeem_plan_days", note=f"{body.amount} day(s) of {target}")
        else:
            today = utcnow().date()
            row = db.get(UsageDaily, (user.id, today))
            if row is None:
                row = UsageDaily(user_id=user.id, day=today, prompt_tokens=0, cached_tokens=0, completion_tokens=0,
                                 credits_used=0, storage_bytes=0)
                db.add(row)
            row.credits_used = (row.credits_used or 0) - body.amount * 1000  # negative usage = extra credits
            kt.award(db, user.id, -cost, "redeem_credits", note=f"{body.amount * 1000} credits")
        db.commit()
    return {"balance": kt.balance(db, user.id), "summary": billing.summary(db, user)}


# ── R&D side ─────────────────────────────────────────────────────────────────


@rnd_router.get("/contributions")
def queue(status: str = "open", db: Session = Depends(get_db), _: User = Depends(rnd_or_admin)):
    stmt = select(Contribution).where(Contribution.status != "draft")
    if status == "open":
        stmt = stmt.where(Contribution.status.in_(("submitted", "in_review")))
    elif status != "all":
        stmt = stmt.where(Contribution.status == status)
    rows = db.scalars(stmt.order_by(Contribution.submitted_at)).all()
    return {"contributions": [contribution_public(c, db) for c in rows]}


def _any(db: Session, cid: str) -> Contribution:
    c = db.get(Contribution, cid)
    if c is None or c.status == "draft":
        raise HTTPException(status_code=404, detail="Contribution not found.")
    return c


@rnd_router.get("/contributions/{cid}")
def review_view(cid: str, db: Session = Depends(get_db), _: User = Depends(rnd_or_admin)):
    c = _any(db, cid)
    return {"contribution": contribution_public(c, db, True), "balance": kt.balance(db, c.contributor_id)}


@rnd_router.post("/contributions/{cid}/claim")
def claim(cid: str, db: Session = Depends(get_db), user: User = Depends(rnd)):
    c = _any(db, cid)
    if c.status not in ("submitted", "in_review"):
        raise HTTPException(status_code=409, detail="It is not waiting for review.")
    c.reviewer_id = user.id
    if c.status == "submitted":
        _move(db, c, user, "in_review", f"{user.username} is reviewing.")
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


class DecisionBody(BaseModel):
    note: str = Field(default="", max_length=5000)


@rnd_router.post("/contributions/{cid}/request-changes")
def request_changes(cid: str, body: DecisionBody, db: Session = Depends(get_db), user: User = Depends(rnd)):
    c = _any(db, cid)
    if c.status not in ("submitted", "in_review"):
        raise HTTPException(status_code=409, detail="It is not under review.")
    if not body.note.strip():
        raise HTTPException(status_code=422, detail="Say what should change.")
    c.reviewer_id = c.reviewer_id or user.id
    c.decision_note = body.note
    _move(db, c, user, "changes_requested", body.note)
    notify(db, c.contributor_id, "contribution_changes", f"R&D asked for changes to '{c.title}'", actor_id=user.id,
           target_type="contribution", target_id=c.id)
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


@rnd_router.post("/contributions/{cid}/reject")
def reject(cid: str, body: DecisionBody, request: Request, db: Session = Depends(get_db), user: User = Depends(rnd)):
    c = _any(db, cid)
    if c.status not in ("submitted", "in_review", "changes_requested"):
        raise HTTPException(status_code=409, detail="It cannot be rejected now.")
    if not body.note.strip():
        raise HTTPException(status_code=422, detail="Give the reason.")
    c.reviewer_id = c.reviewer_id or user.id
    c.decision_note, c.decided_at = body.note, utcnow()
    _move(db, c, user, "rejected", body.note)
    notify(db, c.contributor_id, "contribution_rejected", f"'{c.title}' was not accepted: {body.note[:150]}",
           actor_id=user.id, target_type="contribution", target_id=c.id)
    audit.record(db, user.id, "contribution.reject", target_type="contribution", target_id=c.id, ip=_client_ip(request))
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


class ApproveBody(BaseModel):
    note: str = Field(default="", max_length=5000)
    domain_id: str | None = None


@rnd_router.post("/contributions/{cid}/approve")
def approve(cid: str, body: ApproveBody, request: Request, db: Session = Depends(get_db), user: User = Depends(rnd)):
    """Accept the content: each file becomes a pending verified document, ready to be placed in the tree."""
    c = _any(db, cid)
    if c.status not in ("submitted", "in_review"):
        raise HTTPException(status_code=409, detail="It is not under review.")
    domain_id = body.domain_id or c.domain_id
    if not domain_id or db.get(KnowledgeDomain, domain_id) is None:
        raise HTTPException(status_code=422, detail="Choose the domain.")
    c.domain_id = domain_id
    files = db.execute(select(ContributionFile, StoredFile).join(StoredFile, StoredFile.id == ContributionFile.file_id)
                       .where(ContributionFile.contribution_id == c.id, ContributionFile.revision == c.revision)).all()
    data_dir = request.app.state.settings.data_dir
    docs = []
    for cf, f in files:
        try:
            ex = extract.extract(f.filename, storage.read_bytes(db, data_dir, f), f.mime_type)
        except (extract.ExtractError, storage.StorageError):
            continue
        text = "\f".join(ex.pages) if ex.pages else ex.text
        doc = VerifiedDocument(domain_id=domain_id, file_id=f.id, contribution_id=c.id,
                               title=(c.title if len(files) == 1 else f"{c.title} - {f.filename}")[:300],
                               status="pending", text=text, page_count=len(ex.pages))
        db.add(doc)
        docs.append(doc)
    if not docs:
        raise HTTPException(status_code=422, detail="None of the files could be read.")
    c.reviewer_id = c.reviewer_id or user.id
    c.decision_note, c.decided_at = body.note, utcnow()
    _move(db, c, user, "approved", body.note or "Approved. It will appear in the tree once published.")
    notify(db, c.contributor_id, "contribution_approved", f"'{c.title}' was approved", actor_id=user.id,
           target_type="contribution", target_id=c.id)
    audit.record(db, user.id, "contribution.approve", target_type="contribution", target_id=c.id, ip=_client_ip(request))
    db.commit()
    return {"contribution": contribution_public(c, db, True), "documents": [kt.document_public(d, db) for d in docs]}


@rnd_router.post("/contributions/{cid}/comments")
def comment_rnd(cid: str, body: CommentBody, db: Session = Depends(get_db), user: User = Depends(rnd)):
    c = _any(db, cid)
    _comment(db, c, user, body.body)
    db.commit()
    return {"contribution": contribution_public(c, db, True)}


class BonusBody(BaseModel):
    points: int = Field(ge=1, le=1000)
    note: str = Field(min_length=1, max_length=500)


@rnd_router.post("/contributions/{cid}/bonus")
def bonus(cid: str, body: BonusBody, request: Request, db: Session = Depends(get_db), user: User = Depends(rnd)):
    c = _any(db, cid)
    if c.status not in ("approved", "integrated"):
        raise HTTPException(status_code=409, detail="Bonus points are for approved contributions.")
    kt.award(db, c.contributor_id, body.points, "quality_bonus", contribution_id=c.id, note=body.note, granted_by=user.id)
    notify(db, c.contributor_id, "reward_bonus", f"+{body.points} points for '{c.title}': {body.note[:120]}",
           actor_id=user.id, target_type="contribution", target_id=c.id)
    audit.record(db, user.id, "reward.bonus", target_type="user", target_id=c.contributor_id,
                 data={"points": body.points}, ip=_client_ip(request))
    db.commit()
    return {"balance": kt.balance(db, c.contributor_id)}


class FromContributionBody(BaseModel):
    parent_node_id: str | None = None
    use_ai: bool = False


@rnd_router.post("/contributions/{cid}/propose", status_code=201)
def propose_from_contribution(cid: str, body: FromContributionBody, db: Session = Depends(get_db), user: User = Depends(rnd)):
    c = _any(db, cid)
    if c.status != "approved":
        raise HTTPException(status_code=409, detail="Approve the contribution first.")
    docs = db.scalars(select(VerifiedDocument).where(VerifiedDocument.contribution_id == c.id,
                                                     VerifiedDocument.status == "pending")).all()
    if not docs:
        raise HTTPException(status_code=409, detail="No pending documents from this contribution.")
    cs = kt.propose_from_document(db, docs[0], user, parent_id=body.parent_node_id, use_ai=body.use_ai, contribution_id=c.id)
    for extra in docs[1:]:
        kt.add_change(db, cs, "attach_document", temp_key="n1", payload={"document_id": extra.id, "node_temp_key": "n1"},
                      origin="suggested")
        kt.add_change(db, cs, "verify_document", payload={"document_id": extra.id}, origin="suggested")
    if c.gap_id:
        gap = db.get(KnowledgeGap, c.gap_id)
        if gap is not None:
            gap.resolved_by_change_set_id = cs.id
    db.commit()
    return {"change_set": kt.change_set_public(cs, db, True)}


# ── Gaps and feedback (R&D) ───────────────────────────────────────────────────


@rnd_router.get("/gaps")
def gaps(status: str = "open", domain_id: str = "", db: Session = Depends(get_db), _: User = Depends(rnd_or_admin)):
    stmt = select(KnowledgeGap)
    if status != "all":
        stmt = stmt.where(KnowledgeGap.status == status)
    if domain_id:
        stmt = stmt.where(KnowledgeGap.domain_id == domain_id)
    rows = db.scalars(stmt.order_by(KnowledgeGap.ask_count.desc(), KnowledgeGap.last_asked_at.desc()).limit(300)).all()
    return {"gaps": [kt.gap_public(g, db) for g in rows]}


class GapPatch(BaseModel):
    status: Literal["open", "requested", "resolved", "dismissed"] | None = None
    domain_id: str | None = None


@rnd_router.patch("/gaps/{gap_id}")
def patch_gap(gap_id: str, body: GapPatch, db: Session = Depends(get_db), user: User = Depends(rnd)):
    g = db.get(KnowledgeGap, gap_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Gap not found.")
    if body.status:
        if body.status == "requested" and g.status != "requested":
            for eid in db.scalars(select(User.id).where(User.role == "educator", User.is_active.is_(True))):
                notify(db, eid, "gap_requested", f"R&D is looking for material: {g.question[:150]}", actor_id=user.id,
                       target_type="gap", target_id=g.id)
        g.status = body.status
    if body.domain_id is not None:
        g.domain_id = body.domain_id or None
    db.commit()
    return {"gap": kt.gap_public(g, db)}


@rnd_router.get("/feedback")
def feedback(status: str = "new", db: Session = Depends(get_db), _: User = Depends(rnd_or_admin)):
    stmt = select(AnswerFeedback, ChatMessage).join(ChatMessage, ChatMessage.id == AnswerFeedback.message_id)
    if status != "all":
        stmt = stmt.where(AnswerFeedback.status == status)
    rows = db.execute(stmt.order_by(AnswerFeedback.created_at.desc()).limit(300)).all()
    out = []
    for fb, msg in rows:
        question = db.scalar(select(ChatMessage.content).where(ChatMessage.session_id == msg.session_id,
                                                               ChatMessage.seq < msg.seq, ChatMessage.role == "user")
                             .order_by(ChatMessage.seq.desc()).limit(1))
        out.append({"message_id": fb.message_id, "user_id": fb.user_id, "rating": fb.rating, "reason": fb.reason,
                    "comment": fb.comment, "status": fb.status, "created_at": kt.iso(fb.created_at),
                    "question": question or "", "answer": msg.content[:3000], "grounding": msg.grounding})
    return {"feedback": out}


@rnd_router.post("/feedback/{message_id}/{user_id}/reviewed")
def feedback_reviewed(message_id: str, user_id: str, db: Session = Depends(get_db), _: User = Depends(rnd)):
    fb = db.get(AnswerFeedback, (message_id, user_id))
    if fb is None:
        raise HTTPException(status_code=404, detail="Feedback not found.")
    fb.status = "reviewed"
    db.commit()
    return {"ok": True}


@rnd_router.get("/rewards")
def rewards_overview(db: Session = Depends(get_db), _: User = Depends(rnd_or_admin)):
    recent = db.scalars(select(RewardLedger).order_by(RewardLedger.id.desc()).limit(100)).all()
    return {"leaderboard": leaderboard(db, 30, 20), "leaderboard_all_time": leaderboard(db, None, 20),
            "recent": [{"id": r.id, "user": _user_name(db, r.user_id), "points": r.points, "reason": r.reason,
                        "note": r.note, "created_at": kt.iso(r.created_at)} for r in recent]}

