"""Personal knowledge bases (Knowledge Center): upload documents, index them, search, share.

Different from the company knowledge tree: each person curates their own, and nobody verifies it.
The index (passages + optional vectors) is kept in ``<data_dir>/kb/<id>/index.json``.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import (
    BackgroundTask, KnowledgeBase, KnowledgeBaseAccess, KnowledgeBaseDocument, StoredFile, User, utcnow,
)
from ..services import billing, extract, llm, retrieval, storage, tasks
from .files import read_upload

router = APIRouter()
WEB_TRANSPORT: httpx.BaseTransport | None = None  # tests


def iso(d):
    return d.isoformat() + "Z" if d else None


def kb_public(kb: KnowledgeBase, db: Session, user: User) -> dict:
    owner = db.get(User, kb.owner_id)
    return {"id": kb.id, "name": kb.name, "description": kb.description, "status": kb.status, "visibility": kb.visibility,
            "document_count": kb.document_count, "size_bytes": kb.size_bytes, "last_indexed_at": iso(kb.last_indexed_at),
            "owner": owner.username if owner else None, "mine": kb.owner_id == user.id, "created_at": iso(kb.created_at)}


def doc_public(d: KnowledgeBaseDocument) -> dict:
    return {"id": d.id, "title": d.title, "url": d.url, "folder": d.folder, "status": d.status, "error": d.error,
            "file_id": d.file_id, "indexed_at": iso(d.indexed_at), "created_at": iso(d.created_at)}


def _readable(db: Session, user: User, kb_id: str) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or not (kb.owner_id == user.id or kb.visibility == "public" or user.role == "admin"
                          or db.get(KnowledgeBaseAccess, (kb.id, user.id))):
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return kb


def _mine(db: Session, user: User, kb_id: str) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return kb


@router.get("")
def list_kbs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    shared = select(KnowledgeBaseAccess.knowledge_base_id).where(KnowledgeBaseAccess.user_id == user.id)
    rows = db.scalars(select(KnowledgeBase).where(or_(KnowledgeBase.owner_id == user.id, KnowledgeBase.visibility == "public",
                                                      KnowledgeBase.id.in_(shared))).order_by(KnowledgeBase.name)).all()
    return {"knowledge_bases": [kb_public(k, db, user) for k in rows]}


class KBBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)


@router.post("", status_code=201)
def create_kb(body: KBBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    billing.require_feature(db, user, "kb")
    n = db.scalar(select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)) or 0
    billing.check_max(db, user, "max_kbs", n)
    if db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.owner_id == user.id, KnowledgeBase.name == body.name.strip())):
        raise HTTPException(status_code=409, detail="You already have a knowledge base with this name.")
    emb = llm.embedding_target(db)
    kb = KnowledgeBase(owner_id=user.id, name=body.name.strip(), description=body.description,
                       embedding_model=emb.name if emb else "", engine="pathmind")
    db.add(kb)
    db.commit()
    return {"knowledge_base": kb_public(kb, db, user)}


@router.get("/{kb_id}")
def get_kb(kb_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _readable(db, user, kb_id)
    docs = db.scalars(select(KnowledgeBaseDocument).where(KnowledgeBaseDocument.knowledge_base_id == kb.id)
                      .order_by(KnowledgeBaseDocument.folder, KnowledgeBaseDocument.title)).all()
    access = []
    if kb.owner_id == user.id:
        for a in db.scalars(select(KnowledgeBaseAccess).where(KnowledgeBaseAccess.knowledge_base_id == kb.id)):
            u = db.get(User, a.user_id)
            access.append({"user_id": a.user_id, "username": u.username if u else None})
    task = db.scalar(select(BackgroundTask).where(BackgroundTask.kind == "kb_index",
                                                  BackgroundTask.payload["kb_id"].as_string() == kb.id)
                     .order_by(BackgroundTask.created_at.desc()).limit(1))
    return {"knowledge_base": kb_public(kb, db, user), "documents": [doc_public(d) for d in docs], "access": access,
            "task": tasks.public(task) if task else None}


@router.put("/{kb_id}")
def update_kb(kb_id: str, body: KBBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    kb.name, kb.description = body.name.strip(), body.description
    db.commit()
    return {"knowledge_base": kb_public(kb, db, user)}


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    data_dir = request.app.state.settings.data_dir
    for d in db.scalars(select(KnowledgeBaseDocument).where(KnowledgeBaseDocument.knowledge_base_id == kb.id)):
        f = db.get(StoredFile, d.file_id) if d.file_id else None
        if f is not None:
            storage.delete_file(db, data_dir, f)
    path = retrieval.kb_index_path(data_dir, kb.id)
    if path.exists():
        path.unlink()
    db.delete(kb)
    db.commit()
    return {"ok": True}


# ── Documents ────────────────────────────────────────────────────────────────


def _queue_index(request: Request, db: Session, kb: KnowledgeBase, user: User) -> None:
    """Commit first (the task reads the documents in its own session), then index in the background."""
    kb.status = "indexing"
    db.commit()
    data_dir = request.app.state.settings.data_dir
    kb_id = kb.id

    def run(db: Session, task: BackgroundTask, report) -> dict:
        return index_kb(db, data_dir, kb_id, report)

    request.app.state.tasks.submit("kb_index", user.id, {"kb_id": kb.id}, run)


def index_kb(db: Session, data_dir, kb_id: str, report=lambda p: None) -> dict:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return {}
    owner = db.get(User, kb.owner_id)
    docs = db.scalars(select(KnowledgeBaseDocument).where(KnowledgeBaseDocument.knowledge_base_id == kb.id)).all()
    items: list[dict] = []
    total = len(docs) or 1
    for i, d in enumerate(docs):
        try:
            if d.file_id:
                f = db.get(StoredFile, d.file_id)
                ex = extract.extract(f.filename, storage.read_bytes(db, data_dir, f), f.mime_type)
            else:
                ex = _fetch_web(d.url)
            passages = extract.split_passages(ex, max_chars=1000)
            for p in passages:
                items.append({"doc_id": d.id, "seq": p.seq, "title": d.title, "heading": p.heading_path,
                              "page": p.page_from, "text": (f"{p.heading_path}\n{p.text}" if p.heading_path else p.text)[:6000]})
            d.status, d.error, d.indexed_at = "indexed", "", utcnow()
        except Exception as exc:  # noqa: BLE001 - shown per document
            d.status, d.error = "failed", str(exc)[:1000]
        report((i + 1) / total * 0.7)
    if items:
        try:
            res = llm.embed(db, [it["text"] for it in items], user=owner, purpose="indexing")
        except (llm.LLMError, HTTPException):
            res = None
        if res:
            for it, v in zip(items, res[0]):
                it["vector"] = v
            kb.embedding_model = res[1]
    retrieval.save_kb_index(data_dir, kb.id, items)
    kb.document_count = len(docs)
    kb.status = "ready" if any(d.status == "indexed" for d in docs) else ("error" if docs else "empty")
    kb.last_indexed_at = utcnow()
    db.commit()
    return {"passages": len(items), "documents": len(docs)}


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=422, detail="Enter a full web address starting with https://")
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="That address cannot be reached.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(status_code=422, detail="Addresses inside a private network are not allowed.")


def _fetch_web(url: str) -> extract.Extracted:
    with httpx.Client(timeout=30, follow_redirects=False, transport=WEB_TRANSPORT,
                      headers={"User-Agent": "PathMind/0.2 (+knowledge base)"}) as c:
        for _ in range(5):  # follow redirects ourselves so every hop is checked
            if WEB_TRANSPORT is None:
                _check_url(url)
            r = c.get(url)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = str(httpx.URL(url).join(r.headers["location"]))
                continue
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "html" in ctype:
                return extract.Extracted(extract.html_to_text(r.text[:3_000_000]))
            if ctype.startswith("text/"):
                return extract.Extracted(r.text[:3_000_000])
            if "pdf" in ctype:
                pages = extract.pdf_pages(r.content)
                return extract.Extracted("\n\n".join(pages), pages)
            raise extract.ExtractError(f"Unsupported content type: {ctype}")
    raise extract.ExtractError("Too many redirects.")


@router.post("/{kb_id}/documents", status_code=201)
def upload_docs(kb_id: str, request: Request, files: list[UploadFile] = File(...), folder: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    data_dir = request.app.state.settings.data_dir
    added = []
    for file in files[:20]:
        data = read_upload(file)
        billing.check_upload(db, user, len(data))
        try:
            extract.extract(file.filename or "", data, file.content_type or "")
        except extract.ExtractError as exc:
            raise HTTPException(status_code=422, detail=f"{file.filename}: {exc}") from exc
        f = storage.save_bytes(db, data_dir, owner_id=user.id, filename=file.filename or "file", data=data,
                               mime_type=file.content_type or "", purpose="kb")
        d = KnowledgeBaseDocument(knowledge_base_id=kb.id, file_id=f.id, title=(file.filename or "file")[:300],
                                  folder=folder.strip()[:300])
        db.add(d)
        kb.size_bytes = (kb.size_bytes or 0) + len(data)
        added.append(d)
    db.flush()
    _queue_index(request, db, kb, user)
    db.refresh(kb)
    return {"documents": [doc_public(d) for d in added]}


class UrlBody(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    title: str = Field(default="", max_length=300)


@router.post("/{kb_id}/urls", status_code=201)
def add_url(kb_id: str, body: UrlBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    if WEB_TRANSPORT is None:
        _check_url(body.url.strip())
    d = KnowledgeBaseDocument(knowledge_base_id=kb.id, url=body.url.strip(), title=(body.title or body.url).strip()[:300])
    db.add(d)
    db.flush()
    _queue_index(request, db, kb, user)
    db.refresh(kb)
    return {"document": doc_public(d)}


@router.delete("/{kb_id}/documents/{doc_id}")
def delete_doc(kb_id: str, doc_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    d = db.get(KnowledgeBaseDocument, doc_id)
    if d is None or d.knowledge_base_id != kb.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    if d.file_id:
        f = db.get(StoredFile, d.file_id)
        if f is not None:
            kb.size_bytes = max(0, (kb.size_bytes or 0) - f.size_bytes)
            storage.delete_file(db, request.app.state.settings.data_dir, f)
    db.delete(d)
    db.flush()
    _queue_index(request, db, kb, user)
    db.refresh(kb)
    return {"ok": True}


@router.post("/{kb_id}/reindex")
def reindex(kb_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    _queue_index(request, db, kb, user)
    db.refresh(kb)
    return {"knowledge_base": kb_public(kb, db, user)}


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


@router.post("/{kb_id}/search")
def search(kb_id: str, body: SearchBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _readable(db, user, kb_id)
    hits = retrieval.search_kbs(db, request.app.state.settings.data_dir, body.query, [kb], user=user, limit=body.limit)
    db.commit()
    return {"results": [{"title": h.doc.meta["item"].get("title"), "heading": h.doc.meta["item"].get("heading"),
                         "page": h.doc.meta["item"].get("page"), "text": h.doc.text[:800], "score": round(h.score, 3),
                         "document_id": h.doc.meta["item"]["doc_id"]} for h in hits]}


class ShareBody(BaseModel):
    login: str = Field(min_length=1, max_length=255)  # username or email


@router.post("/{kb_id}/access")
def share(kb_id: str, body: ShareBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    typed = body.login.strip().lower()
    other = db.scalar(select(User).where(or_(func.lower(User.username) == typed, User.email == typed), User.is_active.is_(True)))
    if other is None or other.id == user.id:
        raise HTTPException(status_code=404, detail="No such person.")
    if db.get(KnowledgeBaseAccess, (kb.id, other.id)) is None:
        db.add(KnowledgeBaseAccess(knowledge_base_id=kb.id, user_id=other.id, granted_by=user.id))
    if kb.visibility == "private":
        kb.visibility = "shared"
    db.commit()
    return get_kb(kb_id, db, user)


@router.delete("/{kb_id}/access/{user_id}")
def unshare(kb_id: str, user_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    kb = _mine(db, user, kb_id)
    a = db.get(KnowledgeBaseAccess, (kb.id, user_id))
    if a is not None:
        db.delete(a)
    db.flush()
    if kb.visibility == "shared" and not db.scalar(select(KnowledgeBaseAccess.user_id).where(KnowledgeBaseAccess.knowledge_base_id == kb.id).limit(1)):
        kb.visibility = "private"
    db.commit()
    return {"ok": True}


class VisibilityBody(BaseModel):
    public: bool


@router.put("/{kb_id}/publish")
def publish(kb_id: str, body: VisibilityBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Admins can publish a knowledge base to everyone."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrators only.")
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    has_access = db.scalar(select(KnowledgeBaseAccess.user_id).where(KnowledgeBaseAccess.knowledge_base_id == kb.id).limit(1))
    kb.visibility = "public" if body.public else ("shared" if has_access else "private")
    db.commit()
    return {"knowledge_base": kb_public(kb, db, user)}
