"""Conversations with the AI tutor. Answers stream as server-sent events.

Events: meta {user_message, assistant_message_id, turn_id}, status {text}, delta {text},
sources {citations, grounding}, done {message}, error {message}.
"""

from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db, require_roles
from ..models import (
    AnswerFeedback, ChatMessage, ChatMessageCitation, ChatSession, ChatSessionKnowledgeBase, ChatTurn, Course, Partner,
    PartnerShare, Persona, User, utcnow,
)
from ..services import billing, llm, tutor
from ..services.app_settings import get_setting

router = APIRouter()  # /api/chat
rnd_router = APIRouter()  # /api/rnd


def iso(d):
    return d.isoformat() + "Z" if d else None


def session_public(s: ChatSession, db: Session) -> dict:
    kbs = list(db.scalars(select(ChatSessionKnowledgeBase.knowledge_base_id).where(ChatSessionKnowledgeBase.session_id == s.id)))
    return {"id": s.id, "title": s.title, "mode": s.mode, "model": s.model, "persona_id": s.persona_id,
            "partner_id": s.partner_id, "course_id": s.course_id, "is_pinned": s.is_pinned,
            "archived": s.archived_at is not None, "kb_ids": kbs, "last_message_at": iso(s.last_message_at),
            "created_at": iso(s.created_at)}


def message_public(m: ChatMessage, db: Session, viewer: User) -> dict:
    cites = db.scalars(select(ChatMessageCitation).where(ChatMessageCitation.message_id == m.id).order_by(ChatMessageCitation.seq))
    fb = db.get(AnswerFeedback, (m.id, viewer.id)) if m.role == "assistant" else None
    return {"id": m.id, "seq": m.seq, "role": m.role, "content": m.content, "attachments": m.attachments or [],
            "grounding": m.grounding, "created_at": iso(m.created_at),
            "citations": [tutor.citation_view(db, c, viewer) for c in cites],
            "feedback": {"rating": fb.rating, "reason": fb.reason} if fb else None}


def can_use_partner(db: Session, user: User, partner_id: str) -> bool:
    p = db.get(Partner, partner_id)
    if p is None:
        return False
    return p.owner_id == user.id or db.get(PartnerShare, (p.id, user.id)) is not None


class SessionBody(BaseModel):
    title: str = Field(default="", max_length=200)
    mode: str = "chat"
    model: str = Field(default="", max_length=120)
    persona_id: str | None = None
    partner_id: str | None = None
    course_id: str | None = None
    kb_ids: list[str] = []


def _validate(db: Session, user: User, body) -> None:
    if getattr(body, "mode", None) and body.mode not in tutor.MODES:
        raise HTTPException(status_code=422, detail="Unknown mode.")
    if getattr(body, "partner_id", None) and not can_use_partner(db, user, body.partner_id):
        raise HTTPException(status_code=404, detail="Partner not found.")
    if getattr(body, "persona_id", None):
        p = db.get(Persona, body.persona_id)
        if p is None or (p.owner_id and p.owner_id != user.id):
            raise HTTPException(status_code=404, detail="Persona not found.")
    if getattr(body, "course_id", None):
        c = db.get(Course, body.course_id)
        if c is None or c.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Course not found.")


def _set_kbs(db: Session, user: User, s: ChatSession, kb_ids: list[str]) -> None:
    kbs = tutor.accessible_kbs(db, user, kb_ids)
    db.query(ChatSessionKnowledgeBase).filter(ChatSessionKnowledgeBase.session_id == s.id).delete()
    for kb in kbs:
        db.add(ChatSessionKnowledgeBase(session_id=s.id, knowledge_base_id=kb.id))


