"""The hidden personal overlay: enrichment from documents, retrieval, reset.

Enrichment is deterministic (document headings -> overlay nodes, anchored to the
best-matching main-tree node), so it costs no AI credits. The main tree always
wins: overlay hits whose title repeats a main-tree hit are dropped at retrieval.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import re

from sqlalchemy.orm import Session

from pathmind.database.models import BranchNode
from pathmind.services import audit
from pathmind.services.knowledge_tree import ingest
from pathmind.services.knowledge_tree import service as tree

from .service import (
    BranchError,
    MAX_NODES,
    _node_dict,
    create_node,
    get_or_create_branch,
    save_version,
    _touch,
)

MAX_NEW_NODES = 120
MAX_AUTO_BYTES = 25 * 1024 * 1024  # larger files are skipped by the automatic enrichment

logger = logging.getLogger(__name__)


def _keep_original(db: Session, user_id: str, filename: str, data: bytes) -> int:
    """Keep the file so an Educator can later offer it to R&D with a recommendation.

    Returns the change in stored bytes (the caller charges it to the storage quota).
    """
    from pathmind.database.models import BranchDocument
    from pathmind.services.storage import object_store

    sha = hashlib.sha256(data).hexdigest()
    delta = 0
    old = db.query(BranchDocument).filter_by(user_id=user_id, filename=filename[:255]).first()
    if old is not None:
        if old.sha256 == sha:
            return 0
        if old.ref:
            object_store.remove(old.ref)
            delta -= int(old.size or 0)
        db.delete(old)
    owner = re.sub(r"[^\w-]+", "_", user_id)[:64] or "user"
    ref = object_store.save(f"branch-documents/{owner}", filename, data, db=db)
    db.add(BranchDocument(user_id=user_id, filename=filename[:255], ref=ref, size=len(data), sha256=sha))
    db.commit()
    return delta + len(data)


def _already_processed(db: Session, user_id: str, sha: str) -> bool:
    from pathmind.database.models import BranchDocument

    return db.query(BranchDocument.id).filter_by(user_id=user_id, sha256=sha).first() is not None


def _remember_processed(db: Session, user_id: str, filename: str, data: bytes, sha: str) -> None:
    """Mark a KB file as processed without keeping a second copy (the KB already has it)."""
    from pathmind.database.models import BranchDocument

    db.add(BranchDocument(user_id=user_id, filename=filename[:255], ref="", size=len(data), sha256=sha))
    db.commit()


def enrich_from_document(
    db: Session, user_id: str, filename: str, data: bytes, *, keep_original: bool = True
) -> dict:
    """Turn a document's outline into overlay nodes under one node named after the file.

    A file whose sha256 was already turned into nodes for this user is skipped.
    ``keep_original=False`` is used for KB uploads: the KB keeps the file and is
    already counted against the storage quota, so no second copy is stored.
    """
    sha = hashlib.sha256(data).hexdigest()
    if _already_processed(db, user_id, sha):
        return {"root_id": None, "created": 0, "anchor_node_id": None, "skipped": "already_processed"}
    try:
        text = ingest.extract_text(filename, data)
    except ingest.IngestError as exc:
        raise BranchError(400, str(exc)) from exc
    branch = get_or_create_branch(db, user_id)
    existing = db.query(BranchNode).filter(BranchNode.branch_id == branch.id).count()
    sections = ingest.build_outline(text)[:MAX_NEW_NODES]
    if existing + len(sections) + 1 > MAX_NODES:
        raise BranchError(400, "Your branch is full. Remove some nodes first.")
    stored_bytes = 0
    if keep_original:
        stored_bytes = _keep_original(db, user_id, filename, data)
    else:
        _remember_processed(db, user_id, filename, data, sha)
    root_title = re.sub(r"\.[^.]+$", "", filename)[:200] or "Document"
    anchors = tree.find_scope(db, root_title + " " + " ".join(s.title for s in sections[:8]), limit=1)
    anchor = anchors[0]["id"] if anchors else None
    root = create_node(
        db, user_id, None, root_title, "", source_doc=filename[:255],
        anchor_node_id=anchor, origin="auto",
    )
    created = 1
    stack: list[tuple[int, str]] = []
    for sec in sections:
        while stack and stack[-1][0] >= sec.level:
            stack.pop()
        parent = stack[-1][1] if stack else root.id
        node = create_node(
            db, user_id, parent, sec.title, ingest.summarise(text, sec),
            source_doc=filename[:255], origin="auto",
        )
        stack.append((sec.level, node.id))
        created += 1
    return {"root_id": root.id, "created": created, "anchor_node_id": anchor, "stored_bytes": stored_bytes}


def auto_enrich_files(user_id: str, file_paths: list[str]) -> int:
    """Background step after a KB upload: grow the uploader's personal branch.

    Deterministic (outline only, no AI credits). Files over 25 MB, unreadable
    formats and files already processed are skipped; nothing here may fail the
    upload, so every error is logged and swallowed. Returns the nodes created.
    """
    if not user_id or not file_paths:
        return 0
    from pathmind.database.connection import get_db_session

    created = 0
    for raw in file_paths:
        path = Path(raw)
        try:
            if not path.is_file() or path.stat().st_size > MAX_AUTO_BYTES:
                continue
            data = path.read_bytes()
            with get_db_session() as db:
                created += int(enrich_from_document(db, user_id, path.name, data, keep_original=False)["created"])
        except BranchError as exc:
            logger.info("Personal branch enrichment skipped %s: %s", path.name, exc.message)
        except Exception as exc:  # noqa: BLE001 - must never break the upload
            logger.warning("Personal branch enrichment failed for %s: %s", path.name, exc)
    return created


def reset(db: Session, user_id: str) -> int:
    """Wipe the overlay (a restorable version is saved first)."""
    branch = get_or_create_branch(db, user_id)
    count = db.query(BranchNode).filter(BranchNode.branch_id == branch.id).count()
    if count == 0:
        return 0
    save_version(db, user_id, "Before reset")
    db.query(BranchNode).filter(BranchNode.branch_id == branch.id).delete(synchronize_session=False)
    _touch(db, branch)
    audit.record(db, user_id, "branch.reset", target_type="branch", target_id=branch.id, data={"nodes": count})
    return count


def has_nodes(db: Session, user_id: str) -> bool:
    from pathmind.database.models import PersonalBranch

    b = db.query(PersonalBranch).filter(PersonalBranch.user_id == user_id).first()
    return bool(b and db.query(BranchNode.id).filter(BranchNode.branch_id == b.id).first())


def search(db: Session, user_id: str, query: str, limit: int = 3) -> list[dict]:
    from pathmind.database.models import PersonalBranch

    terms = tree._terms(query)
    b = db.query(PersonalBranch).filter(PersonalBranch.user_id == user_id).first()
    if not terms or b is None:
        return []
    scored = []
    for n in db.query(BranchNode).filter(BranchNode.branch_id == b.id).limit(5000).all():
        title, summary = n.title.lower(), (n.summary or "").lower()
        score = sum(3 for t in terms if t in title) + sum(1 for t in terms if t in summary)
        if score:
            scored.append((score, n))
    scored.sort(key=lambda x: (-x[0], x[1].title))
    return [{**_node_dict(n), "score": sc} for sc, n in scored[:limit]]
