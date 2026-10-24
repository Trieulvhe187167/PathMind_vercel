"""Learning space: courses, notebooks (with tags) and co-writer documents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import (
    Book, ChatMessage, ChatSession, Course, CourseKnowledgeBase, LearningTopic, Notebook, NotebookEntry,
    NotebookEntryTag, Question, Tag, User, WritingDocument, WritingRevision, utcnow,
)
from ..services import billing, llm, tutor

router = APIRouter()


def iso(d):
    return d.isoformat() + "Z" if d else None


def owned(db: Session, model: Any, obj_id: str, user: User, field: str = "owner_id"):
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, field) != user.id or getattr(obj, "deleted_at", None) is not None:
        raise HTTPException(status_code=404, detail="Not found.")
    return obj


def check_course(db: Session, user: User, course_id: str | None) -> None:
    if course_id:
        owned(db, Course, course_id, user)


# ── Courses ──────────────────────────────────────────────────────────────────


def course_public(c: Course, db: Session) -> dict:
    count = lambda m, col: db.scalar(select(func.count()).select_from(m).where(col == c.id)) or 0  # noqa: E731
    return {"id": c.id, "title": c.title, "description": c.description, "color": c.color,
            "archived": c.archived_at is not None, "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
            "kb_ids": list(db.scalars(select(CourseKnowledgeBase.knowledge_base_id).where(CourseKnowledgeBase.course_id == c.id))),
            "counts": {"chats": count(ChatSession, ChatSession.course_id), "notebooks": count(Notebook, Notebook.course_id),
                       "topics": count(LearningTopic, LearningTopic.course_id), "questions": count(Question, Question.course_id),
                       "books": count(Book, Book.course_id)}}


class CourseBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    color: str = Field(default="", max_length=16)
    archived: bool = False
    kb_ids: list[str] = []


@router.get("/courses")
def list_courses(db: Session = Depends(get_db), user: User = Depends(current_user)):
    ent = billing.entitlements(db, user)
    rows = db.scalars(select(Course).where(Course.owner_id == user.id).order_by(Course.archived_at.is_not(None), Course.title)).all()
    return {"courses": [course_public(c, db) for c in rows] if ent.get("courses") else [], "locked": not ent.get("courses")}


def _course_kbs(db: Session, user: User, c: Course, kb_ids: list[str]) -> None:
    db.query(CourseKnowledgeBase).filter(CourseKnowledgeBase.course_id == c.id).delete()
    for kb in tutor.accessible_kbs(db, user, kb_ids):
        db.add(CourseKnowledgeBase(course_id=c.id, knowledge_base_id=kb.id))


@router.post("/courses", status_code=201)
def create_course(body: CourseBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "courses")
    c = Course(owner_id=user.id, title=body.title.strip(), description=body.description, color=body.color)
    db.add(c)
    db.flush()
    _course_kbs(db, user, c, body.kb_ids)
    db.commit()
    return {"course": course_public(c, db)}


@router.get("/courses/{cid}")
def get_course(cid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "courses")
    c = owned(db, Course, cid, user)
    chats = db.scalars(select(ChatSession).where(ChatSession.course_id == c.id).order_by(ChatSession.last_message_at.desc()).limit(50))
    return {"course": course_public(c, db),
            "chats": [{"id": s.id, "title": s.title, "last_message_at": iso(s.last_message_at)} for s in chats],
            "notebooks": [{"id": n.id, "title": n.title} for n in db.scalars(select(Notebook).where(Notebook.course_id == c.id))],
            "topics": [{"id": t.id, "title": t.title, "status": t.status} for t in db.scalars(select(LearningTopic).where(LearningTopic.course_id == c.id))],
            "books": [{"id": b.id, "title": b.title, "status": b.status} for b in db.scalars(select(Book).where(Book.course_id == c.id))]}


@router.put("/courses/{cid}")
def update_course(cid: str, body: CourseBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "courses")
    c = owned(db, Course, cid, user)
    c.title, c.description, c.color = body.title.strip(), body.description, body.color
    c.archived_at = (c.archived_at or utcnow()) if body.archived else None
    _course_kbs(db, user, c, body.kb_ids)
    db.commit()
    return {"course": course_public(c, db)}


@router.delete("/courses/{cid}")
def delete_course(cid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = owned(db, Course, cid, user)
    db.delete(c)  # chats, notebooks... keep existing (course_id set to NULL)
    db.commit()
    return {"ok": True}


# ── Notebooks ────────────────────────────────────────────────────────────────


def notebook_public(n: Notebook, db: Session) -> dict:
    count = db.scalar(select(func.count()).select_from(NotebookEntry).where(NotebookEntry.notebook_id == n.id)) or 0
    return {"id": n.id, "title": n.title, "description": n.description, "course_id": n.course_id, "entries": count,
            "created_at": iso(n.created_at), "updated_at": iso(n.updated_at)}


def entry_public(e: NotebookEntry, db: Session) -> dict:
    tags = db.scalars(select(Tag).join(NotebookEntryTag, NotebookEntryTag.tag_id == Tag.id).where(NotebookEntryTag.entry_id == e.id))
    return {"id": e.id, "notebook_id": e.notebook_id, "kind": e.kind, "title": e.title, "content": e.content,
            "source_message_id": e.source_message_id, "extra": e.extra or {}, "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in tags],
            "created_at": iso(e.created_at), "updated_at": iso(e.updated_at)}


class NotebookBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    course_id: str | None = None


@router.get("/notebooks")
def list_notebooks(course_id: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    ent = billing.entitlements(db, user)
    if not ent.get("notebooks"):
        return {"notebooks": [], "locked": True}
    stmt = select(Notebook).where(Notebook.owner_id == user.id)
    if course_id:
        stmt = stmt.where(Notebook.course_id == course_id)
    return {"notebooks": [notebook_public(n, db) for n in db.scalars(stmt.order_by(Notebook.updated_at.desc()))], "locked": False}


@router.post("/notebooks", status_code=201)
def create_notebook(body: NotebookBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "notebooks")
    check_course(db, user, body.course_id)
    n = Notebook(owner_id=user.id, title=body.title.strip(), description=body.description, course_id=body.course_id)
    db.add(n)
    db.commit()
    return {"notebook": notebook_public(n, db)}


@router.get("/notebooks/{nid}")
def get_notebook(nid: str, tag: str = "", q: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "notebooks")
    n = owned(db, Notebook, nid, user)
    stmt = select(NotebookEntry).where(NotebookEntry.notebook_id == n.id)
    if tag:
        stmt = stmt.join(NotebookEntryTag, NotebookEntryTag.entry_id == NotebookEntry.id).where(NotebookEntryTag.tag_id == tag)
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(NotebookEntry.title).like(term) | func.lower(NotebookEntry.content).like(term))
    entries = db.scalars(stmt.order_by(NotebookEntry.updated_at.desc())).all()
    return {"notebook": notebook_public(n, db), "entries": [entry_public(e, db) for e in entries]}


@router.put("/notebooks/{nid}")
def update_notebook(nid: str, body: NotebookBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n = owned(db, Notebook, nid, user)
    check_course(db, user, body.course_id)
    n.title, n.description, n.course_id = body.title.strip(), body.description, body.course_id
    db.commit()
    return {"notebook": notebook_public(n, db)}


@router.delete("/notebooks/{nid}")
def delete_notebook(nid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned(db, Notebook, nid, user))
    db.commit()
    return {"ok": True}


class EntryBody(BaseModel):
    kind: str = "note"
    title: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=200000)
    source_message_id: str | None = None
    tag_ids: list[str] = []
    extra: dict = {}


def _set_tags(db: Session, user: User, e: NotebookEntry, tag_ids: list[str]) -> None:
    db.query(NotebookEntryTag).filter(NotebookEntryTag.entry_id == e.id).delete()
    for tid in dict.fromkeys(tag_ids):
        t = db.get(Tag, tid)
        if t is not None and t.owner_id == user.id:
            db.add(NotebookEntryTag(entry_id=e.id, tag_id=t.id))


@router.post("/notebooks/{nid}/entries", status_code=201)
def add_entry(nid: str, body: EntryBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "notebooks")
    n = owned(db, Notebook, nid, user)
    if body.kind not in ("note", "chat_excerpt", "quiz", "summary"):
        raise HTTPException(status_code=422, detail="Unknown entry kind.")
    content, title = body.content, body.title
    if body.source_message_id:
        m = db.get(ChatMessage, body.source_message_id)
        s = db.get(ChatSession, m.session_id) if m else None
        if m is None or s is None or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="Message not found.")
        content = content or m.content
        title = title or (s.title or "Chat excerpt")
    e = NotebookEntry(notebook_id=n.id, kind=body.kind, title=title.strip()[:300], content=content,
                      source_message_id=body.source_message_id, extra=body.extra)
    db.add(e)
    db.flush()
    _set_tags(db, user, e, body.tag_ids)
    n.updated_at = utcnow()
    db.commit()
    return {"entry": entry_public(e, db)}


@router.put("/notebooks/{nid}/entries/{eid}")
def update_entry(nid: str, eid: str, body: EntryBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n = owned(db, Notebook, nid, user)
    e = db.get(NotebookEntry, eid)
    if e is None or e.notebook_id != n.id:
        raise HTTPException(status_code=404, detail="Entry not found.")
    e.title, e.content, e.kind, e.extra = body.title.strip()[:300], body.content, body.kind, body.extra
    _set_tags(db, user, e, body.tag_ids)
    db.commit()
    return {"entry": entry_public(e, db)}


@router.delete("/notebooks/{nid}/entries/{eid}")
def delete_entry(nid: str, eid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n = owned(db, Notebook, nid, user)
    e = db.get(NotebookEntry, eid)
    if e is None or e.notebook_id != n.id:
        raise HTTPException(status_code=404, detail="Entry not found.")
    db.delete(e)
    db.commit()
    return {"ok": True}


class SummariseBody(BaseModel):
    entry_ids: list[str] = []


@router.post("/notebooks/{nid}/summarise")
def summarise(nid: str, body: SummariseBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """AI summary of a notebook (or chosen entries), saved as a new 'summary' entry."""
    billing.require_feature(db, user, "notebooks")
    n = owned(db, Notebook, nid, user)
    stmt = select(NotebookEntry).where(NotebookEntry.notebook_id == n.id)
    if body.entry_ids:
        stmt = stmt.where(NotebookEntry.id.in_(body.entry_ids))
    entries = db.scalars(stmt.order_by(NotebookEntry.created_at)).all()
    if not entries:
        raise HTTPException(status_code=422, detail="Nothing to summarise.")
    text = "\n\n".join(f"## {e.title}\n{e.content}" for e in entries)[:60000]
    summary = llm.complete(db, user=user, purpose="notebook_summary", messages=[
        {"role": "system", "content": f"Summarise study notes as clear revision notes in {tutor.LANG.get(user.locale, 'Vietnamese')}."},
        {"role": "user", "content": text}])
    e = NotebookEntry(notebook_id=n.id, kind="summary", title=f"Summary: {n.title}"[:300], content=summary)
    db.add(e)
    db.commit()
    return {"entry": entry_public(e, db)}


class TagBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="", max_length=16)


@router.get("/tags")
def list_tags(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"tags": [{"id": t.id, "name": t.name, "color": t.color} for t in db.scalars(select(Tag).where(Tag.owner_id == user.id).order_by(Tag.name))]}


@router.post("/tags", status_code=201)
def create_tag(body: TagBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.scalar(select(Tag.id).where(Tag.owner_id == user.id, Tag.name == body.name.strip())):
        raise HTTPException(status_code=409, detail="Tag exists.")
    t = Tag(owner_id=user.id, name=body.name.strip(), color=body.color)
    db.add(t)
    db.commit()
    return {"tag": {"id": t.id, "name": t.name, "color": t.color}}


@router.delete("/tags/{tid}")
def delete_tag(tid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned(db, Tag, tid, user))
    db.commit()
    return {"ok": True}


# ── Co-writer ────────────────────────────────────────────────────────────────


def writing_public(d: WritingDocument, full: bool = False) -> dict:
    out = {"id": d.id, "title": d.title, "course_id": d.course_id, "word_count": d.word_count,
           "created_at": iso(d.created_at), "updated_at": iso(d.updated_at)}
    if full:
        out["content"] = d.content
    return out


def _words(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


class WritingBody(BaseModel):
    title: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=500000)
    course_id: str | None = None
    snapshot: str | None = None  # save the previous content first: manual | before_ai_edit | autosave


@router.get("/writing")
def list_writing(db: Session = Depends(get_db), user: User = Depends(current_user)):
    ent = billing.entitlements(db, user)
    if not ent.get("cowriter"):
        return {"documents": [], "locked": True}
    rows = db.scalars(select(WritingDocument).where(WritingDocument.owner_id == user.id, WritingDocument.deleted_at.is_(None))
                      .order_by(WritingDocument.updated_at.desc())).all()
    return {"documents": [writing_public(d) for d in rows], "locked": False}


@router.post("/writing", status_code=201)
def create_writing(body: WritingBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "cowriter")
    check_course(db, user, body.course_id)
    d = WritingDocument(owner_id=user.id, title=body.title.strip() or "Untitled", content=body.content,
                        course_id=body.course_id, word_count=_words(body.content))
    db.add(d)
    db.commit()
    return {"document": writing_public(d, True)}


@router.get("/writing/{did}")
def get_writing(did: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = owned(db, WritingDocument, did, user)
    revs = db.scalars(select(WritingRevision).where(WritingRevision.document_id == d.id).order_by(WritingRevision.created_at.desc()).limit(50))
    return {"document": writing_public(d, True),
            "revisions": [{"id": r.id, "reason": r.reason, "created_at": iso(r.created_at), "words": _words(r.content)} for r in revs]}


@router.put("/writing/{did}")
def update_writing(did: str, body: WritingBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "cowriter")
    d = owned(db, WritingDocument, did, user)
    if body.snapshot in ("manual", "before_ai_edit", "autosave") and d.content != body.content:
        db.add(WritingRevision(document_id=d.id, content=d.content, reason=body.snapshot))
    d.title, d.content, d.word_count = body.title.strip() or d.title, body.content, _words(body.content)
    if body.course_id is not None:
        check_course(db, user, body.course_id or None)
        d.course_id = body.course_id or None
    db.commit()
    return {"document": writing_public(d, True)}


@router.delete("/writing/{did}")
def delete_writing(did: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = owned(db, WritingDocument, did, user)
    d.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/writing/{did}/revisions/{rid}")
def get_revision(did: str, rid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = owned(db, WritingDocument, did, user)
    r = db.get(WritingRevision, rid)
    if r is None or r.document_id != d.id:
        raise HTTPException(status_code=404, detail="Revision not found.")
    return {"revision": {"id": r.id, "reason": r.reason, "content": r.content, "created_at": iso(r.created_at)}}


ACTIONS = {
    "improve": "Improve the clarity and flow of the text without changing its meaning.",
    "shorten": "Make the text shorter and tighter, keeping every key point.",
    "expand": "Expand the text with more explanation and an example.",
    "fix": "Fix spelling, grammar and punctuation only.",
    "simplify": "Rewrite the text so a beginner understands it.",
    "continue": "Continue writing after the text in the same style; return only the new part.",
    "translate_en": "Translate the text to English.",
    "translate_vi": "Translate the text to Vietnamese.",
    "translate_zh": "Translate the text to Simplified Chinese.",
}


class AIEditBody(BaseModel):
    action: str = "improve"
    instruction: str = Field(default="", max_length=2000)
    text: str = Field(min_length=1, max_length=60000)  # the selection, or the whole document


@router.post("/writing/{did}/ai")
def ai_edit(did: str, body: AIEditBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Suggest a rewrite of the selected text. The client shows it; applying it saves a revision first."""
    billing.require_feature(db, user, "cowriter")
    d = owned(db, WritingDocument, did, user)
    task = ACTIONS.get(body.action)
    if task is None and not body.instruction:
        raise HTTPException(status_code=422, detail="Choose an action or write an instruction.")
    system = ("You are a writing assistant inside a study app. " + (task or "") + " " + body.instruction +
              " Keep markdown formatting. Return only the resulting text, no comments.")
    context = d.content[:4000] if body.text != d.content else ""
    msgs = [{"role": "system", "content": system.strip()}]
    if context:
        msgs.append({"role": "user", "content": f"(Context - the whole document, do not rewrite it)\n{context}"})
    msgs.append({"role": "user", "content": body.text})
    suggestion = llm.complete(db, user=user, messages=msgs, purpose="cowriter")
    db.commit()
    return {"suggestion": suggestion.strip()}
