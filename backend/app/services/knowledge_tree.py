"""Company knowledge tree: publishing change sets, verifying documents, chunks, gaps and rewards.

Writes to the tree only happen through ``publish`` (a change set applied in one transaction) or the
document helpers below, so every published node change leaves a version row behind.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..models import (
    Contribution, ContributionEvent, KnowledgeChunk, KnowledgeDomain, KnowledgeGap, KnowledgeNode,
    KnowledgeNodeDocument, KnowledgeNodeVersion, RewardLedger, TreeChange, TreeChangeSet, User, UserBadge,
    VerifiedDocument, utcnow,
)
from . import extract, llm
from .app_settings import get_setting
from .notify import notify

OPS = ("add_node", "update_node", "move_node", "archive_node", "attach_document", "detach_document",
       "verify_document", "retire_document")


def iso(d):
    return d.isoformat() + "Z" if d else None


# ── Serialisation ────────────────────────────────────────────────────────────


def domain_public(d: KnowledgeDomain, db: Session | None = None) -> dict:
    out = {"id": d.id, "slug": d.slug, "name": d.name, "description": d.description, "answer_policy": d.answer_policy,
           "citation_detail": d.citation_detail, "status": d.status, "created_at": iso(d.created_at),
           "updated_at": iso(d.updated_at)}
    if db is not None:
        out["node_count"] = db.scalar(select(func.count()).select_from(KnowledgeNode).where(
            KnowledgeNode.domain_id == d.id, KnowledgeNode.status == "published")) or 0
        out["document_count"] = db.scalar(select(func.count()).select_from(VerifiedDocument).where(
            VerifiedDocument.domain_id == d.id, VerifiedDocument.status == "verified")) or 0
    return out


def node_public(n: KnowledgeNode) -> dict:
    return {"id": n.id, "domain_id": n.domain_id, "parent_id": n.parent_id, "title": n.title, "summary": n.summary,
            "body": n.body, "position": n.position, "status": n.status, "version": n.version,
            "published_at": iso(n.published_at), "updated_at": iso(n.updated_at)}


def document_public(d: VerifiedDocument, db: Session | None = None) -> dict:
    out = {"id": d.id, "domain_id": d.domain_id, "file_id": d.file_id, "contribution_id": d.contribution_id,
           "title": d.title, "doc_version": d.doc_version, "status": d.status, "supersedes_id": d.supersedes_id,
           "page_count": d.page_count, "verified_at": iso(d.verified_at), "review_due_at": iso(d.review_due_at),
           "created_at": iso(d.created_at), "text_length": len(d.text or ""),
           "overdue": bool(d.review_due_at and d.review_due_at <= utcnow() and d.status == "verified")}
    if db is not None:
        out["chunks"] = db.scalar(select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.document_id == d.id, KnowledgeChunk.is_active.is_(True))) or 0
        out["node_ids"] = list(db.scalars(select(KnowledgeNodeDocument.node_id).where(KnowledgeNodeDocument.document_id == d.id)))
        if d.contribution_id:
            c = db.get(Contribution, d.contribution_id)
            u = db.get(User, c.contributor_id) if c else None
            out["contributor"] = (u.full_name or u.username) if u else None
    return out


def change_public(c: TreeChange) -> dict:
    return {"id": c.id, "seq": c.seq, "op": c.op, "node_id": c.node_id, "temp_key": c.temp_key, "payload": c.payload,
            "origin": c.origin, "accepted": c.accepted}


def change_set_public(cs: TreeChangeSet, db: Session, with_changes: bool = False) -> dict:
    out = {"id": cs.id, "domain_id": cs.domain_id, "contribution_id": cs.contribution_id, "title": cs.title,
           "origin": cs.origin, "status": cs.status, "created_at": iso(cs.created_at), "updated_at": iso(cs.updated_at),
           "published_at": iso(cs.published_at),
           "change_count": db.scalar(select(func.count()).select_from(TreeChange).where(TreeChange.change_set_id == cs.id)) or 0}
    u = db.get(User, cs.created_by) if cs.created_by else None
    out["created_by"] = u.username if u else None
    if with_changes:
        out["changes"] = [change_public(c) for c in db.scalars(
            select(TreeChange).where(TreeChange.change_set_id == cs.id).order_by(TreeChange.seq))]
    return out


def tree_of(db: Session, domain_id: str, include_archived: bool = False) -> list[dict]:
    stmt = select(KnowledgeNode).where(KnowledgeNode.domain_id == domain_id)
    if not include_archived:
        stmt = stmt.where(KnowledgeNode.status == "published")
    nodes = db.scalars(stmt.order_by(KnowledgeNode.position, KnowledgeNode.title)).all()
    docs: dict[str, int] = dict(db.execute(select(KnowledgeNodeDocument.node_id, func.count()).group_by(
        KnowledgeNodeDocument.node_id)).all())
    by_parent: dict[str | None, list[KnowledgeNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)

    def build(pid: str | None) -> list[dict]:
        return [{**node_public(n), "documents": docs.get(n.id, 0), "children": build(n.id)} for n in by_parent.get(pid, [])]

    return build(None)


# ── Change sets ──────────────────────────────────────────────────────────────


def add_change(db: Session, cs: TreeChangeSet, op: str, *, node_id: str | None = None, temp_key: str = "",
               payload: dict | None = None, origin: str = "manual", accepted: bool = True) -> TreeChange:
    if op not in OPS:
        raise HTTPException(status_code=422, detail=f"Unknown operation: {op}")
    seq = (db.scalar(select(func.max(TreeChange.seq)).where(TreeChange.change_set_id == cs.id)) or 0) + 1
    ch = TreeChange(change_set_id=cs.id, seq=seq, op=op, node_id=node_id, temp_key=temp_key[:40],
                    payload=payload or {}, origin=origin, accepted=accepted)
    db.add(ch)
    cs.updated_at = utcnow()
    db.flush()
    return ch


def _snapshot(db: Session, node: KnowledgeNode, cs: TreeChangeSet | None, actor_id: str | None, note: str) -> None:
    node.version = (node.version or 0) + 1
    node.published_at = utcnow()
    node.updated_by = actor_id
    db.add(KnowledgeNodeVersion(node_id=node.id, version=node.version, parent_id=node.parent_id, title=node.title,
                                summary=node.summary, body=node.body, change_set_id=cs.id if cs else None,
                                changed_by=actor_id, change_note=note[:500]))


def _resolve_node(db: Session, domain_id: str, keys: dict[str, str], node_id: str | None, temp_key: str | None) -> KnowledgeNode | None:
    nid = node_id or (keys.get(temp_key) if temp_key else None)
    if not nid:
        return None
    n = db.get(KnowledgeNode, nid)
    if n is None or n.domain_id != domain_id:
        raise HTTPException(status_code=422, detail="A change refers to a node that is not in this domain.")
    return n


def _is_descendant(db: Session, node_id: str, maybe_child: str | None) -> bool:
    cur = maybe_child
    while cur:
        if cur == node_id:
            return True
        cur = db.scalar(select(KnowledgeNode.parent_id).where(KnowledgeNode.id == cur))
    return False


def publish(db: Session, cs: TreeChangeSet, actor: User, data_dir: Path, runner=None) -> dict:
    """Apply every accepted change in order. Caller commits (everything or nothing)."""
    if cs.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be published.")
    keys: dict[str, str] = {}
    counts = {"added": 0, "updated": 0, "moved": 0, "archived": 0, "documents": 0}
    to_index: list[str] = []
    changes = db.scalars(select(TreeChange).where(TreeChange.change_set_id == cs.id, TreeChange.accepted.is_(True))
                         .order_by(TreeChange.seq)).all()
    for ch in changes:
        p = ch.payload or {}
        if ch.op == "add_node":
            title = str(p.get("title") or "").strip()
            if not title:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: a node needs a title.")
            parent = _resolve_node(db, cs.domain_id, keys, p.get("parent_id"), p.get("parent_temp_key"))
            pos = p.get("position")
            if pos is None:
                pos = (db.scalar(select(func.max(KnowledgeNode.position)).where(
                    KnowledgeNode.domain_id == cs.domain_id, KnowledgeNode.parent_id == (parent.id if parent else None))) or 0) + 1
            n = KnowledgeNode(domain_id=cs.domain_id, parent_id=parent.id if parent else None, title=title[:200],
                              summary=str(p.get("summary") or ""), body=str(p.get("body") or ""), position=int(pos),
                              status="published")
            db.add(n)
            db.flush()
            _snapshot(db, n, cs, actor.id, "added")
            if ch.temp_key:
                keys[ch.temp_key] = n.id
            ch.node_id = n.id
            counts["added"] += 1
        elif ch.op == "update_node":
            n = _resolve_node(db, cs.domain_id, keys, ch.node_id, ch.temp_key)
            if n is None:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: which node?")
            for f in ("title", "summary", "body"):
                if f in p and p[f] is not None:
                    setattr(n, f, str(p[f])[:200] if f == "title" else str(p[f]))
            if n.status == "archived" and p.get("unarchive"):
                n.status = "published"
            _snapshot(db, n, cs, actor.id, p.get("note") or "updated")
            counts["updated"] += 1
        elif ch.op == "move_node":
            n = _resolve_node(db, cs.domain_id, keys, ch.node_id, ch.temp_key)
            if n is None:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: which node?")
            parent = _resolve_node(db, cs.domain_id, keys, p.get("parent_id"), p.get("parent_temp_key"))
            if parent is not None and _is_descendant(db, n.id, parent.id):
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: a node cannot move under itself.")
            n.parent_id = parent.id if parent else None
            if p.get("position") is not None:
                n.position = int(p["position"])
            _snapshot(db, n, cs, actor.id, "moved")
            counts["moved"] += 1
        elif ch.op == "archive_node":
            n = _resolve_node(db, cs.domain_id, keys, ch.node_id, ch.temp_key)
            if n is None:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: which node?")
            stack = [n.id]
            while stack:
                nid = stack.pop()
                node = db.get(KnowledgeNode, nid)
                node.status = "archived"
                stack += list(db.scalars(select(KnowledgeNode.id).where(KnowledgeNode.parent_id == nid)))
            _snapshot(db, n, cs, actor.id, "archived")
            counts["archived"] += 1
        elif ch.op in ("attach_document", "detach_document"):
            doc = db.get(VerifiedDocument, p.get("document_id") or "")
            n = _resolve_node(db, cs.domain_id, keys, ch.node_id or p.get("node_id"), ch.temp_key or p.get("node_temp_key"))
            if doc is None or n is None:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: document or node missing.")
            link = db.get(KnowledgeNodeDocument, (n.id, doc.id))
            if ch.op == "attach_document" and link is None:
                db.add(KnowledgeNodeDocument(node_id=n.id, document_id=doc.id, locator=p.get("locator") or {}))
                to_index.append(doc.id)
            elif ch.op == "detach_document" and link is not None:
                db.delete(link)
                to_index.append(doc.id)
            counts["documents"] += 1
        elif ch.op == "verify_document":
            doc = db.get(VerifiedDocument, p.get("document_id") or "")
            if doc is None or doc.domain_id != cs.domain_id:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: document missing.")
            verify_document(db, doc, actor, review_days=p.get("review_days"))
            to_index.append(doc.id)
            counts["documents"] += 1
        elif ch.op == "retire_document":
            doc = db.get(VerifiedDocument, p.get("document_id") or "")
            if doc is None:
                raise HTTPException(status_code=422, detail=f"Change {ch.seq}: document missing.")
            retire_document(db, doc, actor_id=actor.id, note=p.get("note", ""))
            counts["documents"] += 1
    cs.status, cs.published_by, cs.published_at = "published", actor.id, utcnow()
    db.flush()
    # Documents linked to new nodes: re-cut their chunks so each passage knows its node.
    for doc_id in dict.fromkeys(to_index):
        doc = db.get(VerifiedDocument, doc_id)
        if doc is not None and doc.status == "verified":
            index_document(db, doc, data_dir)
    if cs.contribution_id:
        _integrate_contribution(db, cs, actor)
    for gap in db.scalars(select(KnowledgeGap).where(KnowledgeGap.resolved_by_change_set_id == cs.id)):
        gap.status = "resolved"
    return counts


def _integrate_contribution(db: Session, cs: TreeChangeSet, actor: User) -> None:
    c = db.get(Contribution, cs.contribution_id)
    if c is None or c.status == "integrated":
        return
    prev = c.status
    c.status, c.decided_at = "integrated", c.decided_at or utcnow()
    db.add(ContributionEvent(contribution_id=c.id, actor_id=actor.id, kind="status", from_status=prev,
                             to_status="integrated", body=f"Published in '{cs.title}'."))
    rw = get_setting(db, "rewards")
    award(db, c.contributor_id, int(rw.get("points_integrated") or 0), "contribution_integrated", contribution_id=c.id)
    db.flush()
    if c.gap_id:
        gap = db.get(KnowledgeGap, c.gap_id)
        if gap is not None and gap.status != "resolved":
            gap.status, gap.resolved_by_change_set_id = "resolved", cs.id
            award(db, c.contributor_id, int(rw.get("points_gap_resolved") or 0), "gap_resolved", contribution_id=c.id)
    n = db.scalar(select(func.count()).select_from(Contribution).where(
        Contribution.contributor_id == c.contributor_id, Contribution.status == "integrated")) or 0
    for badge, need in (("first_contribution", 1), ("contributor_5", 5), ("contributor_25", 25)):
        if n >= need and db.get(UserBadge, (c.contributor_id, badge)) is None:
            db.add(UserBadge(user_id=c.contributor_id, badge=badge))
    notify(db, c.contributor_id, "contribution_integrated", f"Your contribution '{c.title}' is now part of the knowledge tree.",
           actor_id=actor.id, target_type="contribution", target_id=c.id)


def award(db: Session, user_id: str, points: int, reason: str, *, contribution_id: str | None = None,
          note: str = "", granted_by: str | None = None) -> None:
    if points:
        db.add(RewardLedger(user_id=user_id, points=points, reason=reason[:24], contribution_id=contribution_id,
                            note=note[:500], granted_by=granted_by))


def balance(db: Session, user_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(RewardLedger.points), 0)).where(RewardLedger.user_id == user_id)) or 0)


# ── Documents ────────────────────────────────────────────────────────────────


def verify_document(db: Session, doc: VerifiedDocument, actor: User, review_days: int | None = None) -> None:
    if not (doc.text or "").strip():
        raise HTTPException(status_code=422, detail=f"'{doc.title}' has no readable text, so the AI could not use it.")
    days = int(review_days if review_days is not None else get_setting(db, "knowledge").get("review_interval_days") or 365)
    doc.status, doc.verified_by, doc.verified_at = "verified", actor.id, utcnow()
    doc.review_due_at = utcnow() + timedelta(days=days) if days > 0 else None
    if doc.supersedes_id:
        old = db.get(VerifiedDocument, doc.supersedes_id)
        if old is not None and old.status == "verified":
            old.status = "superseded"
            db.execute(update(KnowledgeChunk).where(KnowledgeChunk.document_id == old.id).values(is_active=False))
            # the new version takes over the old one's place in the tree
            for link in db.scalars(select(KnowledgeNodeDocument).where(KnowledgeNodeDocument.document_id == old.id)):
                if db.get(KnowledgeNodeDocument, (link.node_id, doc.id)) is None:
                    db.add(KnowledgeNodeDocument(node_id=link.node_id, document_id=doc.id, locator=link.locator))


def retire_document(db: Session, doc: VerifiedDocument, actor_id: str | None, note: str = "") -> None:
    doc.status = "retired"
    db.execute(update(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id).values(is_active=False))


def _vector_path(data_dir: Path, doc_id: str) -> Path:
    return data_dir / "vectors" / f"doc_{doc_id}.json"


def index_document(db: Session, doc: VerifiedDocument, data_dir: Path) -> int:
    """Cut a verified document into passages (and embed them when an embedding model is set)."""
    pages = (doc.text or "").split("\f") if "\f" in (doc.text or "") else []
    ex = extract.Extracted(doc.text.replace("\f", "\n\n") if not pages else "\n\n".join(pages), pages)
    passages = extract.split_passages(ex)
    nodes = db.scalars(select(KnowledgeNode).join(KnowledgeNodeDocument, KnowledgeNodeDocument.node_id == KnowledgeNode.id)
                       .where(KnowledgeNodeDocument.document_id == doc.id, KnowledgeNode.status == "published")).all()
    # A passage belongs to the node named like its heading (any published node of the domain,
    # those the document is attached to first); otherwise to the node the document is attached to.
    by_title = {n.title.strip().lower(): n.id for n in db.scalars(select(KnowledgeNode).where(
        KnowledgeNode.domain_id == doc.domain_id, KnowledgeNode.status == "published"))}
    by_title.update({n.title.strip().lower(): n.id for n in nodes})
    default_node = nodes[0].id if nodes else None
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    rows: list[KnowledgeChunk] = []
    for p in passages:
        node_id = default_node
        for part in reversed(p.heading_path.split(" > ")):
            if part.strip().lower() in by_title:
                node_id = by_title[part.strip().lower()]
                break
        text = (f"{p.heading_path}\n{p.text}" if p.heading_path else p.text)[:8000]
        rows.append(KnowledgeChunk(document_id=doc.id, node_id=node_id, seq=p.seq, heading_path=p.heading_path[:500],
                                   text=text, page_from=p.page_from, page_to=p.page_to, token_count=llm.estimate_tokens(text),
                                   content_hash=hashlib.sha256(text.encode()).hexdigest(),
                                   is_active=doc.status == "verified"))
    db.add_all(rows)
    db.flush()
    path = _vector_path(data_dir, doc.id)
    try:
        res = llm.embed(db, [r.text for r in rows], purpose="indexing") if rows else None
    except llm.LLMError:
        res = None  # keyword search still works; vectors can be rebuilt later
    if res:
        vectors, model = res
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({r.id: v for r, v in zip(rows, vectors)}), encoding="utf-8")
        for r in rows:
            r.embedding_model = model
    elif path.exists():
        path.unlink()
    return len(rows)


def load_vectors(data_dir: Path, doc_id: str) -> dict[str, list[float]]:
    path = _vector_path(data_dir, doc_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def propose_from_document(db: Session, doc: VerifiedDocument, actor: User, *, parent_id: str | None,
                          use_ai: bool, contribution_id: str | None = None) -> TreeChangeSet:
    """A draft that adds the document's headings as nodes, attaches and verifies it."""
    if parent_id:
        parent = db.get(KnowledgeNode, parent_id)
        if parent is None or parent.domain_id != doc.domain_id:
            raise HTTPException(status_code=422, detail="The chosen place is not in this document's domain.")
    cs = TreeChangeSet(domain_id=doc.domain_id, title=f"Add: {doc.title}"[:200], origin="document",
                       contribution_id=contribution_id or doc.contribution_id, created_by=actor.id)
    db.add(cs)
    db.flush()
    heads = [(lvl, t, body) for lvl, t, body in extract.headings(doc.text or "") if t.strip()][:80]
    summaries: dict[int, str] = {}
    if use_ai and heads:
        prompt = ("For each numbered section, write a one-sentence summary that helps route questions to it. "
                  "Respond with JSON: {\"summaries\": [\"...\", ...]} in the same order and language as the text.\n\n"
                  + "\n\n".join(f"[{i + 1}] {t}\n{b[:1200]}" for i, (_, t, b) in enumerate(heads)))
        try:
            data = llm.complete_json(db, user=actor, messages=[{"role": "user", "content": prompt}],
                                     purpose="tree_propose", fallback={})
            for i, s in enumerate((data or {}).get("summaries") or []):
                if isinstance(s, str):
                    summaries[i] = s.strip()[:500]
        except (llm.LLMError, HTTPException):
            pass
    stack: list[tuple[int, str]] = []  # (level, temp_key)
    if not heads:
        heads = [(1, doc.title, (doc.text or "")[:2000])]
    min_level = min(lvl for lvl, _, _ in heads)
    for i, (lvl, title, body) in enumerate(heads):
        lvl = lvl - min_level + 1
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        key = f"n{i + 1}"
        payload: dict[str, Any] = {"title": title[:200], "summary": summaries.get(i) or _first_sentence(body),
                                   "body": ""}
        if stack:
            payload["parent_temp_key"] = stack[-1][1]
        elif parent_id:
            payload["parent_id"] = parent_id
        add_change(db, cs, "add_node", temp_key=key, payload=payload, origin="suggested")
        stack.append((lvl, key))
    first_key = "n1"
    add_change(db, cs, "attach_document", temp_key=first_key, payload={"document_id": doc.id, "node_temp_key": first_key},
               origin="suggested")
    if doc.status != "verified":
        add_change(db, cs, "verify_document", payload={"document_id": doc.id}, origin="suggested")
    return cs


