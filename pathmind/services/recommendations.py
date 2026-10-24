"""Educator -> R&D: recommend a branch (with its documents); R&D picks nodes to merge."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from pathmind.database.models import BranchDocument, BranchNode, BranchRecommendation, KnowledgeDomain, KnowledgeNode, KnowledgeSource
from pathmind.services import audit, rewards
from pathmind.services.knowledge_branch import service as branch
from pathmind.services.storage import object_store

MAX_PENDING_PER_USER = 10


class RecError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _view(r: BranchRecommendation, detail: bool = False) -> dict:
    d = {
        "id": r.id,
        "educator_id": r.educator_id,
        "title": r.title,
        "note": r.note,
        "status": r.status,
        "node_count": len(r.nodes or []),
        "document_count": len(r.documents or []),
        "review_note": r.review_note,
        "merged": r.merged or {},
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "reviewed_at": r.reviewed_at.isoformat() + "Z" if r.reviewed_at else None,
    }
    if detail:
        d["nodes"] = r.nodes or []
        d["documents"] = [{k: v for k, v in x.items() if k != "ref"} for x in (r.documents or [])]
    return d


def submit(db: Session, user_id: str, node_id: str, title: str = "", note: str = "") -> dict:
    """Snapshot the subtree rooted at ``node_id`` of the caller's branch."""
    pending = db.query(BranchRecommendation).filter_by(educator_id=user_id, status="pending").count()
    if pending >= MAX_PENDING_PER_USER:
        raise RecError(400, "You already have many recommendations waiting for review.")
    b = branch.get_or_create_branch(db, user_id)
    root = db.get(BranchNode, node_id)
    if root is None or root.branch_id != b.id:
        raise RecError(404, "Node not found.")
    ids = branch._descendants(db, node_id) | {node_id}
    rows = db.query(BranchNode).filter(BranchNode.id.in_(ids)).all()
    nodes = [
        {"id": n.id, "parent_id": n.parent_id if n.id != node_id else None, "title": n.title,
         "summary": n.summary, "source_doc": n.source_doc, "position": n.position}
        for n in rows
    ]
    docs: list[dict] = []
    names = {n.source_doc for n in rows if n.source_doc}
    for bd in db.query(BranchDocument).filter(BranchDocument.user_id == user_id).all():
        if bd.filename in names and bd.ref:  # KB-derived nodes keep no copy here
            docs.append({"filename": bd.filename, "size": bd.size, "sha256": bd.sha256, "ref": bd.ref})
    rec = BranchRecommendation(
        educator_id=user_id,
        title=(title or root.title).strip()[:200] or root.title,
        note=(note or "").strip()[:2000],
        nodes=nodes,
        documents=docs,
    )
    db.add(rec)
    db.flush()
    audit.record(db, user_id, "recommendation.submit", target_type="recommendation", target_id=rec.id,
                 data={"nodes": len(nodes), "documents": len(docs)}, commit=False)
    db.commit()
    return _view(rec)


def list_mine(db: Session, user_id: str) -> list[dict]:
    rows = db.query(BranchRecommendation).filter_by(educator_id=user_id).order_by(BranchRecommendation.created_at.desc()).all()
    return [_view(r) for r in rows]


def list_all(db: Session, status: str | None = None) -> list[dict]:
    q = db.query(BranchRecommendation)
    if status:
        q = q.filter_by(status=status)
    return [_view(r) for r in q.order_by(BranchRecommendation.created_at.desc()).limit(300).all()]


def _get(db: Session, rec_id: str) -> BranchRecommendation:
    r = db.get(BranchRecommendation, rec_id)
    if r is None:
        raise RecError(404, "Recommendation not found.")
    return r


def detail(db: Session, rec_id: str) -> dict:
    return _view(_get(db, rec_id), detail=True)


def read_document(db: Session, actor_id: str, rec_id: str, index: int) -> tuple[bytes, str]:
    r = _get(db, rec_id)
    docs = r.documents or []
    if index < 0 or index >= len(docs):
        raise RecError(404, "Document not found.")
    try:
        data = object_store.load(docs[index]["ref"], db=db)
    except object_store.StorageError as exc:
        raise RecError(exc.status, exc.message) from exc
    audit.record(db, actor_id, "recommendation.document.download", target_type="recommendation",
                 target_id=rec_id, data={"filename": docs[index]["filename"]})
    return data, docs[index]["filename"]


