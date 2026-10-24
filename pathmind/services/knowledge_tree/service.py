"""Knowledge-tree rules: R&D (and administrators) curate the main graph; it is hidden
from everyone else, who only meet it through the tutor.

The tutor reads the tree (``knowledge_tree`` / ``knowledge_search`` tools).
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import re
import unicodedata
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from pathmind.database.models import (
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
)

from . import ingest

# Only R&D changes the main graph. Administrators read it and its edit history
# (so they can follow what R&D commits) but cannot edit it from their account.
EDIT_ROLES = frozenset({"rnd"})
VIEW_ROLES = frozenset({"rnd", "admin"})
MAX_TITLE = 200
MAX_SUMMARY = 4000
MAX_REASON = 1000


class TreeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def can_edit(role: str) -> bool:
    return role in EDIT_ROLES


def can_view(role: str) -> bool:
    return role in VIEW_ROLES


def _require_edit(role: str) -> None:
    if not can_edit(role):
        raise TreeError(403, "Only R&D can change the main knowledge graph.")


def _require_view(role: str) -> None:
    if not can_view(role):
        raise TreeError(403, "The main knowledge graph is only visible to R&D and administrators.")


def _now() -> datetime:
    return datetime.utcnow()


def _clean(value: Any, limit: int, *, required: bool = False, field: str = "text") -> str:
    text = str(value or "").strip()
    if required and not text:
        raise TreeError(400, f"{field} is required.")
    return text[:limit]


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:48] or "domain"


# ── reading ───────────────────────────────────────────────────────────────


def domain_summaries(db: Session) -> list[dict]:
    counts = dict(
        db.query(KnowledgeNode.domain_id, func.count(KnowledgeNode.id))
        .group_by(KnowledgeNode.domain_id)
        .all()
    )
    return [
        {
            "id": d.id,
            "slug": d.slug,
            "name": d.name,
            "description": d.description,
            "kb_name": d.kb_name,
            "nodes": int(counts.get(d.id, 0)),
        }
        for d in db.query(KnowledgeDomain).order_by(KnowledgeDomain.name).all()
    ]


def _node_dict(n: KnowledgeNode) -> dict:
    return {
        "id": n.id,
        "parent_id": n.parent_id,
        "title": n.title,
        "summary": n.summary,
        "version": n.version,
        "refs": n.refs or [],
        "label": n.label or "Concept",
        "layer": n.layer or "",
        "properties": n.properties or {},
        "key": n.key or "",
        "names": n.names or {},
        "description": n.description or "",
        "provenance": n.provenance or {},
        "created_by": n.created_by or "",
        "created_at": n.created_at.isoformat() + "Z" if n.created_at else None,
        "updated_by": n.updated_by or "",
        "updated_at": n.updated_at.isoformat() + "Z" if n.updated_at else None,
    }


def tree_of(db: Session, domain_id: str) -> dict:
    domain = db.get(KnowledgeDomain, domain_id)
    if domain is None:
        raise TreeError(404, "Domain not found.")
    rows = (
        db.query(KnowledgeNode)
        .filter(KnowledgeNode.domain_id == domain_id)
        .order_by(KnowledgeNode.position, KnowledgeNode.title)
        .all()
    )
    by_parent: dict[str | None, list[dict]] = {}
    for n in rows:
        by_parent.setdefault(n.parent_id, []).append({**_node_dict(n), "children": []})
    ids = {n.id for n in rows}

    def attach(items: list[dict]) -> list[dict]:
        for item in items:
            item["children"] = attach(by_parent.get(item["id"], []))
        return items

    roots = by_parent.get(None, [])
    # Orphans (parent deleted out from under them) surface as topics, not lost.
    for pid, items in by_parent.items():
        if pid is not None and pid not in ids:
            roots.extend(items)
    sources = [
        {"id": s.id, "filename": s.filename, "size": s.size}
        for s in db.query(KnowledgeSource).filter(KnowledgeSource.domain_id == domain_id)
    ]
    return {
        "domain": {
            "id": domain.id, "slug": domain.slug, "name": domain.name,
            "description": domain.description, "kb_name": domain.kb_name,
        },
        "nodes": attach(roots),
        "sources": sources,
    }


def node_detail(db: Session, node_id: str, excerpt_chars: int = 3000) -> dict:
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise TreeError(404, "Node not found.")
    excerpts: list[dict] = []
    for ref in node.refs or []:
        src = db.get(KnowledgeSource, str(ref.get("source_id") or ""))
        if src is None:
            continue
        start, end = int(ref.get("start") or 0), int(ref.get("end") or 0)
        excerpts.append(
            {
                "source_id": src.id,
                "filename": src.filename,
                "text": src.text[start:end][:excerpt_chars],
            }
        )
    from .graph import relations_of

    return {**_node_dict(node), "domain_id": node.domain_id, "excerpts": excerpts,
            "relations": relations_of(db, node_id)}


def has_content(db: Session) -> bool:
    return db.query(KnowledgeNode.id).first() is not None


def outline_text(db: Session, domain_id: str, limit: int = 200) -> str:
    data = tree_of(db, domain_id)
    lines = [f"{data['domain']['name']} ({data['domain']['slug']})"]

    def walk(items: list[dict], depth: int) -> None:
        for item in items:
            if len(lines) >= limit:
                return
            lines.append(f"{'  ' * depth}- {item['title']} [{item['id']}]")
            walk(item["children"], depth + 1)

    walk(data["nodes"], 0)
    return "\n".join(lines)


def search_nodes(db: Session, query: str, limit: int = 10) -> list[dict]:
    q = f"%{query.strip()[:80]}%"
    if q == "%%":
        return []
    rows = (
        db.query(KnowledgeNode, KnowledgeDomain.name)
        .join(KnowledgeDomain, KnowledgeDomain.id == KnowledgeNode.domain_id)
        .filter((KnowledgeNode.title.ilike(q)) | (KnowledgeNode.summary.ilike(q)))
        .limit(limit)
        .all()
    )
    return [{**_node_dict(n), "domain": name} for n, name in rows]


# ── scope finding (the tree half of unified retrieval) ──────────────────


def _terms(query: str) -> list[str]:
    """Lower-case words, plus character bigrams for CJK runs (no spaces there)."""
    q = (query or "").lower()[:300]
    out: list[str] = []
    for word in re.findall(r"\w+", q):
        if re.search(r"[\u3400-\u9fff]", word):
            out.extend(word[i : i + 2] for i in range(max(len(word) - 1, 1)))
        elif len(word) >= 2:
            out.append(word)
    return list(dict.fromkeys(out))[:24]


def find_scope(db: Session, query: str, domain_id: str | None = None, limit: int = 5) -> list[dict]:
    """Rank nodes against ``query``; each hit carries its ancestor ``path`` and domain.

    Deterministic keyword scoring (title ×3, summary ×1, ancestor title ×1) so the
    scope step costs nothing and is easy to reason about; the GraphRAG step does
    the semantic work inside whatever scope this finds.
    """
    terms = _terms(query)
    if not terms:
        return []
    q = db.query(KnowledgeNode)
    if domain_id:
        q = q.filter(KnowledgeNode.domain_id == domain_id)
    rows = q.limit(20000).all()
    by_id = {n.id: n for n in rows}
    names = {d.id: d.name for d in db.query(KnowledgeDomain).all()}

    def path_of(n: KnowledgeNode) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        cur = by_id.get(n.parent_id) if n.parent_id else None
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            out.append(cur.title)
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        return list(reversed(out))

    scored: list[tuple[int, KnowledgeNode, list[str]]] = []
    for n in rows:
        title, summary = n.title.lower(), (n.summary or "").lower()
        path = path_of(n)
        trail = " ".join(path).lower()
        score = sum(3 for t in terms if t in title) + sum(1 for t in terms if t in summary)
        score += sum(1 for t in terms if t in trail)
        if score:
            scored.append((score, n, path))
    scored.sort(key=lambda x: (-x[0], x[1].title))
    return [
        {**_node_dict(n), "domain_id": n.domain_id, "domain": names.get(n.domain_id, ""), "path": path, "score": sc}
        for sc, n, path in scored[:limit]
    ]


# ── direct edits (administrators) ────────────────────────────────────────────


def _tracked(*args, **kwargs):
    from .history import tracked

    return tracked(*args, **kwargs)


def _domain_of_node(db: Session, node_id: str) -> str | None:
    node = db.get(KnowledgeNode, node_id)
    return node.domain_id if node is not None else None


def create_domain(db: Session, role: str, user_id: str, name: str, description: str = "") -> KnowledgeDomain:
    _require_edit(role)
    with _tracked(db, user_id, None, "domain.create") as box:
        domain = _create_domain(db, user_id, name, description)
        box["domain_id"] = domain.id
    return domain


def _create_domain(db: Session, user_id: str, name: str, description: str) -> KnowledgeDomain:
    name = _clean(name, 120, required=True, field="Name")
    base = slugify(name)
    slug, n = base, 2
    while db.query(KnowledgeDomain.id).filter(KnowledgeDomain.slug == slug).first():
        slug, n = f"{base}-{n}", n + 1
    domain = KnowledgeDomain(
        slug=slug,
        name=name,
        description=_clean(description, MAX_SUMMARY),
        created_by=user_id,
    )
    db.add(domain)
    db.commit()
    return domain


def delete_domain(db: Session, role: str, domain_id: str, user_id: str = "") -> None:
    """Delete a domain with its nodes and documents (revertable from the history)."""
    _require_edit(role)
    domain = db.get(KnowledgeDomain, domain_id)
    if domain is None:
        raise TreeError(404, "Domain not found.")
    with _tracked(db, user_id, domain_id, "domain.delete", summary=f"Deleted domain “{domain.name}”",
                  source_text=True):
        _delete_domain_rows(db, domain)


def _delete_domain_rows(db: Session, domain: KnowledgeDomain) -> None:
    domain_id = domain.id
    from pathmind.database.models import KnowledgeEdge

    db.query(KnowledgeEdge).filter(KnowledgeEdge.domain_id == domain_id).delete(synchronize_session=False)
    db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain_id).delete(synchronize_session=False)
    db.query(KnowledgeSource).filter(KnowledgeSource.domain_id == domain_id).delete(synchronize_session=False)
    db.delete(domain)
    db.commit()


def update_domain(
    db: Session, role: str, domain_id: str, *, name: str | None = None, description: str | None = None,
    kb_name: str | None = None, set_kb: bool = False, user_id: str = "",
) -> KnowledgeDomain:
    """Rename / describe a domain and link its GraphRAG knowledge base."""
    _require_edit(role)
    with _tracked(db, user_id, domain_id, "domain.update"):
        return _update_domain(db, domain_id, name, description, kb_name, set_kb)


def _update_domain(
    db: Session, domain_id: str, name: str | None, description: str | None, kb_name: str | None, set_kb: bool
) -> KnowledgeDomain:
    domain = db.get(KnowledgeDomain, domain_id)
    if domain is None:
        raise TreeError(404, "Domain not found.")
    if name is not None:
        domain.name = _clean(name, 120, required=True, field="Name")
    if description is not None:
        domain.description = _clean(description, MAX_SUMMARY)
    if set_kb:
        link = _clean(kb_name, 200)
        if link and link not in admin_kb_names():
            raise TreeError(400, "That GraphRAG knowledge base does not exist in the admin or R&D libraries.")
        domain.kb_name = link or None
    domain.updated_at = _now()
    db.commit()
    return domain


def linkable_kbs() -> list[dict]:
    """GraphRAG knowledge bases a domain may link: the admin library and every R&D account's.

    A reference is the plain KB name for the admin library (how links were
    stored before) or ``<user id>/<name>`` for an R&D account's own KB.
    """
    out: list[dict] = []
    try:
        from pathmind.multi_user.identity import load_users
        from pathmind.multi_user.knowledge_access import _manager_for, admin_kb_base_dir
        from pathmind.multi_user.paths import get_path_service_for_scope, scope_for_user
    except Exception:  # noqa: BLE001
        return out

    def add(base_dir, owner_id: str, owner_name: str) -> None:
        try:
            manager = _manager_for(str(base_dir.resolve()))
            for name in manager.list_knowledge_bases():
                entry = manager.get_kb_entry(name) or {}
                provider = str(entry.get("rag_provider") or "").lower()
                if provider and provider != "graphrag":
                    continue  # the main graph links GraphRAG KBs (D2)
                ref = name if not owner_id else f"{owner_id}/{name}"
                out.append({"ref": ref, "name": name, "owner_id": owner_id, "owner": owner_name})
        except Exception:  # noqa: BLE001 - one unreadable library must not hide the others
            pass

    add(admin_kb_base_dir(), "", "admin")
    for username, record in load_users().items():
        if str(record.get("role") or "") == "rnd" and record.get("id"):
            scope = scope_for_user(str(record["id"]), is_admin=False)
            add(get_path_service_for_scope(scope).get_knowledge_bases_root(), str(record["id"]), username)
    return sorted(out, key=lambda x: (x["owner"], x["name"]))


def resolve_kb_ref(ref: str) -> tuple[str, str]:
    """``(library dir, kb name)`` for a linked KB reference (see :func:`linkable_kbs`)."""
    from pathmind.multi_user.knowledge_access import admin_kb_base_dir
    from pathmind.multi_user.paths import get_path_service_for_scope, scope_for_user

    owner, _, name = ref.rpartition("/")
    if not owner:
        return str(admin_kb_base_dir()), ref
    scope = scope_for_user(owner, is_admin=False)
    return str(get_path_service_for_scope(scope).get_knowledge_bases_root()), name


def admin_kb_names() -> list[str]:
    """References of the knowledge bases a domain may link (kept for older callers)."""
    return [x["ref"] for x in linkable_kbs()]


def _next_position(db: Session, domain_id: str, parent_id: str | None) -> int:
    q = db.query(func.max(KnowledgeNode.position)).filter(KnowledgeNode.domain_id == domain_id)
    q = q.filter(KnowledgeNode.parent_id.is_(None) if parent_id is None else KnowledgeNode.parent_id == parent_id)
    return int(q.scalar() if q.scalar() is not None else -1) + 1


def _parent_ok(db: Session, domain_id: str, parent_id: str | None) -> None:
    if parent_id is None:
        return
    parent = db.get(KnowledgeNode, parent_id)
    if parent is None or parent.domain_id != domain_id:
        raise TreeError(400, "Parent node not found in this domain.")


def create_node(
    db: Session, role: str, user_id: str, domain_id: str, parent_id: str | None, title: str, summary: str = ""
) -> KnowledgeNode:
    _require_edit(role)
    with _tracked(db, user_id, domain_id, "node.create"):
        return _create_node(db, user_id, domain_id, parent_id, title, summary)


def _create_node(
    db: Session, user_id: str, domain_id: str, parent_id: str | None, title: str, summary: str
) -> KnowledgeNode:
    if db.get(KnowledgeDomain, domain_id) is None:
        raise TreeError(404, "Domain not found.")
    _parent_ok(db, domain_id, parent_id)
    node = KnowledgeNode(
        domain_id=domain_id,
        parent_id=parent_id,
        title=_clean(title, MAX_TITLE, required=True, field="Title"),
        summary=_clean(summary, MAX_SUMMARY),
        position=_next_position(db, domain_id, parent_id),
        refs=[],
        updated_by=user_id,
        created_by=user_id,
    )
    db.add(node)
    db.flush()
    from .graph import ensure_keys

    ensure_keys(db, domain_id)
    db.commit()
    return node


def update_node(
    db: Session, role: str, user_id: str, node_id: str, *, title: str | None = None, summary: str | None = None,
    base_version: int | None = None,
) -> KnowledgeNode:
    _require_edit(role)
    with _tracked(db, user_id, _domain_of_node(db, node_id), "node.update"):
        return _update_node(db, user_id, node_id, title, summary, base_version)


def _update_node(
    db: Session, user_id: str, node_id: str, title: str | None, summary: str | None, base_version: int | None
) -> KnowledgeNode:
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise TreeError(404, "Node not found.")
    if base_version is not None and node.version != base_version:
        raise TreeError(409, "This node changed after the request was filed.")
    if title is not None:
        node.title = _clean(title, MAX_TITLE, required=True, field="Title")
    if summary is not None:
        node.summary = _clean(summary, MAX_SUMMARY)
    node.version += 1
    node.updated_by = user_id
    node.updated_at = _now()
    db.commit()
    return node


def _descendants(db: Session, node_id: str) -> set[str]:
    out: set[str] = set()
    frontier = [node_id]
    while frontier:
        kids = [
            i for (i,) in db.query(KnowledgeNode.id).filter(KnowledgeNode.parent_id.in_(frontier)).all()
        ]
        kids = [k for k in kids if k not in out]
        out.update(kids)
        frontier = kids
    return out


def move_node(db: Session, role: str, user_id: str, node_id: str, parent_id: str | None) -> KnowledgeNode:
    _require_edit(role)
    with _tracked(db, user_id, _domain_of_node(db, node_id), "node.move"):
        return _move_node(db, user_id, node_id, parent_id)


def _move_node(db: Session, user_id: str, node_id: str, parent_id: str | None) -> KnowledgeNode:
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise TreeError(404, "Node not found.")
    _parent_ok(db, node.domain_id, parent_id)
    if parent_id is not None and (parent_id == node_id or parent_id in _descendants(db, node_id)):
        raise TreeError(400, "A node cannot be moved under itself.")
    node.parent_id = parent_id
    node.position = _next_position(db, node.domain_id, parent_id)
    node.version += 1
    node.updated_by = user_id
    node.updated_at = _now()
    db.commit()
    return node


def delete_node(db: Session, role: str, node_id: str, user_id: str = "") -> int:
    _require_edit(role)
    with _tracked(db, user_id, _domain_of_node(db, node_id), "node.delete"):
        return _delete_node(db, node_id)


def _delete_node(db: Session, node_id: str) -> int:
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise TreeError(404, "Node not found.")
    ids = _descendants(db, node_id) | {node_id}
    from .graph import drop_edges_of

    drop_edges_of(db, ids)
    db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return len(ids)


# ── build from a raw file ─────────────────────────────────────────────────


def import_file(
    db: Session, role: str, user_id: str, *, domain_id: str | None, new_domain_name: str | None,
    filename: str, data: bytes,
) -> dict:
    _require_edit(role)
    try:
        text = ingest.extract_text(filename, data)
    except ingest.IngestError as exc:
        raise TreeError(400, str(exc)) from exc
    if domain_id and db.get(KnowledgeDomain, domain_id) is None:
        raise TreeError(404, "Domain not found.")
    with _tracked(db, user_id, domain_id, "import", summary=f"Imported “{filename[:200]}”") as box:
        result = _import_file(db, user_id, domain_id, new_domain_name, filename, data, text)
        box["domain_id"] = result["domain_id"]
    return result


def _import_file(
    db: Session, user_id: str, domain_id: str | None, new_domain_name: str | None,
    filename: str, data: bytes, text: str,
) -> dict:
    if domain_id:
        domain = db.get(KnowledgeDomain, domain_id)
    else:
        domain = _create_domain(db, user_id, new_domain_name or filename.rsplit(".", 1)[0], "")
    source = KnowledgeSource(
        domain_id=domain.id,
        filename=_clean(filename, 255) or "file",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        text=text,
        uploaded_by=user_id,
    )
    db.add(source)
    db.flush()
    from .store import store_original

    store_original(source, filename, data, db)
    from pathmind.services import audit

    audit.record(
        db, user_id, "store.document.upload", target_type="document", target_id=source.id,
        data={"filename": source.filename, "size": source.size, "domain_id": domain.id, "built_tree": True},
        commit=False,
    )
    from .document_sync import apply_directives, parse_directives

    stack: list[tuple[int, str]] = []  # (level, node id)
    created = 0
    declared: list = []
    positions: dict[str | None, int] = {}
    for sec in ingest.build_outline(text):
        plain, directives = parse_directives(text[sec.start : sec.end])
        while stack and stack[-1][0] >= sec.level:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        if parent_id not in positions:
            positions[parent_id] = _next_position(db, domain.id, parent_id) if parent_id is None else 0
        node = KnowledgeNode(
            domain_id=domain.id,
            parent_id=parent_id,
            title=sec.title,
            summary=_clean(" ".join(plain.split())[:400], MAX_SUMMARY),
            position=positions[parent_id],
            refs=[{"source_id": source.id, "start": sec.start, "end": sec.end}],
            updated_by=user_id,
        )
        positions[parent_id] += 1
        db.add(node)
        db.flush()
        stack.append((sec.level, node.id))
        declared.append((node, directives))
        created += 1
    from .graph import ensure_keys

    for node, d in declared:
        node.created_by = user_id
        if d["id"]:
            try:
                from .graph import clean_key

                key = clean_key(d["id"])
                if not db.query(KnowledgeNode.id).filter(KnowledgeNode.domain_id == domain.id,
                                                         KnowledgeNode.key == key).first():
                    node.key = key
            except TreeError:
                pass
    db.flush()
    ensure_keys(db, domain.id)
    db.flush()
    warnings = apply_directives(db, user_id, domain.id, declared)
    domain.updated_at = _now()
    db.commit()
    return {"domain_id": domain.id, "source_id": source.id, "nodes_created": created, "warnings": warnings}
