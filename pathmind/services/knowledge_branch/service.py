"""Rules for a user's personal knowledge branch.

* One branch per user, private to the owner.
* It never changes the main tree; the main tree wins when answers conflict.
* Moves reject cycles; every change bumps ``PersonalBranch.version`` so a stale
  editor can be told to reload (``base_version`` -> 409).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from pathmind.database.models import BranchNode, BranchVersion, PersonalBranch

MAX_TITLE = 200
MAX_SUMMARY = 4000
MAX_NODES = 2000
MAX_VERSIONS = 30


class BranchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _clean(value: Any, limit: int, *, required: bool = False, field: str = "text") -> str:
    text = str(value or "").strip()
    if required and not text:
        raise BranchError(400, f"{field} is required.")
    return text[:limit]


def get_or_create_branch(db: Session, user_id: str) -> PersonalBranch:
    branch = db.query(PersonalBranch).filter(PersonalBranch.user_id == user_id).first()
    if branch is None:
        branch = PersonalBranch(user_id=user_id)
        db.add(branch)
        db.commit()
    return branch


def _touch(db: Session, branch: PersonalBranch) -> None:
    branch.version += 1
    branch.updated_at = datetime.utcnow()
    db.commit()


def _check_base(branch: PersonalBranch, base_version: int | None) -> None:
    if base_version is not None and branch.version != base_version:
        raise BranchError(409, "Your branch changed elsewhere. Reload and try again.")


def _node(db: Session, branch: PersonalBranch, node_id: str) -> BranchNode:
    node = db.get(BranchNode, node_id)
    if node is None or node.branch_id != branch.id:
        raise BranchError(404, "Node not found.")
    return node


def _node_dict(n: BranchNode) -> dict:
    return {
        "id": n.id,
        "parent_id": n.parent_id,
        "title": n.title,
        "summary": n.summary,
        "position": n.position,
        "source_kb": n.source_kb,
        "source_doc": n.source_doc,
        "prerequisites": list(n.prerequisites or []),
        "anchor_node_id": n.anchor_node_id,
        "origin": n.origin or "manual",
    }


def tree_of(db: Session, user_id: str) -> dict:
    branch = get_or_create_branch(db, user_id)
    rows = (
        db.query(BranchNode)
        .filter(BranchNode.branch_id == branch.id)
        .order_by(BranchNode.position, BranchNode.title)
        .all()
    )
    by_parent: dict[str | None, list[dict]] = {}
    for n in rows:
        d = _node_dict(n)
        d["children"] = []
        by_parent.setdefault(n.parent_id, []).append(d)
    index = {d["id"]: d for items in by_parent.values() for d in items}
    for pid, items in by_parent.items():
        if pid in index:
            index[pid]["children"] = items
    roots = [d for d in by_parent.get(None, [])]
    # Orphans (parent deleted elsewhere) surface at the top instead of vanishing.
    orphans = [d for pid, items in by_parent.items() if pid is not None and pid not in index for d in items]
    return {
        "id": branch.id,
        "name": branch.name,
        "version": branch.version,
        "node_count": len(rows),
        "nodes": roots + orphans,
    }


def _next_position(db: Session, branch_id: str, parent_id: str | None) -> int:
    q = db.query(func.max(BranchNode.position)).filter(BranchNode.branch_id == branch_id)
    q = q.filter(BranchNode.parent_id.is_(None) if parent_id is None else BranchNode.parent_id == parent_id)
    value = q.scalar()
    return int(value if value is not None else -1) + 1


def _descendants(db: Session, node_id: str) -> set[str]:
    out: set[str] = set()
    frontier = [node_id]
    while frontier:
        kids = [i for (i,) in db.query(BranchNode.id).filter(BranchNode.parent_id.in_(frontier)).all()]
        kids = [k for k in kids if k not in out]
        out.update(kids)
        frontier = kids
    return out


def create_node(
    db: Session,
    user_id: str,
    parent_id: str | None,
    title: str,
    summary: str = "",
    *,
    source_kb: str | None = None,
    source_doc: str | None = None,
    anchor_node_id: str | None = None,
    origin: str = "manual",
    base_version: int | None = None,
) -> BranchNode:
    branch = get_or_create_branch(db, user_id)
    _check_base(branch, base_version)
    if db.query(func.count(BranchNode.id)).filter(BranchNode.branch_id == branch.id).scalar() >= MAX_NODES:
        raise BranchError(400, f"A branch holds at most {MAX_NODES} nodes.")
    if parent_id is not None:
        _node(db, branch, parent_id)
    node = BranchNode(
        branch_id=branch.id,
        parent_id=parent_id,
        title=_clean(title, MAX_TITLE, required=True, field="Title"),
        summary=_clean(summary, MAX_SUMMARY),
        position=_next_position(db, branch.id, parent_id),
        source_kb=(source_kb or None),
        source_doc=(source_doc or None),
        prerequisites=[],
        anchor_node_id=(anchor_node_id or None),
        origin="auto" if origin == "auto" else "manual",
    )
    db.add(node)
    _touch(db, branch)
    return node


def update_node(
    db: Session,
    user_id: str,
    node_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    base_version: int | None = None,
) -> BranchNode:
    branch = get_or_create_branch(db, user_id)
    _check_base(branch, base_version)
    node = _node(db, branch, node_id)
    if title is not None:
        node.title = _clean(title, MAX_TITLE, required=True, field="Title")
    if summary is not None:
        node.summary = _clean(summary, MAX_SUMMARY)
    node.updated_at = datetime.utcnow()
    _touch(db, branch)
    return node


def move_node(
    db: Session, user_id: str, node_id: str, parent_id: str | None, *, base_version: int | None = None
) -> BranchNode:
    branch = get_or_create_branch(db, user_id)
    _check_base(branch, base_version)
    node = _node(db, branch, node_id)
    if parent_id is not None:
        _node(db, branch, parent_id)
        if parent_id == node_id or parent_id in _descendants(db, node_id):
            raise BranchError(400, "A node cannot be moved under itself.")
    node.parent_id = parent_id
    node.position = _next_position(db, branch.id, parent_id)
    node.updated_at = datetime.utcnow()
    _touch(db, branch)
    return node


def delete_node(db: Session, user_id: str, node_id: str, *, base_version: int | None = None) -> int:
    branch = get_or_create_branch(db, user_id)
    _check_base(branch, base_version)
    _node(db, branch, node_id)
    ids = _descendants(db, node_id) | {node_id}
    db.query(BranchNode).filter(BranchNode.id.in_(ids)).delete(synchronize_session=False)
    # Drop dangling prerequisite references to the removed nodes.
    for n in db.query(BranchNode).filter(BranchNode.branch_id == branch.id).all():
        if any(p in ids for p in (n.prerequisites or [])):
            n.prerequisites = [p for p in n.prerequisites if p not in ids]
    _touch(db, branch)
    return len(ids)


def set_prerequisites(db: Session, user_id: str, node_id: str, prerequisite_ids: list[str]) -> BranchNode:
    """Declare which nodes must be mastered first. Rejects self-reference and cycles."""
    branch = get_or_create_branch(db, user_id)
    node = _node(db, branch, node_id)
    wanted = list(dict.fromkeys(str(p) for p in prerequisite_ids))
    for pid in wanted:
        if pid == node_id:
            raise BranchError(400, "A node cannot require itself.")
        _node(db, branch, pid)
    graph = {n.id: list(n.prerequisites or []) for n in db.query(BranchNode).filter(BranchNode.branch_id == branch.id)}
    graph[node_id] = wanted
    seen: set[str] = set()
    stack = list(wanted)
    while stack:
        cur = stack.pop()
        if cur == node_id:
            raise BranchError(400, "These prerequisites would form a cycle.")
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, []))
    node.prerequisites = wanted
    node.updated_at = datetime.utcnow()
    _touch(db, branch)
    return node


# ── versions ──────────────────────────────────────────────────────────────


def _snapshot(db: Session, branch: PersonalBranch) -> list[dict]:
    rows = db.query(BranchNode).filter(BranchNode.branch_id == branch.id).all()
    return [{**_node_dict(n)} for n in rows]


def save_version(db: Session, user_id: str, label: str = "") -> BranchVersion:
    branch = get_or_create_branch(db, user_id)
    ver = BranchVersion(
        branch_id=branch.id,
        label=_clean(label, 120) or datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        snapshot=_snapshot(db, branch),
    )
    db.add(ver)
    db.commit()
    old = (
        db.query(BranchVersion)
        .filter(BranchVersion.branch_id == branch.id)
        .order_by(BranchVersion.created_at.desc())
        .offset(MAX_VERSIONS)
        .all()
    )
    for row in old:
        db.delete(row)
    if old:
        db.commit()
    return ver


def list_versions(db: Session, user_id: str) -> list[dict]:
    branch = get_or_create_branch(db, user_id)
    rows = (
        db.query(BranchVersion)
        .filter(BranchVersion.branch_id == branch.id)
        .order_by(BranchVersion.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "label": r.label,
            "node_count": len(r.snapshot or []),
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]


def restore_version(db: Session, user_id: str, version_id: str) -> int:
    """Replace the branch content with a saved snapshot (current state is saved first)."""
    branch = get_or_create_branch(db, user_id)
    ver = db.get(BranchVersion, version_id)
    if ver is None or ver.branch_id != branch.id:
        raise BranchError(404, "Version not found.")
    save_version(db, user_id, "before restore")
    db.query(BranchNode).filter(BranchNode.branch_id == branch.id).delete(synchronize_session=False)
    for item in ver.snapshot or []:
        db.add(
            BranchNode(
                id=item["id"],
                branch_id=branch.id,
                parent_id=item.get("parent_id"),
                title=item.get("title") or "Untitled",
                summary=item.get("summary") or "",
                position=int(item.get("position") or 0),
                source_kb=item.get("source_kb"),
                source_doc=item.get("source_doc"),
                prerequisites=list(item.get("prerequisites") or []),
                anchor_node_id=item.get("anchor_node_id"),
                origin=item.get("origin") or "manual",
            )
        )
    _touch(db, branch)
    return len(ver.snapshot or [])