def merge(
    db: Session, actor_id: str, rec_id: str, *, domain_id: str, parent_id: str | None,
    node_ids: list[str], adopt_documents: bool = True, note: str = "",
) -> dict:
    """Copy the chosen snapshot nodes into the main graph under ``parent_id``.

    Chosen nodes keep their relative structure; a chosen node whose parent was not
    chosen hangs directly under ``parent_id``. The Educator's own branch is untouched.
    """
    from pathmind.services.knowledge_tree import service as tree
    from pathmind.services.knowledge_tree.store import store_original  # noqa: F401

    r = _get(db, rec_id)
    if r.status != "pending":
        raise RecError(409, "This recommendation was already reviewed.")
    if db.get(KnowledgeDomain, domain_id) is None:
        raise RecError(404, "Domain not found.")
    if parent_id:
        p = db.get(KnowledgeNode, parent_id)
        if p is None or p.domain_id != domain_id:
            raise RecError(400, "The target node is not in that domain.")
    snap = {n["id"]: n for n in (r.nodes or [])}
    chosen = [i for i in dict.fromkeys(node_ids) if i in snap]
    if not chosen:
        raise RecError(400, "Choose at least one node to merge.")
    from pathmind.services.knowledge_tree import history

    tree_before = history.snapshot(db, domain_id)
    new_id: dict[str, str] = {}
    order = sorted(chosen, key=lambda i: (snap[i].get("position") or 0))
    pending = list(order)
    for _ in range(64):
        if not pending:
            break
        later = []
        for i in pending:
            pid = snap[i].get("parent_id")
            if pid in chosen and pid not in new_id:
                later.append(i)
                continue
            n = tree._create_node(db, actor_id, domain_id, new_id.get(pid) if pid in new_id else parent_id,
                                  snap[i]["title"], snap[i].get("summary") or "")
            new_id[i] = n.id
        pending = later
    adopted = 0
    if adopt_documents:
        for d in r.documents or []:
            try:
                data = object_store.load(d["ref"], db=db)
            except object_store.StorageError:
                continue
            sha = hashlib.sha256(data).hexdigest()
            if db.query(KnowledgeSource.id).filter_by(domain_id=domain_id, sha256=sha).first():
                continue
            try:
                from pathmind.services.knowledge_tree import ingest

                text = ingest.extract_text(d["filename"], data)
            except Exception:  # noqa: BLE001 - skip unreadable files
                continue
            src = KnowledgeSource(domain_id=domain_id, filename=d["filename"][:255], size=len(data),
                                  sha256=sha, text=text, uploaded_by=actor_id)
            db.add(src)
            db.flush()
            store_original(src, d["filename"], data, db)
            adopted += 1
    rl = rewards.rules(db)
    points = rl["earn.recommendation_merged"] + rl["earn.per_node_merged"] * len(new_id)
    if points > 0:
        rewards.grant(db, r.educator_id, points, "recommendation.merged", ref=r.id,
                      note=r.title[:200], actor_id=actor_id, commit=False)
    r.status, r.reviewer_id, r.reviewed_at = "merged", actor_id, datetime.utcnow()
    r.review_note = (note or "").strip()[:2000]
    r.merged = {"domain_id": domain_id, "parent_id": parent_id, "node_ids": list(new_id.values()),
                "documents_adopted": adopted, "points": points}
    audit.record(db, actor_id, "recommendation.merge", target_type="recommendation", target_id=r.id,
                 data=r.merged, commit=False)
    db.commit()
    # Reverting this only undoes the tree side; the Educator keeps the points.
    history.record(db, actor_id, domain_id, "recommendation.merge", tree_before, history.snapshot(db, domain_id),
                   summary=f"Merged recommendation “{r.title[:200]}”")
    from pathmind.services import notifications

    notifications.notify_user(
        r.educator_id, "Your recommendation was merged",
        f"R&D merged “{r.title}” into the main knowledge graph. You earned {points} reward points.",
    )
    return _view(r, detail=True)


def reject(db: Session, actor_id: str, rec_id: str, note: str) -> dict:
    r = _get(db, rec_id)
    if r.status != "pending":
        raise RecError(409, "This recommendation was already reviewed.")
    r.status, r.reviewer_id, r.reviewed_at = "rejected", actor_id, datetime.utcnow()
    r.review_note = (note or "").strip()[:2000]
    audit.record(db, actor_id, "recommendation.reject", target_type="recommendation", target_id=r.id, commit=False)
    db.commit()
    from pathmind.services import notifications

    notifications.notify_user(
        r.educator_id, "Your recommendation was not merged",
        f"R&D reviewed “{r.title}” and did not merge it.\n\nNote: {r.review_note}",
    )
    return _view(r)
