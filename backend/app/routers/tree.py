"""The company knowledge tree. R&D edits (through change sets); admins may read and export.

Learners and educators never browse the tree; the AI uses it for their answers.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import get_db, require_roles
from ..models import (
    BulkImport, KnowledgeChunk, KnowledgeDomain, KnowledgeNode, KnowledgeNodeDocument, KnowledgeNodeVersion,
    StoredFile, TreeChange, TreeChangeSet, User, VerifiedDocument, utcnow,
)
from ..services import audit, extract, storage, tree_io
from ..services import knowledge_tree as kt
from .auth import _client_ip
from .files import read_upload

router = APIRouter()
reader = require_roles("rnd", "admin")
editor = require_roles("rnd")


def _domain(db: Session, domain_id: str) -> KnowledgeDomain:
    d = db.get(KnowledgeDomain, domain_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Domain not found.")
    return d


def _slug(text: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "domain"


# ── Domains ──────────────────────────────────────────────────────────────────


class DomainBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=5000)
    answer_policy: Literal["strict", "prefer"] = "prefer"
    citation_detail: Literal["title", "quote", "none"] = "title"
    status: Literal["active", "archived"] = "active"


@router.get("/domains")
def list_domains(db: Session = Depends(get_db), _: User = Depends(reader)):
    rows = db.scalars(select(KnowledgeDomain).order_by(KnowledgeDomain.status, KnowledgeDomain.name)).all()
    return {"domains": [kt.domain_public(d, db) for d in rows]}


@router.post("/domains", status_code=201)
def create_domain(body: DomainBody, request: Request, db: Session = Depends(get_db), user: User = Depends(editor)):
    slug = _slug(body.slug or body.name)
    base, n = slug, 1
    while db.scalar(select(KnowledgeDomain.id).where(KnowledgeDomain.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    d = KnowledgeDomain(slug=slug, name=body.name.strip(), description=body.description, answer_policy=body.answer_policy,
                        citation_detail=body.citation_detail, status=body.status, created_by=user.id)
    db.add(d)
    db.flush()
    audit.record(db, user.id, "tree.domain_create", target_type="domain", target_id=d.id, data={"name": d.name},
                 ip=_client_ip(request))
    db.commit()
    return {"domain": kt.domain_public(d, db)}


@router.put("/domains/{domain_id}")
def update_domain(domain_id: str, body: DomainBody, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(editor)):
    d = _domain(db, domain_id)
    d.name, d.description = body.name.strip(), body.description
    d.answer_policy, d.citation_detail, d.status = body.answer_policy, body.citation_detail, body.status
    audit.record(db, user.id, "tree.domain_update", target_type="domain", target_id=d.id,
                 data={"answer_policy": d.answer_policy, "citation_detail": d.citation_detail, "status": d.status},
                 ip=_client_ip(request))
    db.commit()
    return {"domain": kt.domain_public(d, db)}


@router.get("/domains/{domain_id}/tree")
def domain_tree(domain_id: str, archived: bool = False, db: Session = Depends(get_db), _: User = Depends(reader)):
    d = _domain(db, domain_id)
    return {"domain": kt.domain_public(d, db), "nodes": kt.tree_of(db, d.id, archived)}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(reader)):
    from ..models import AnswerFeedback, Contribution, KnowledgeGap

    now = utcnow()
    return {
        "contributions_waiting": db.scalar(select(func.count()).select_from(Contribution).where(Contribution.status == "submitted")) or 0,
        "contributions_in_review": db.scalar(select(func.count()).select_from(Contribution).where(Contribution.status == "in_review")) or 0,
        "drafts": db.scalar(select(func.count()).select_from(TreeChangeSet).where(TreeChangeSet.status == "draft")) or 0,
        "documents_pending": db.scalar(select(func.count()).select_from(VerifiedDocument).where(VerifiedDocument.status == "pending")) or 0,
        "documents_overdue": db.scalar(select(func.count()).select_from(VerifiedDocument).where(
            VerifiedDocument.status == "verified", VerifiedDocument.review_due_at <= now)) or 0,
        "gaps_open": db.scalar(select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.status == "open")) or 0,
        "feedback_new": db.scalar(select(func.count()).select_from(AnswerFeedback).where(
            AnswerFeedback.status == "new", AnswerFeedback.rating < 0)) or 0,
        "domains": db.scalar(select(func.count()).select_from(KnowledgeDomain).where(KnowledgeDomain.status == "active")) or 0,
        "nodes": db.scalar(select(func.count()).select_from(KnowledgeNode).where(KnowledgeNode.status == "published")) or 0,
        "verified_documents": db.scalar(select(func.count()).select_from(VerifiedDocument).where(VerifiedDocument.status == "verified")) or 0,
    }


# ── Nodes ────────────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}")
def get_node(node_id: str, db: Session = Depends(get_db), _: User = Depends(reader)):
    n = db.get(KnowledgeNode, node_id)
    if n is None:
        raise HTTPException(status_code=404, detail="Node not found.")
    docs = db.scalars(select(VerifiedDocument).join(KnowledgeNodeDocument, KnowledgeNodeDocument.document_id == VerifiedDocument.id)
                      .where(KnowledgeNodeDocument.node_id == n.id)).all()
    versions = db.scalars(select(KnowledgeNodeVersion).where(KnowledgeNodeVersion.node_id == n.id)
                          .order_by(KnowledgeNodeVersion.version.desc())).all()
    path, cur = [], n.parent_id
    while cur:
        p = db.get(KnowledgeNode, cur)
        if p is None:
            break
        path.insert(0, {"id": p.id, "title": p.title})
        cur = p.parent_id
    out_versions = []
    for v in versions:
        u = db.get(User, v.changed_by) if v.changed_by else None
        out_versions.append({"version": v.version, "title": v.title, "summary": v.summary, "body": v.body,
                             "change_note": v.change_note, "change_set_id": v.change_set_id,
                             "changed_by": u.username if u else None, "created_at": kt.iso(v.created_at)})
    return {"node": kt.node_public(n), "path": path, "documents": [kt.document_public(d) for d in docs],
            "versions": out_versions}


class RestoreBody(BaseModel):
    version: int


@router.post("/nodes/{node_id}/restore", status_code=201)
def restore_node(node_id: str, body: RestoreBody, db: Session = Depends(get_db), user: User = Depends(editor)):
    """Make a draft that brings back an older version of the node (published like any draft)."""
    n = db.get(KnowledgeNode, node_id)
    v = db.scalar(select(KnowledgeNodeVersion).where(KnowledgeNodeVersion.node_id == node_id,
                                                     KnowledgeNodeVersion.version == body.version))
    if n is None or v is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    cs = TreeChangeSet(domain_id=n.domain_id, title=f"Restore '{v.title}' to version {v.version}", origin="restore",
                       created_by=user.id)
    db.add(cs)
    db.flush()
    kt.add_change(db, cs, "update_node", node_id=n.id, payload={"title": v.title, "summary": v.summary, "body": v.body,
                                                                "unarchive": True, "note": f"restored version {v.version}"})
    db.commit()
    return {"change_set": kt.change_set_public(cs, db, True)}


# ── Change sets (drafts) ──────────────────────────────────────────────────────


class ChangeSetBody(BaseModel):
    domain_id: str
    title: str = Field(min_length=1, max_length=200)


@router.get("/change-sets")
def list_change_sets(status: str = "draft", domain_id: str = "", db: Session = Depends(get_db), _: User = Depends(reader)):
    stmt = select(TreeChangeSet).order_by(TreeChangeSet.updated_at.desc()).limit(200)
    if status != "all":
        stmt = stmt.where(TreeChangeSet.status == status)
    if domain_id:
        stmt = stmt.where(TreeChangeSet.domain_id == domain_id)
    return {"change_sets": [kt.change_set_public(cs, db) for cs in db.scalars(stmt)]}


@router.post("/change-sets", status_code=201)
def create_change_set(body: ChangeSetBody, db: Session = Depends(get_db), user: User = Depends(editor)):
    _domain(db, body.domain_id)
    cs = TreeChangeSet(domain_id=body.domain_id, title=body.title.strip(), created_by=user.id)
    db.add(cs)
    db.commit()
    return {"change_set": kt.change_set_public(cs, db, True)}


def _cs(db: Session, cs_id: str, draft: bool = False) -> TreeChangeSet:
    cs = db.get(TreeChangeSet, cs_id)
    if cs is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft and cs.status != "draft":
        raise HTTPException(status_code=409, detail="This change set is no longer a draft.")
    return cs


@router.get("/change-sets/{cs_id}")
def get_change_set(cs_id: str, db: Session = Depends(get_db), _: User = Depends(reader)):
    cs = _cs(db, cs_id)
    nodes = {n.id: n.title for n in db.scalars(select(KnowledgeNode).where(KnowledgeNode.domain_id == cs.domain_id))}
    docs = {d.id: d.title for d in db.scalars(select(VerifiedDocument).where(VerifiedDocument.domain_id == cs.domain_id))}
    return {"change_set": kt.change_set_public(cs, db, True), "node_titles": nodes, "document_titles": docs,
            "domain": kt.domain_public(_domain(db, cs.domain_id))}


class ChangeBody(BaseModel):
    op: str
    node_id: str | None = None
    temp_key: str = Field(default="", max_length=40)
    payload: dict = {}


@router.post("/change-sets/{cs_id}/changes", status_code=201)
def add_change(cs_id: str, body: ChangeBody, db: Session = Depends(get_db), _: User = Depends(editor)):
    cs = _cs(db, cs_id, draft=True)
    temp = body.temp_key
    if body.op == "add_node" and not temp:
        temp = f"m{(db.scalar(select(func.count()).select_from(TreeChange).where(TreeChange.change_set_id == cs.id)) or 0) + 1}"
    ch = kt.add_change(db, cs, body.op, node_id=body.node_id, temp_key=temp, payload=body.payload)
    db.commit()
    return {"change": kt.change_public(ch)}


class ChangePatch(BaseModel):
    payload: dict | None = None
    accepted: bool | None = None


@router.patch("/change-sets/{cs_id}/changes/{change_id}")
def patch_change(cs_id: str, change_id: str, body: ChangePatch, db: Session = Depends(get_db), _: User = Depends(editor)):
    cs = _cs(db, cs_id, draft=True)
    ch = db.get(TreeChange, change_id)
    if ch is None or ch.change_set_id != cs.id:
        raise HTTPException(status_code=404, detail="Change not found.")
    if body.payload is not None:
        ch.payload = {**(ch.payload or {}), **body.payload}
    if body.accepted is not None:
        ch.accepted = body.accepted
    cs.updated_at = utcnow()
    db.commit()
    return {"change": kt.change_public(ch)}


@router.delete("/change-sets/{cs_id}/changes/{change_id}")
def delete_change(cs_id: str, change_id: str, db: Session = Depends(get_db), _: User = Depends(editor)):
    cs = _cs(db, cs_id, draft=True)
    ch = db.get(TreeChange, change_id)
    if ch is None or ch.change_set_id != cs.id:
        raise HTTPException(status_code=404, detail="Change not found.")
    db.delete(ch)
    db.commit()
    return {"ok": True}


@router.post("/change-sets/{cs_id}/publish")
def publish(cs_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(editor)):
    cs = _cs(db, cs_id, draft=True)
    try:
        counts = kt.publish(db, cs, user, request.app.state.settings.data_dir)
    except HTTPException:
        db.rollback()
        raise
    audit.record(db, user.id, "tree.publish", target_type="change_set", target_id=cs.id,
                 data={"title": cs.title, **counts}, ip=_client_ip(request))
    db.commit()
    return {"change_set": kt.change_set_public(cs, db, True), "counts": counts}


@router.post("/change-sets/{cs_id}/discard")
def discard(cs_id: str, db: Session = Depends(get_db), _: User = Depends(editor)):
    cs = _cs(db, cs_id, draft=True)
    cs.status = "discarded"
    db.commit()
    return {"change_set": kt.change_set_public(cs, db)}


# ── Verified documents ────────────────────────────────────────────────────────


def _extract_text(filename: str, data: bytes, mime: str) -> tuple[str, int]:
    try:
        ex = extract.extract(filename, data, mime)
    except extract.ExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if ex.pages:
        return "\f".join(ex.pages), len(ex.pages)
    return ex.text, 0


@router.get("/documents")
def list_documents(domain_id: str = "", status: str = "", q: str = "", db: Session = Depends(get_db),
                   _: User = Depends(reader)):
    stmt = select(VerifiedDocument).order_by(VerifiedDocument.created_at.desc()).limit(500)
    if domain_id:
        stmt = stmt.where(VerifiedDocument.domain_id == domain_id)
    if status == "overdue":
        stmt = stmt.where(VerifiedDocument.status == "verified", VerifiedDocument.review_due_at <= utcnow())
    elif status:
        stmt = stmt.where(VerifiedDocument.status == status)
    if q.strip():
        stmt = stmt.where(func.lower(VerifiedDocument.title).contains(q.strip().lower()))
    return {"documents": [kt.document_public(d, db) for d in db.scalars(stmt)]}


@router.post("/documents", status_code=201)
def upload_document(request: Request, file: UploadFile = File(...), domain_id: str = Form(...), title: str = Form(""),
                    doc_version: str = Form(""), supersedes_id: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(editor)):
    _domain(db, domain_id)
    data = read_upload(file)
    text, pages = _extract_text(file.filename or "", data, file.content_type or "")
    if supersedes_id:
        old = db.get(VerifiedDocument, supersedes_id)
        if old is None or old.domain_id != domain_id:
            raise HTTPException(status_code=422, detail="The document it replaces is not in this domain.")
    f = storage.save_bytes(db, request.app.state.settings.data_dir, owner_id=user.id, filename=file.filename or "document",
                           data=data, mime_type=file.content_type or "", purpose="verified")
    doc = VerifiedDocument(domain_id=domain_id, file_id=f.id, title=(title or file.filename or "Document").strip()[:300],
                           doc_version=doc_version.strip()[:40], status="pending", text=text, page_count=pages,
                           supersedes_id=supersedes_id or None)
    db.add(doc)
    db.commit()
    return {"document": kt.document_public(doc, db)}


def _doc(db: Session, doc_id: str) -> VerifiedDocument:
    d = db.get(VerifiedDocument, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return d


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, db: Session = Depends(get_db), _: User = Depends(reader)):
    d = _doc(db, doc_id)
    chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == d.id).order_by(KnowledgeChunk.seq).limit(300)).all()
    return {"document": kt.document_public(d, db), "text": (d.text or "").replace("\f", "\n\n")[:200_000],
            "chunks": [{"id": c.id, "seq": c.seq, "heading_path": c.heading_path, "text": c.text[:600], "node_id": c.node_id,
                        "page_from": c.page_from, "page_to": c.page_to, "is_active": c.is_active,
                        "embedded": bool(c.embedding_model)} for c in chunks]}


class VerifyBody(BaseModel):
    review_days: int | None = Field(default=None, ge=0, le=3650)


@router.post("/documents/{doc_id}/verify")
def verify(doc_id: str, body: VerifyBody, request: Request, db: Session = Depends(get_db), user: User = Depends(editor)):
    d = _doc(db, doc_id)
    if d.status not in ("pending", "retired", "verified"):
        raise HTTPException(status_code=409, detail="A superseded document cannot be verified again.")
    kt.verify_document(db, d, user, review_days=body.review_days)
    n = kt.index_document(db, d, request.app.state.settings.data_dir)
    audit.record(db, user.id, "tree.document_verify", target_type="verified_document", target_id=d.id,
                 data={"title": d.title, "chunks": n}, ip=_client_ip(request))
    db.commit()
    return {"document": kt.document_public(d, db)}


class RetireBody(BaseModel):
    note: str = Field(default="", max_length=500)


@router.post("/documents/{doc_id}/retire")
def retire(doc_id: str, body: RetireBody, request: Request, db: Session = Depends(get_db), user: User = Depends(editor)):
    d = _doc(db, doc_id)
    kt.retire_document(db, d, actor_id=user.id, note=body.note)
    audit.record(db, user.id, "tree.document_retire", target_type="verified_document", target_id=d.id,
                 data={"title": d.title, "note": body.note}, ip=_client_ip(request))
    db.commit()
    return {"document": kt.document_public(d, db)}


@router.post("/documents/{doc_id}/reindex")
def reindex(doc_id: str, request: Request, db: Session = Depends(get_db), _: User = Depends(editor)):
    d = _doc(db, doc_id)
    n = kt.index_document(db, d, request.app.state.settings.data_dir)
    db.commit()
    return {"chunks": n, "document": kt.document_public(d, db)}


class ProposeBody(BaseModel):
    parent_node_id: str | None = None
    use_ai: bool = False


@router.post("/documents/{doc_id}/propose", status_code=201)
def propose(doc_id: str, body: ProposeBody, db: Session = Depends(get_db), user: User = Depends(editor)):
    d = _doc(db, doc_id)
    cs = kt.propose_from_document(db, d, user, parent_id=body.parent_node_id, use_ai=body.use_ai)
    db.commit()
    return {"change_set": kt.change_set_public(cs, db, True)}


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: str, request: Request, db: Session = Depends(get_db), _: User = Depends(reader)):
    d = _doc(db, doc_id)
    f = db.get(StoredFile, d.file_id) if d.file_id else None
    if f is None or f.deleted_at is not None:
        return Response((d.text or "").replace("\f", "\n\n").encode("utf-8"), media_type="text/plain; charset=utf-8")
    data = storage.read_bytes(db, request.app.state.settings.data_dir, f)
    from urllib.parse import quote

    return Response(data, media_type=f.mime_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.filename)}"})


# ── Import / export ───────────────────────────────────────────────────────────


@router.post("/import", status_code=201)
def import_tree(request: Request, file: UploadFile = File(...), domain_id: str = Form(""), parent_node_id: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(editor)):
    data = read_upload(file, 20 * 1024 * 1024)
    try:
        parsed = tree_io.parse(file.filename or "", data)
    except tree_io.TreeFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    f = storage.save_bytes(db, request.app.state.settings.data_dir, owner_id=user.id, filename=file.filename or "tree",
                           data=data, mime_type=file.content_type or "", purpose="tree_import")
    imp = BulkImport(kind="knowledge_tree", file_id=f.id, filename=(file.filename or "tree")[:255], created_by=user.id,
                     options={"domain_id": domain_id, "parent_node_id": parent_node_id}, status="applied")
    db.add(imp)
    db.flush()
    sets = []
    for dom in parsed:
        if domain_id:
            target = _domain(db, domain_id)
        else:
            name = dom.get("name") or (file.filename or "Imported").rsplit(".", 1)[0]
            target = db.scalar(select(KnowledgeDomain).where(func.lower(KnowledgeDomain.name) == name.lower()))
            if target is None:
                slug = _slug(dom.get("slug") or name)
                base, k = slug, 1
                while db.scalar(select(KnowledgeDomain.id).where(KnowledgeDomain.slug == slug)):
                    k += 1
                    slug = f"{base}-{k}"
                target = KnowledgeDomain(slug=slug, name=name[:120], description=dom.get("description") or "",
                                         answer_policy=dom.get("answer_policy") if dom.get("answer_policy") in ("strict", "prefer") else "prefer",
                                         citation_detail=dom.get("citation_detail") if dom.get("citation_detail") in ("title", "quote", "none") else "title",
                                         created_by=user.id)
                db.add(target)
                db.flush()
        cs = tree_io.to_change_set(db, target, dom["nodes"], user, f"Import: {file.filename}", imp.id,
                                   parent_id=parent_node_id or None)
        sets.append(cs)
    nodes = sum(kt.change_set_public(cs, db)["change_count"] for cs in sets)
    imp.total_rows = imp.ok_rows = nodes
    imp.summary = {"change_sets": [cs.id for cs in sets], "nodes": nodes}
    audit.record(db, user.id, "tree.import", target_type="import", target_id=imp.id,
                 data={"file": imp.filename, "nodes": nodes}, ip=_client_ip(request))
    db.commit()
    return {"change_sets": [kt.change_set_public(cs, db) for cs in sets], "nodes": nodes}


@router.get("/export")
def export_tree(request: Request, format: Literal["json", "xlsx", "md", "zip"] = "json", domain_id: str = "",
                db: Session = Depends(get_db), user: User = Depends(reader)):
    domains = [_domain(db, domain_id)] if domain_id else list(
        db.scalars(select(KnowledgeDomain).where(KnowledgeDomain.status == "active").order_by(KnowledgeDomain.name)))
    name = domains[0].slug if len(domains) == 1 else "knowledge-tree"
    if format == "json":
        data, mime = tree_io.export_json(db, domains), "application/json"
    elif format == "xlsx":
        data, mime = tree_io.export_xlsx(db, domains), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "md":
        data, mime = tree_io.export_markdown(db, domains), "text/markdown; charset=utf-8"
    else:
        data, mime = tree_io.export_zip(db, domains, request.app.state.settings.data_dir), "application/zip"
    audit.record(db, user.id, "tree.export", target_type="domain", target_id=domain_id or "all",
                 data={"format": format, "domains": len(domains)}, ip=_client_ip(request))
    db.commit()
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'})