@router.get("/sessions")
def list_sessions(q: str = "", archived: bool = False, course_id: str = "", partner_id: str = "", limit: int = 100,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    stmt = select(ChatSession).where(ChatSession.user_id == user.id)
    stmt = stmt.where(ChatSession.archived_at.is_not(None) if archived else ChatSession.archived_at.is_(None))
    if course_id:
        stmt = stmt.where(ChatSession.course_id == course_id)
    if partner_id:
        stmt = stmt.where(ChatSession.partner_id == partner_id)
    if q.strip():
        term = f"%{q.strip().lower()}%"
        sub = select(ChatMessage.session_id).where(func.lower(ChatMessage.content).like(term))
        stmt = stmt.where(or_(func.lower(ChatSession.title).like(term), ChatSession.id.in_(sub)))
    stmt = stmt.order_by(ChatSession.is_pinned.desc(), func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())
    return {"sessions": [session_public(s, db) for s in db.scalars(stmt.limit(max(1, min(limit, 500))))]}


@router.post("/sessions", status_code=201)
def create_session(body: SessionBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _validate(db, user, body)
    s = ChatSession(user_id=user.id, title=body.title.strip(), mode=body.mode, model=body.model.strip(),
                    persona_id=body.persona_id, partner_id=body.partner_id, course_id=body.course_id)
    db.add(s)
    db.flush()
    _set_kbs(db, user, s, body.kb_ids)
    db.commit()
    return {"session": session_public(s, db)}


def _own(db: Session, user: User, sid: str) -> ChatSession:
    s = db.get(ChatSession, sid)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return s


@router.get("/sessions/{sid}")
def get_session(sid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _own(db, user, sid)
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.session_id == s.id).order_by(ChatMessage.seq)).all()
    return {"session": session_public(s, db), "messages": [message_public(m, db, user) for m in msgs]}


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None
    archived: bool | None = None
    mode: str | None = None
    model: str | None = Field(default=None, max_length=120)
    persona_id: str | None = None
    partner_id: str | None = None
    course_id: str | None = None
    kb_ids: list[str] | None = None


@router.patch("/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _own(db, user, sid)
    _validate(db, user, body)
    if body.title is not None:
        s.title = body.title.strip()
    if body.is_pinned is not None:
        s.is_pinned = body.is_pinned
    if body.archived is not None:
        s.archived_at = utcnow() if body.archived else None
    if body.mode is not None:
        s.mode = body.mode
    if body.model is not None:
        s.model = body.model.strip()
    for f in ("persona_id", "partner_id", "course_id"):
        v = getattr(body, f)
        if v is not None:
            setattr(s, f, v or None)
    if body.kb_ids is not None:
        _set_kbs(db, user, s, body.kb_ids)
    db.commit()
    return {"session": session_public(s, db)}


@router.delete("/sessions/{sid}")
def delete_session(sid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _own(db, user, sid)
    db.delete(s)
    db.commit()
    return {"ok": True}


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    attachments: list[str] = []
    mode: str | None = None
    model: str | None = None
    kb_ids: list[str] = []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/sessions/{sid}/messages")
def send(sid: str, body: MessageBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _own(db, user, sid)
    mode = body.mode or s.mode or "chat"
    if mode not in tutor.MODES:
        raise HTTPException(status_code=422, detail="Unknown mode.")
    billing.check_credits(db, user)
    partner_model = None
    if s.partner_id:
        p = db.get(Partner, s.partner_id)
        partner_model = p.model if p else None
    target = llm.resolve(db, user, body.model or s.model or partner_model or None)  # 503 / 402 / 422 before streaming
    seq = (db.scalar(select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == s.id)) or 0) + 1
    history = [{"role": m.role, "content": m.content} for m in db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == s.id, ChatMessage.role.in_(("user", "assistant")))
        .order_by(ChatMessage.seq.desc()).limit(12))][::-1]
    turn = ChatTurn(session_id=s.id, mode=mode, model=target.name)
    db.add(turn)
    db.flush()
    from ..models import StoredFile

    atts = []
    for fid in body.attachments[:5]:
        f = db.get(StoredFile, fid)
        if f is not None and f.owner_id == user.id and f.deleted_at is None:
            atts.append({"file_id": f.id, "name": f.filename, "mime": f.mime_type})
    um = ChatMessage(session_id=s.id, turn_id=turn.id, seq=seq, role="user", content=body.content, attachments=atts)
    db.add(um)
    if not s.title:
        s.title = body.content.strip().splitlines()[0][:60]
    s.last_message_at = utcnow()
    db.commit()
    user_msg = message_public(um, db, user)
    ids = dict(session_id=s.id, turn_id=turn.id, user_id=user.id, target_id=(target.provider.id, target.model.id),
               seq=seq + 1, mode=mode, kb_ids=body.kb_ids, attachments=[a["file_id"] for a in atts],
               question=body.content, history=history)
    factory = request.app.state.session_factory
    data_dir = request.app.state.settings.data_dir

    def stream() -> Iterator[str]:
        sdb: Session = factory()
        try:
            yield from _answer(sdb, data_dir, ids, user_msg)
        finally:
            sdb.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


def _answer(db: Session, data_dir, ids: dict, user_msg: dict) -> Iterator[str]:
    from ..models import AIModel, AIProvider

    user = db.get(User, ids["user_id"])
    s = db.get(ChatSession, ids["session_id"])
    turn = db.get(ChatTurn, ids["turn_id"])
    am = ChatMessage(session_id=s.id, turn_id=turn.id, seq=ids["seq"], role="assistant", content="")
    db.add(am)
    db.flush()
    yield _sse("meta", {"user_message": user_msg, "assistant_message_id": am.id, "turn_id": turn.id})
    try:
        yield _sse("status", {"text": "searching"})
        prep = tutor.prepare(db, data_dir, user, question=ids["question"], history=ids["history"], mode=ids["mode"],
                             session=s, kb_ids=ids["kb_ids"], attachments=ids["attachments"])
        if prep.fixed_answer:
            am.content = prep.fixed_answer
            yield _sse("delta", {"text": prep.fixed_answer})
        else:
            target = llm.Target(db.get(AIProvider, ids["target_id"][0]), db.get(AIModel, ids["target_id"][1]))
            yield _sse("status", {"text": "writing"})
            parts: list[str] = []
            temp = float(get_setting(db, "ai").get("temperature") or 0.4)
            for piece in llm.stream(db, user=user, messages=prep.messages, target=target, temperature=temp,
                                    chat_session_id=s.id, purpose=f"chat:{ids['mode']}"):
                parts.append(piece)
                yield _sse("delta", {"text": piece})
            am.content = "".join(parts)
        cites = tutor.finish(db, user, am, prep)
        am.grounding = prep.grounding
        turn.status, turn.finished_at = "done", utcnow()
        s.last_message_at = utcnow()
        db.commit()
        yield _sse("sources", {"grounding": prep.grounding, "citations": [tutor.citation_view(db, c, user) for c in cites]})
        yield _sse("done", {"message": message_public(am, db, user)})
        # Learn about the learner now and then (every 4th question).
        if (ids["seq"] // 2) % 4 == 0:
            recent = ids["history"] + [{"role": "user", "content": ids["question"]}, {"role": "assistant", "content": am.content}]
            tutor.extract_memories(db, user, s, recent)
            db.commit()
    except Exception as exc:  # noqa: BLE001 - reported to the client, recorded on the turn
        db.rollback()
        turn = db.get(ChatTurn, ids["turn_id"])
        msg = exc.detail if hasattr(exc, "detail") else str(exc)
        if isinstance(msg, dict):
            msg = msg.get("message") or str(msg)
        if turn is not None:
            turn.status, turn.error, turn.finished_at = "failed", str(msg)[:2000], utcnow()
        db.add(ChatMessage(session_id=ids["session_id"], turn_id=ids["turn_id"], seq=ids["seq"], role="assistant",
                           content=f"⚠️ {msg}"))
        db.commit()
        yield _sse("error", {"message": str(msg)})


class FeedbackBody(BaseModel):
    rating: int = Field(ge=-1, le=1)
    reason: str = Field(default="", max_length=20)
    comment: str = Field(default="", max_length=1000)


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: str, body: FeedbackBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = db.get(ChatMessage, message_id)
    if m is None or m.role != "assistant":
        raise HTTPException(status_code=404, detail="Message not found.")
    s = db.get(ChatSession, m.session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="Message not found.")
    if body.rating == 0:
        fb = db.get(AnswerFeedback, (m.id, user.id))
        if fb is not None:
            db.delete(fb)
        db.commit()
        return {"feedback": None}
    if body.reason and body.reason not in ("wrong", "outdated", "missing_source", "unclear", "other"):
        raise HTTPException(status_code=422, detail="Unknown reason.")
    fb = db.get(AnswerFeedback, (m.id, user.id))
    if fb is None:
        fb = AnswerFeedback(message_id=m.id, user_id=user.id, rating=body.rating)
        db.add(fb)
    fb.rating, fb.reason, fb.comment, fb.status = body.rating, body.reason, body.comment, "new"
    db.commit()
    return {"feedback": {"rating": fb.rating, "reason": fb.reason}}


# ── R&D: see how the AI would answer a learner ─────────────────────────────────


class TryBody(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    mode: str = "chat"


@rnd_router.post("/try")
def try_ai(body: TryBody, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_roles("rnd", "admin"))):
    """Answer as a learner would see it, with the retrieval details. Nothing is saved; no gap is logged."""
    data_dir = request.app.state.settings.data_dir
    # Answer in the R&D person's language, but cite as a learner would see the sources.
    learner_view = User(id=user.id, username=user.username, role="learner", locale=user.locale)
    prep = tutor.prepare(db, data_dir, learner_view, question=body.question, history=[], mode=body.mode,
                         record_gaps=False)
    if prep.fixed_answer:
        answer = prep.fixed_answer
    else:
        answer = llm.complete(db, user=user, messages=prep.messages, purpose="rnd_try")
    cited = [int(n) for n in dict.fromkeys(tutor.CITE.findall(answer))]
    by_n = {s.n: s for s in prep.sources}
    citations = []
    for n in cited:
        s = by_n.get(n)
        if s is None:
            continue
        c = ChatMessageCitation(message_id="preview", seq=n, chunk_id=s.chunk_id, node_id=s.node_id,
                                document_id=s.document_id, kb_document_id=s.kb_document_id, quote=s.text[:1500], page=s.page)
        citations.append({"learner_sees": tutor.citation_view(db, c, learner_view), "full": tutor.citation_view(db, c, user)})
    db.commit()  # usage metering
    return {"answer": answer, "grounding": prep.grounding, "citations": citations, "debug": prep.debug,
            "strict_refusal": prep.fixed_answer is not None}
