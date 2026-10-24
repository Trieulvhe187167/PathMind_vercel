"""Mastery path over a personal branch: study order from prerequisites + mastered marks."""

from __future__ import annotations

from sqlalchemy.orm import Session

from pathmind.database.models import BranchNode, NodeMastery

from .service import BranchError, _node, get_or_create_branch


def _mastered(db: Session, user_id: str) -> set[str]:
    return {n for (n,) in db.query(NodeMastery.node_id).filter(NodeMastery.user_id == user_id).all()}


def set_mastered(db: Session, user_id: str, node_id: str, mastered: bool) -> dict:
    """Mark or unmark a node as mastered.

    A node can only be marked once all of its prerequisites are mastered, and a
    node cannot be unmarked while a mastered node depends on it (the path would
    otherwise show a mastered node whose prerequisite is not).
    """
    branch = get_or_create_branch(db, user_id)
    node = _node(db, branch, node_id)
    done = _mastered(db, user_id)
    row = db.query(NodeMastery).filter_by(user_id=user_id, node_id=node_id).first()
    if mastered and row is None:
        siblings = {
            n.id: n.title
            for n in db.query(BranchNode).filter(BranchNode.branch_id == branch.id).all()
        }
        missing = [p for p in (node.prerequisites or []) if p in siblings and p not in done]
        if missing:
            raise BranchError(
                409, "Master the prerequisites first: " + ", ".join(siblings[p] for p in missing[:5])
            )
        db.add(NodeMastery(user_id=user_id, node_id=node_id))
        db.commit()
        _reward_mastery(db, user_id, node_id)
    elif not mastered and row is not None:
        dependents = [
            n.title
            for n in db.query(BranchNode).filter(BranchNode.branch_id == branch.id).all()
            if n.id in done and node_id in (n.prerequisites or [])
        ]
        if dependents:
            raise BranchError(409, "Unmark the nodes that build on this first: " + ", ".join(dependents[:5]))
        db.delete(row)
        db.commit()
    return {"node_id": node_id, "mastered": mastered}


def _reward_mastery(db: Session, user_id: str, node_id: str) -> None:
    """Points for a newly mastered node (once per node, daily cap). Never blocks the mark."""
    try:
        from pathmind.services import rewards

        rewards.earn_once(db, user_id, "earn.node_mastered", ref=f"mastery:{node_id}")
    except Exception:  # noqa: BLE001 - rewards are a bonus, not part of the path
        db.rollback()


def mastery_path(db: Session, user_id: str) -> dict:
    """Nodes in a valid study order (prerequisites first).

    Status per node: ``mastered``; ``ready`` (all prerequisites mastered, or none);
    ``locked`` (some prerequisite is not mastered yet, listed in ``blocked_by``).
    """
    branch = get_or_create_branch(db, user_id)
    nodes = db.query(BranchNode).filter(BranchNode.branch_id == branch.id).order_by(BranchNode.position, BranchNode.title).all()
    by_id = {n.id: n for n in nodes}
    done = _mastered(db, user_id) & set(by_id)
    order: list[str] = []
    seen: set[str] = set()

    def visit(nid: str, stack: frozenset[str] = frozenset()) -> None:
        if nid in seen or nid in stack:  # cycles are rejected on write; guard anyway
            return
        for p in by_id[nid].prerequisites or []:
            if p in by_id:
                visit(p, stack | {nid})
        seen.add(nid)
        order.append(nid)

    for n in nodes:
        visit(n.id)
    items = []
    for nid in order:
        n = by_id[nid]
        reqs = [p for p in (n.prerequisites or []) if p in by_id]
        blocked = [p for p in reqs if p not in done]
        status = "mastered" if nid in done else ("locked" if blocked else "ready")
        items.append({
            "id": nid, "title": n.title, "summary": n.summary, "status": status,
            "prerequisites": [{"id": p, "title": by_id[p].title} for p in reqs],
            "blocked_by": [{"id": p, "title": by_id[p].title} for p in blocked],
        })
    total = len(items)
    return {
        "items": items,
        "mastered": len(done),
        "total": total,
        "next": next((i["id"] for i in items if i["status"] == "ready"), None),
    }