def _first_sentence(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"(.{20,300}?[.!?。])(\s|$)", t)
    return (m.group(1) if m else t[:200]).strip()


# ── Gaps ─────────────────────────────────────────────────────────────────────


def fingerprint(question: str) -> str:
    words = sorted(set(w for w in re.findall(r"\w+", question.lower()) if len(w) > 2))
    return hashlib.sha256(" ".join(words).encode()).hexdigest()


def record_gap(db: Session, question: str, domain_id: str | None) -> KnowledgeGap:
    fp = fingerprint(question)
    gap = db.scalar(select(KnowledgeGap).where(KnowledgeGap.fingerprint == fp))
    now = utcnow()
    if gap is None:
        gap = KnowledgeGap(question=question[:2000], fingerprint=fp, domain_id=domain_id, first_asked_at=now, last_asked_at=now)
        db.add(gap)
        db.flush()
    else:
        gap.ask_count += 1
        gap.last_asked_at = now
        if gap.status == "resolved":
            gap.status = "open"  # asked again although considered solved
    return gap


def gap_public(g: KnowledgeGap, db: Session) -> dict:
    d = db.get(KnowledgeDomain, g.domain_id) if g.domain_id else None
    return {"id": g.id, "question": g.question, "ask_count": g.ask_count, "status": g.status, "domain_id": g.domain_id,
            "domain": d.name if d else None, "first_asked_at": iso(g.first_asked_at), "last_asked_at": iso(g.last_asked_at)}
