"""Edit history of the main knowledge graph, and reverting one edit.

Every staff edit runs inside :func:`tracked`, which diffs the domain before and
after (its row, its nodes and its documents) and stores only what changed.

Reverting puts the changed entities back to their ``before`` state. It is
refused (409) when any of them changed again afterwards: the newer edit has to
be reverted first, so nobody's later work is silently overwritten. A revert is
recorded like any other edit, so it can be reverted in turn (redo).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import DateTime
from sqlalchemy.orm import Session

from pathmind.database.models import (
    KnowledgeDomain,
    KnowledgeEdge,
    KnowledgeLabel,
    KnowledgeNode,
    KnowledgeRelationType,
    KnowledgeSource,
    KnowledgeTreeChange,
)

from .service import TreeError

MODELS = {
    "domain": KnowledgeDomain,
    "node": KnowledgeNode,
    "source": KnowledgeSource,
    "edge": KnowledgeEdge,
    "label": KnowledgeLabel,
    "relation": KnowledgeRelationType,
}
# Schema edits (labels, relation types) are recorded under this pseudo-domain.
SCHEMA_SCOPE = "__schema__"
# Columns that are bookkeeping only: a difference there alone is not an edit.
_IGNORED = {"updated_at"}


def _row(obj: Any, *, with_text: bool = True) -> dict:
    out = {}
    for col in obj.__table__.columns:
        if col.name == "text" and not with_text:
            continue
        value = getattr(obj, col.name)
        out[col.name] = value.isoformat() if isinstance(value, datetime) else value
    return out


def snapshot(db: Session, domain_id: str | None, *, source_text: bool = False) -> dict[str, dict]:
    """Every entity of one domain, keyed ``kind:id``. Document text only on request."""
    if not domain_id:
        return {}
    out: dict[str, dict] = {}
    if domain_id == SCHEMA_SCOPE:
        for x in db.query(KnowledgeLabel).all():
            out[f"label:{x.id}"] = _row(x)
        for x in db.query(KnowledgeRelationType).all():
            out[f"relation:{x.id}"] = _row(x)
        return out
    domain = db.get(KnowledgeDomain, domain_id)
    if domain is not None:
        out[f"domain:{domain.id}"] = _row(domain)
    for n in db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain_id).all():
        out[f"node:{n.id}"] = _row(n)
    for s in db.query(KnowledgeSource).filter(KnowledgeSource.domain_id == domain_id).all():
        out[f"source:{s.id}"] = _row(s, with_text=source_text)
    for e in db.query(KnowledgeEdge).filter(KnowledgeEdge.domain_id == domain_id).all():
        out[f"edge:{e.id}"] = _row(e)
    return out


def _same(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is b
    keys = (set(a) | set(b)) - _IGNORED - {"text"}
    return all(a.get(k) == b.get(k) for k in keys)


def _fill_source_text(db: Session, side: dict[str, dict]) -> None:
    for key, row in side.items():
        if key.startswith("source:") and "text" not in row:
            src = db.get(KnowledgeSource, key.split(":", 1)[1])
            row["text"] = src.text if src is not None else ""


def _describe(before: dict, after: dict) -> str:
    """E.g. ``Removed “Geometry” and 1 more; changed “Algebra”``."""
    groups: dict[str, list[dict]] = {"added": [], "changed": [], "removed": []}
    for key in set(before) | set(after):
        verb = "added" if key not in before else "removed" if key not in after else "changed"
        groups[verb].append(after.get(key) or before.get(key) or {})
    parts = []
    for verb, rows in groups.items():
        if not rows:
            continue
        ids = {r.get("id") for r in rows}
        # The top of a subtree names it best: a row whose parent is not in the group.
        rows.sort(key=lambda r: (r.get("parent_id") in ids, "domain_id" in r, str(_label(r))))
        first = _label(rows[0]) or "item"
        more = f" and {len(rows) - 1} more" if len(rows) > 1 else ""
        parts.append(f"{verb} “{first[:80]}”{more}")
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def record(
    db: Session, user_id: str, domain_id: str, action: str, before_all: dict, after_all: dict,
    *, summary: str = "", reverts_id: int | None = None,
) -> KnowledgeTreeChange | None:
    """Store the difference between two snapshots (nothing when they are equal)."""
    keys = [k for k in set(before_all) | set(after_all) if not _same(before_all.get(k), after_all.get(k))]
    if not keys:
        return None
    before = {k: before_all[k] for k in keys if k in before_all}
    after = {k: after_all[k] for k in keys if k in after_all}
    _fill_source_text(db, after)
    name_row = after.get(f"domain:{domain_id}") or before.get(f"domain:{domain_id}") or {}
    if domain_id == SCHEMA_SCOPE:
        name_row = {"name": "Graph schema"}
    domain = db.get(KnowledgeDomain, domain_id)
    change = KnowledgeTreeChange(
        domain_id=domain_id,
        domain_name=str(name_row.get("name") or (domain.name if domain else ""))[:120],
        actor_id=str(user_id or "")[:64],
        action=action[:40],
        summary=(summary or _describe(before, after))[:300],
        before=before,
        after=after,
        reverts_id=reverts_id,
    )
    db.add(change)
    db.commit()
    return change


@contextmanager
def tracked(
    db: Session, user_id: str, domain_id: str | None, action: str, *, summary: str = "",
    source_text: bool = False,
) -> Iterator[dict]:
    """Record the edit made inside the block.

    Yields a dict; a block that creates a domain sets ``box["domain_id"]``.
    ``source_text`` keeps document text in the "before" snapshot (needed when
    the block may delete documents, so a revert can bring them back).
    """
    before = snapshot(db, domain_id, source_text=source_text)
    box: dict[str, Any] = {"domain_id": domain_id, "summary": summary}
    yield box
    target = box.get("domain_id")
    if not target:
        return
    after = snapshot(db, target)
    record(db, user_id, target, action, before if target == domain_id else {}, after, summary=box.get("summary") or "")


# ── reading ────────────────────────────────────────────────────────────────


def _label(row: dict | None) -> str:
    if not row:
        return ""
    if "rel_type" in row:
        return str(row.get("rel_type") or "")
    return str(row.get("title") or row.get("name") or row.get("filename") or "")


def _actor_name(user_id: str, cache: dict[str, str]) -> str:
    if user_id not in cache:
        try:
            from pathmind.multi_user.identity import get_user_by_id

            found = get_user_by_id(user_id) if user_id else None
            cache[user_id] = found[0] if found else ""
        except Exception:  # noqa: BLE001 - a name is a nicety
            cache[user_id] = ""
    return cache[user_id]


def _view(c: KnowledgeTreeChange, detail: bool = False, names: dict[str, str] | None = None) -> dict:
    d = {
        "id": c.id,
        "domain_id": c.domain_id,
        "domain_name": c.domain_name,
        "actor_id": c.actor_id,
        "actor_name": _actor_name(c.actor_id, names if names is not None else {}),
        "action": c.action,
        "summary": c.summary,
        "reverts_id": c.reverts_id,
        "reverted_by_id": c.reverted_by_id,
        "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
    }
    if detail:
        items = []
        for key in sorted(set(c.before or {}) | set(c.after or {})):
            b, a = (c.before or {}).get(key), (c.after or {}).get(key)
            items.append({
                "key": key,
                "kind": key.split(":", 1)[0],
                "change": "added" if b is None else "removed" if a is None else "changed",
                "before": _label(b),
                "after": _label(a),
                "fields": sorted(k for k in (set(a or {}) | set(b or {})) - _IGNORED - {"text", "version"}
                                 if b and a and b.get(k) != a.get(k)),
            })
        d["items"] = items
    return d


def list_changes(db: Session, domain_id: str | None = None, limit: int = 100) -> list[dict]:
    q = db.query(KnowledgeTreeChange)
    if domain_id:
        q = q.filter(KnowledgeTreeChange.domain_id == domain_id)
    names: dict[str, str] = {}
    rows = q.order_by(KnowledgeTreeChange.id.desc()).limit(min(max(limit, 1), 500)).all()
    return [_view(c, names=names) for c in rows]


def deleted_domains(db: Session) -> list[dict]:
    """Domains whose rows are gone but whose history remains (so they can be restored)."""
    live = {i for (i,) in db.query(KnowledgeDomain.id).all()} | {SCHEMA_SCOPE}
    seen: dict[str, dict] = {}
    for c in db.query(KnowledgeTreeChange).order_by(KnowledgeTreeChange.id.desc()).limit(2000).all():
        if c.domain_id not in live and c.domain_id not in seen:
            seen[c.domain_id] = {"domain_id": c.domain_id, "domain_name": c.domain_name, "last_change_id": c.id}
    return list(seen.values())


def change_detail(db: Session, change_id: int) -> dict:
    c = db.get(KnowledgeTreeChange, change_id)
    if c is None:
        raise TreeError(404, "Change not found.")
    return _view(c, detail=True)


# ── reverting ───────────────────────────────────────────────────────────────


def _current(db: Session, key: str, *, with_text: bool) -> dict | None:
    kind, ident = key.split(":", 1)
    obj = db.get(MODELS[kind], ident)
    return _row(obj, with_text=with_text) if obj is not None else None


def _write(db: Session, key: str, row: dict | None) -> None:
    kind, ident = key.split(":", 1)
    model = MODELS[kind]
    obj = db.get(model, ident)
    if row is None:
        if obj is not None:
            db.delete(obj)
        return
    if obj is None:
        obj = model(id=ident)
        db.add(obj)
    for col in model.__table__.columns:
        if col.name == "id" or col.name not in row:
            continue
        value = row[col.name]
        if isinstance(col.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", ""))
        setattr(obj, col.name, value)


_ORDER = {"label": 0, "relation": 0, "domain": 0, "source": 1, "node": 2, "edge": 3}


def revert(db: Session, role: str, user_id: str, change_id: int) -> dict:
    """Put every entity the change touched back to its ``before`` state."""
    from .service import _require_edit

    _require_edit(role)
    change = db.get(KnowledgeTreeChange, change_id)
    if change is None:
        raise TreeError(404, "Change not found.")
    if change.reverted_by_id:
        raise TreeError(409, "This change was already reverted.")
    before, after = change.before or {}, change.after or {}
    keys = sorted(set(before) | set(after), key=lambda k: _ORDER[k.split(":", 1)[0]])
    current = {k: _current(db, k, with_text=k.startswith("source:")) for k in keys}
    drifted = [k for k in keys if not _same(current[k], after.get(k))]
    if drifted:
        newer = (
            db.query(KnowledgeTreeChange.id)
            .filter(KnowledgeTreeChange.id > change.id, KnowledgeTreeChange.domain_id == change.domain_id,
                    KnowledgeTreeChange.reverted_by_id.is_(None))
            .order_by(KnowledgeTreeChange.id.desc())
            .first()
        )
        hint = f" Revert change #{newer[0]} first." if newer else ""
        raise TreeError(409, "Part of this change was edited again afterwards." + hint)
    # Parents before children when restoring, children before parents when removing.
    restore = [k for k in keys if k in before]
    remove = [k for k in reversed(keys) if k not in before]
    for k in restore:
        _write(db, k, before[k])
        db.flush()
    for k in remove:
        _write(db, k, None)
        db.flush()
    db.commit()
    undo = record(
        db, user_id, change.domain_id, "revert",
        {k: v for k, v in current.items() if v is not None},
        {k: before[k] for k in restore},
        summary=f"Revert #{change.id}: {change.summary}"[:300],
        reverts_id=change.id,
    )
    change.reverted_by_id = undo.id if undo is not None else None
    if change.reverts_id:  # reverting a revert re-opens the original change
        original = db.get(KnowledgeTreeChange, change.reverts_id)
        if original is not None and original.reverted_by_id == change.id:
            original.reverted_by_id = None
    db.commit()
    from pathmind.services import audit

    audit.record(db, user_id, "tree.revert", target_type="tree_change", target_id=str(change.id),
                 data={"domain_id": change.domain_id, "revert_change_id": undo.id if undo else None})
    return {"reverted": change.id, "change": _view(undo) if undo else None}
