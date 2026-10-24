"""Administrators: learner groups and bulk import of learners + plans."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import BulkImport, BulkImportRow, LearnerGroup, LearnerGroupMember, Plan, User, utcnow
from ..services import audit, billing, org_import, storage
from .auth import _client_ip
from .files import read_upload

router = APIRouter()


def group_public(g: LearnerGroup, db: Session) -> dict:
    n = db.scalar(select(func.count()).select_from(LearnerGroupMember).where(LearnerGroupMember.group_id == g.id)) or 0
    return {"id": g.id, "name": g.name, "description": g.description, "members": n,
            "created_at": g.created_at.isoformat() + "Z" if g.created_at else None}


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"groups": [group_public(g, db) for g in db.scalars(select(LearnerGroup).order_by(LearnerGroup.name))]}


@router.post("/groups", status_code=201)
def create_group(body: GroupBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.scalar(select(LearnerGroup.id).where(func.lower(LearnerGroup.name) == body.name.strip().lower())):
        raise HTTPException(status_code=409, detail="A group with this name exists.")
    g = LearnerGroup(name=body.name.strip(), description=body.description.strip(), created_by=admin.id)
    db.add(g)
    db.flush()
    audit.record(db, admin.id, "group.create", target_type="group", target_id=g.id, data={"name": g.name}, ip=_client_ip(request))
    db.commit()
    return {"group": group_public(g, db)}


@router.put("/groups/{group_id}")
def update_group(group_id: str, body: GroupBody, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    g = db.get(LearnerGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    g.name, g.description = body.name.strip(), body.description.strip()
    db.commit()
    return {"group": group_public(g, db)}


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    g = db.get(LearnerGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    audit.record(db, admin.id, "group.delete", target_type="group", target_id=g.id, data={"name": g.name}, ip=_client_ip(request))
    db.delete(g)
    db.commit()
    return {"ok": True}


@router.get("/groups/{group_id}/members")
def group_members(group_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    g = db.get(LearnerGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    users = db.scalars(select(User).join(LearnerGroupMember, LearnerGroupMember.user_id == User.id)
                       .where(LearnerGroupMember.group_id == g.id).order_by(User.username)).all()
    out = []
    for u in users:
        plan, sub = billing.current_plan(db, u)
        out.append({**u.public(), "plan_id": plan.id if plan else None,
                    "plan_ends": sub.period_end.isoformat() + "Z" if sub and sub.period_end else None})
    db.commit()
    return {"group": group_public(g, db), "members": out}


class MembersBody(BaseModel):
    user_ids: list[str] = []
    emails: list[str] = []


@router.post("/groups/{group_id}/members")
def add_members(group_id: str, body: MembersBody, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    g = db.get(LearnerGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    users = list(db.scalars(select(User).where(User.id.in_(body.user_ids[:1000])))) if body.user_ids else []
    if body.emails:
        users += list(db.scalars(select(User).where(User.email.in_([e.strip().lower() for e in body.emails[:1000]]))))
    added = 0
    for u in users:
        if db.get(LearnerGroupMember, (g.id, u.id)) is None:
            db.add(LearnerGroupMember(group_id=g.id, user_id=u.id))
            added += 1
    db.commit()
    found = {u.email for u in users if u.email}
    missing = [e for e in body.emails if e.strip().lower() not in found]
    return {"added": added, "not_found": missing, "group": group_public(g, db)}


@router.delete("/groups/{group_id}/members/{user_id}")
def remove_member(group_id: str, user_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    m = db.get(LearnerGroupMember, (group_id, user_id))
    if m is None:
        raise HTTPException(status_code=404, detail="Not a member.")
    db.delete(m)
    db.commit()
    return {"ok": True}


class GroupPlanBody(BaseModel):
    plan_id: str
    days: int = Field(default=30, ge=0, le=3650)


@router.post("/groups/{group_id}/plan")
def group_plan(group_id: str, body: GroupPlanBody, request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_admin)):
    """Give (or extend) a plan for every learner/educator in the group."""
    g = db.get(LearnerGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    if db.get(Plan, body.plan_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    users = db.scalars(select(User).join(LearnerGroupMember, LearnerGroupMember.user_id == User.id)
                       .where(LearnerGroupMember.group_id == g.id, User.is_active.is_(True))).all()
    n = 0
    for u in users:
        if u.role in billing.UNLIMITED_ROLES:
            continue
        billing.activate(db, u.id, body.plan_id, days=body.days, source="admin")
        n += 1
    audit.record(db, admin.id, "group.plan", target_type="group", target_id=g.id,
                 data={"plan": body.plan_id, "days": body.days, "users": n}, ip=_client_ip(request))
    db.commit()
    return {"updated": n}


@router.post("/groups/{group_id}/plan/revoke")
def group_revoke(group_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    g = db.get(LearnerGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    ids = db.scalars(select(LearnerGroupMember.user_id).where(LearnerGroupMember.group_id == g.id)).all()
    n = 0
    for uid in ids:
        sub = billing.active_subscription(db, uid)
        if sub is not None:
            sub.status, sub.period_end, sub.canceled_at = "expired", utcnow(), utcnow()
            n += 1
    audit.record(db, admin.id, "group.plan_revoke", target_type="group", target_id=g.id, data={"users": n},
                 ip=_client_ip(request))
    db.commit()
    return {"updated": n}


# ── Bulk import ──────────────────────────────────────────────────────────────


def import_public(imp: BulkImport) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {"id": imp.id, "kind": imp.kind, "filename": imp.filename, "options": imp.options, "status": imp.status,
            "total_rows": imp.total_rows, "ok_rows": imp.ok_rows, "error_rows": imp.error_rows, "summary": imp.summary,
            "created_at": iso(imp.created_at), "applied_at": iso(imp.applied_at)}


def row_public(r: BulkImportRow) -> dict:
    d = dict(r.data or {})
    d.pop("pending_on_accept", None)
    return {"row": r.row_number, "data": d, "action": r.action, "status": r.status, "error": r.error}


@router.get("/import/template")
def template(format: Literal["csv", "xlsx"] = "csv", _: User = Depends(require_admin)):
    if format == "xlsx":
        return Response(org_import.template_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="pathmind-learners.xlsx"'})
    return Response(org_import.template_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="pathmind-learners.csv"'})


@router.post("/import/learners", status_code=201)
def upload_learners(
    request: Request, file: UploadFile = File(...), default_plan: str = Form(""), default_days: str = Form(""),
    group_id: str = Form(""), mode: Literal["password", "invite"] = Form("password"), invite_days: int = Form(14),
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    data = read_upload(file, 20 * 1024 * 1024)
    try:
        rows = org_import.read_table(file.filename or "", data)
    except org_import.ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if default_plan and db.get(Plan, default_plan) is None:
        raise HTTPException(status_code=422, detail="Unknown default plan.")
    if group_id and db.get(LearnerGroup, group_id) is None:
        raise HTTPException(status_code=422, detail="Unknown group.")
    f = storage.save_bytes(db, request.app.state.settings.data_dir, owner_id=admin.id,
                           filename=file.filename or "import.csv", data=data, mime_type=file.content_type or "",
                           purpose="import")
    imp = BulkImport(kind="learner_plans", file_id=f.id, filename=(file.filename or "import")[:255], created_by=admin.id,
                     options={"default_plan": default_plan, "default_days": default_days, "group_id": group_id or None,
                              "mode": mode, "invite_days": max(1, min(invite_days, 30))})
    db.add(imp)
    db.flush()
    org_import.preview(db, imp, rows)
    db.commit()
    return {"import": import_public(imp), "rows": [row_public(r) for r in _rows(db, imp)]}


def _rows(db: Session, imp: BulkImport) -> list[BulkImportRow]:
    return list(db.scalars(select(BulkImportRow).where(BulkImportRow.import_id == imp.id).order_by(BulkImportRow.row_number)))


def _import(db: Session, import_id: str) -> BulkImport:
    imp = db.get(BulkImport, import_id)
    if imp is None:
        raise HTTPException(status_code=404, detail="Import not found.")
    return imp


@router.get("/imports")
def list_imports(kind: str = "learner_plans", db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(BulkImport).where(BulkImport.kind == kind).order_by(BulkImport.created_at.desc()).limit(100))
    return {"imports": [import_public(i) for i in rows]}


@router.get("/import/{import_id}")
def get_import(import_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    imp = _import(db, import_id)
    return {"import": import_public(imp), "rows": [row_public(r) for r in _rows(db, imp)]}


@router.post("/import/{import_id}/apply")
def apply_import(import_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    with request.app.state.accounts_lock:
        imp = _import(db, import_id)
        if imp.kind != "learner_plans" or imp.status != "previewed":
            raise HTTPException(status_code=409, detail="This import was already applied or canceled.")
        result = org_import.apply(db, imp, admin)
        audit.record(db, admin.id, "import.learners", target_type="import", target_id=imp.id,
                     data=imp.summary, ip=_client_ip(request))
        db.commit()
    return {"import": import_public(imp), "rows": [row_public(r) for r in _rows(db, imp)], **result}


@router.post("/import/{import_id}/cancel")
def cancel_import(import_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    imp = _import(db, import_id)
    if imp.status != "previewed":
        raise HTTPException(status_code=409, detail="Only a previewed import can be canceled.")
    imp.status = "canceled"
    db.commit()
    return {"import": import_public(imp)}


@router.get("/import/{import_id}/report.csv")
def import_report(import_id: str, errors_only: bool = False, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    imp = _import(db, import_id)
    return Response(org_import.rows_csv(_rows(db, imp), errors_only), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="import-{imp.id[:8]}-report.csv"'})
