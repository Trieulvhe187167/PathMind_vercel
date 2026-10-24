"""The main knowledge graph: schema, node types and typed relations.

Nodes keep their outline placement (``parent_id``, read as an implicit
``PART_OF`` edge) and gain a ``label`` (node type), free ``properties`` and an
optional ``layer`` (a free grouping, empty unless R&D sets one).
Every other relation is an edge with a type from the schema and its own
properties.

R&D declares the schema (labels and relation types); defaults are seeded on
first use. Every change is recorded in the edit history (schema changes under
the pseudo-domain ``SCHEMA_SCOPE``) and can be reverted.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from pathmind.database.models import (
    KnowledgeDomain,
    KnowledgeEdge,
    KnowledgeLabel,
    KnowledgeNode,
    KnowledgeRelationType,
)

from .service import TreeError, _clean, _require_edit, _require_view, _tracked

SCHEMA_SCOPE = "__schema__"
IMPLICIT_RELATION = "PART_OF"  # child -PART_OF-> parent, from the outline
DEFAULT_LABEL = "Concept"
MAX_PROPERTIES = 30
MAX_EDGES_PER_DOMAIN = 20000

DEFAULT_LABELS = [
    ("Category", "#f5c84c", "A broad grouping of other nodes."),
    ("Type", "#c9b48f", "A kind of thing; other nodes point to it with TYPE_OF."),
    ("Topic", "#7fb3d5", "A subject area of the curriculum."),
    ("Concept", "#e46c6c", "An idea the learner should understand."),
    ("Lesson", "#9b7fd4", "A unit of teaching."),
    ("Exercise", "#5fb38a", "A task that practises concepts."),
    ("Example", "#e6935a", "A worked example of a concept."),
    ("Document", "#8a9bb0", "A source document."),
]
DEFAULT_RELATIONS = [
    ("TYPE_OF", "The source is a kind of the target (Apple TYPE_OF Fruit)."),
    ("PART_OF", "The source belongs inside the target (the outline uses this)."),
    ("PREREQUISITE_OF", "The source must be learned before the target."),
    ("RELATED_TO", "The two are related; say how in the properties."),
    ("EXPLAINS", "The source explains the target."),
    ("EXAMPLE_OF", "The source is an example of the target."),
    ("USES", "The source uses or applies the target."),
]

_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]{0,59}$")
_REL_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,59}$")
_PROP_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ .-]{0,59}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
_LAYER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,11}$")
MAX_NAMES = 20


# ── schema ───────────────────────────────────────────────────────────────────


def ensure_schema(db: Session) -> None:
    """Seed the default labels and relation types once (when both are empty)."""
    if db.query(KnowledgeLabel.id).first() or db.query(KnowledgeRelationType.id).first():
        return
    for name, color, desc in DEFAULT_LABELS:
        db.add(KnowledgeLabel(name=name, color=color, description=desc))
    for name, desc in DEFAULT_RELATIONS:
        db.add(KnowledgeRelationType(name=name, description=desc))
    db.commit()


def _label_view(x: KnowledgeLabel) -> dict:
    return {"id": x.id, "name": x.name, "color": x.color, "description": x.description}


def _rel_view(x: KnowledgeRelationType) -> dict:
    return {"id": x.id, "name": x.name, "description": x.description}


def schema(db: Session, role: str | None = None) -> dict:
    if role is not None:
        _require_view(role)
    ensure_schema(db)
    labels = db.query(KnowledgeLabel).order_by(KnowledgeLabel.name).all()
    rels = db.query(KnowledgeRelationType).order_by(KnowledgeRelationType.name).all()
    return {
        "labels": [_label_view(x) for x in labels],
        "relations": [_rel_view(x) for x in rels],
        "implicit_relation": IMPLICIT_RELATION,
    }


def _label_names(db: Session) -> dict[str, KnowledgeLabel]:
    ensure_schema(db)
    return {x.name.lower(): x for x in db.query(KnowledgeLabel).all()}


def _relation_names(db: Session) -> dict[str, KnowledgeRelationType]:
    ensure_schema(db)
    return {x.name: x for x in db.query(KnowledgeRelationType).all()}


def save_label(
    db: Session, role: str, user_id: str, *, label_id: str | None, name: str,
    color: str = "", description: str = "",
) -> dict:
    _require_edit(role)
    name = _clean(name, 60, required=True, field="Name")
    if not _LABEL_RE.match(name):
        raise TreeError(400, "A label starts with a letter and uses letters, digits, spaces or _.")
    color = _clean(color, 16)
    if color and not re.fullmatch(r"#[0-9a-fA-F]{3,8}", color):
        raise TreeError(400, "Colour must look like #a1b2c3.")
    names = _label_names(db)
    clash = names.get(name.lower())
    with _tracked(db, user_id, SCHEMA_SCOPE, "schema.label", summary=f"Label “{name}”"):
        if label_id:
            row = db.get(KnowledgeLabel, label_id)
            if row is None:
                raise TreeError(404, "Label not found.")
            if clash is not None and clash.id != row.id:
                raise TreeError(409, "Another label has this name.")
            if row.name != name:  # rename in place: nodes follow
                db.query(KnowledgeNode).filter(KnowledgeNode.label == row.name).update(
                    {KnowledgeNode.label: name}, synchronize_session=False
                )
        else:
            if clash is not None:
                raise TreeError(409, "This label already exists.")
            row = KnowledgeLabel()
            db.add(row)
        row.name, row.color, row.description = name, color, _clean(description, 1000)
        db.commit()
    return _label_view(row)


def delete_label(db: Session, role: str, user_id: str, label_id: str) -> None:
    _require_edit(role)
    row = db.get(KnowledgeLabel, label_id)
    if row is None:
        raise TreeError(404, "Label not found.")
    used = db.query(KnowledgeNode.id).filter(KnowledgeNode.label == row.name).count()
    if used:
        raise TreeError(409, f"{used} node(s) still use this label. Relabel them first.")
    with _tracked(db, user_id, SCHEMA_SCOPE, "schema.label.delete", summary=f"Removed label “{row.name}”"):
        db.delete(row)
        db.commit()


def save_relation(
    db: Session, role: str, user_id: str, *, relation_id: str | None, name: str, description: str = "",
) -> dict:
    _require_edit(role)
    name = _clean(name, 60, required=True, field="Name").upper().replace(" ", "_")
    if not _REL_RE.match(name):
        raise TreeError(400, "A relation type is UPPER_CASE letters, digits and _ (e.g. TYPE_OF).")
    names = _relation_names(db)
    clash = names.get(name)
    with _tracked(db, user_id, SCHEMA_SCOPE, "schema.relation", summary=f"Relation {name}"):
        if relation_id:
            row = db.get(KnowledgeRelationType, relation_id)
            if row is None:
                raise TreeError(404, "Relation type not found.")
            if clash is not None and clash.id != row.id:
                raise TreeError(409, "Another relation type has this name.")
            if row.name == IMPLICIT_RELATION and name != IMPLICIT_RELATION:
                raise TreeError(400, f"{IMPLICIT_RELATION} is used by the outline and cannot be renamed.")
            if row.name != name:
                db.query(KnowledgeEdge).filter(KnowledgeEdge.rel_type == row.name).update(
                    {KnowledgeEdge.rel_type: name}, synchronize_session=False
                )
        else:
            if clash is not None:
                raise TreeError(409, "This relation type already exists.")
            row = KnowledgeRelationType()
            db.add(row)
        row.name, row.description = name, _clean(description, 1000)
        db.commit()
    return _rel_view(row)


def delete_relation(db: Session, role: str, user_id: str, relation_id: str) -> None:
    _require_edit(role)
    row = db.get(KnowledgeRelationType, relation_id)
    if row is None:
        raise TreeError(404, "Relation type not found.")
    if row.name == IMPLICIT_RELATION:
        raise TreeError(400, f"{IMPLICIT_RELATION} is used by the outline and cannot be removed.")
    used = db.query(KnowledgeEdge.id).filter(KnowledgeEdge.rel_type == row.name).count()
    if used:
        raise TreeError(409, f"{used} edge(s) still use this relation type. Remove them first.")
    with _tracked(db, user_id, SCHEMA_SCOPE, "schema.relation.delete", summary=f"Removed relation {row.name}"):
        db.delete(row)
        db.commit()


# ── nodes ────────────────────────────────────────────────────────────────────


def clean_properties(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TreeError(400, "Properties must be an object of key: value pairs.")
    if len(raw) > MAX_PROPERTIES:
        raise TreeError(400, f"At most {MAX_PROPERTIES} properties.")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        key = str(key).strip()
        if not _PROP_KEY_RE.match(key):
            raise TreeError(400, f"Property name “{key[:40]}” is not allowed.")
        if isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)[:1000]
    return out


# ── entity fields: readable id, names per language, description, provenance ──


def slug_key(text: str) -> str:
    """``Phương trình bậc nhất`` -> ``phuong-trinh-bac-nhat``."""
    folded = unicodedata.normalize("NFKD", (text or "").replace("đ", "d").replace("Đ", "D"))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:60] or "node"


def clean_key(raw: Any) -> str:
    key = str(raw or "").strip().lstrip("#").lower()
    if not _KEY_RE.match(key):
        raise TreeError(400, "An id uses lowercase letters, digits, '.', '_' or '-' (e.g. linear-equations).")
    return key


def ensure_keys(db: Session, domain_id: str) -> None:
    """Give every node of the domain a readable id that is unique inside it (caller commits)."""
    nodes = db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain_id).all()
    taken = {n.key for n in nodes if n.key}
    for n in sorted(nodes, key=lambda x: (x.position, x.title)):
        if n.key:
            continue
        base = slug_key(n.title)
        key, i = base, 2
        while key in taken:
            key, i = f"{base}-{i}", i + 1
        n.key = key
        taken.add(key)


def clean_names(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_NAMES:
        raise TreeError(400, f"Names are up to {MAX_NAMES} pairs of language code: name.")
    out = {}
    for lang, name in raw.items():
        lang = str(lang).strip()
        if not _LANG_RE.match(lang):
            raise TreeError(400, f"{lang[:12]!r} is not a language code (vi, en, zh-Hant...).")
        if str(name or "").strip():
            out[lang] = str(name).strip()[:200]
    return out


def clean_provenance(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TreeError(400, "Provenance must be an object.")
    out: dict[str, Any] = {}
    if str(raw.get("source") or "").strip():
        out["source"] = str(raw["source"]).strip()[:500]
    if raw.get("confidence") not in (None, ""):
        try:
            conf = float(raw["confidence"])
        except (TypeError, ValueError):
            raise TreeError(400, "Confidence is a number between 0 and 1.") from None
        if not 0 <= conf <= 1:
            raise TreeError(400, "Confidence is a number between 0 and 1.")
        out["confidence"] = round(conf, 3)
    if str(raw.get("note") or "").strip():
        out["note"] = str(raw["note"]).strip()[:500]
    return out


def clean_layer(raw: Any) -> str | None:
    """Optional free grouping of a node; empty clears it."""
    value = str(raw or "").strip()
    if not value:
        return None
    if not _LAYER_RE.match(value):
        raise TreeError(400, "A layer is up to 12 letters, digits, spaces, '_' or '-'.")
    return value


def node_label(n: KnowledgeNode) -> str:
    return n.label or DEFAULT_LABEL


def set_node_graph(
    db: Session, role: str, user_id: str, node_id: str, *, label: str | None = None,
    properties: dict | None = None, key: str | None = None, layer: str | None = None,
    names: dict | None = None, description: str | None = None, provenance: dict | None = None,
) -> KnowledgeNode:
    """Change a node's graph fields; ``None`` leaves a field as it is."""
    _require_edit(role)
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise TreeError(404, "Node not found.")
    if key is not None:
        key = clean_key(key)
        clash = db.query(KnowledgeNode.id).filter(
            KnowledgeNode.domain_id == node.domain_id, KnowledgeNode.key == key, KnowledgeNode.id != node.id
        ).first()
        if clash:
            raise TreeError(409, f"Another node in this domain already has the id {key}.")
    with _tracked(db, user_id, node.domain_id, "node.update", summary=f"Edited “{node.title}”"):
        _apply_node_graph(db, node, label, properties)
        if key is not None:
            node.key = key
        if layer is not None:
            node.layer = clean_layer(layer)
        if names is not None:
            node.names = clean_names(names)
        if description is not None:
            node.description = _clean(description, 1000) or None
        if provenance is not None:
            node.provenance = clean_provenance(provenance)
        node.version += 1
        node.updated_by = user_id
        db.commit()
    return node


def _apply_node_graph(
    db: Session, node: KnowledgeNode, label: str | None, properties: dict | None
) -> None:
    if label is not None:
        if not label.strip():
            node.label = None
        else:
            found = _label_names(db).get(label.strip().lower())
            if found is None:
                raise TreeError(400, f"Unknown label “{label[:60]}”. Add it to the schema first.")
            node.label = found.name
    if properties is not None:
        node.properties = clean_properties(properties)


# ── edges ────────────────────────────────────────────────────────────────────


def _edge_view(e: KnowledgeEdge, titles: dict[str, str] | None = None) -> dict:
    d = {
        "id": e.id,
        "source_id": e.source_id,
        "target_id": e.target_id,
        "type": e.rel_type,
        "properties": e.properties or {},
        "implicit": False,
    }
    if titles is not None:
        d["source_title"] = titles.get(e.source_id, "")
        d["target_title"] = titles.get(e.target_id, "")
    return d


def _check_edge(db: Session, source_id: str, target_id: str, rel_type: str) -> tuple[KnowledgeNode, str]:
    source, target = db.get(KnowledgeNode, source_id), db.get(KnowledgeNode, target_id)
    if source is None or target is None:
        raise TreeError(404, "Node not found.")
    if source.domain_id != target.domain_id:
        raise TreeError(400, "Both nodes must be in the same domain.")
    if source_id == target_id:
        raise TreeError(400, "A node cannot relate to itself.")
    rel = _clean(rel_type, 60, required=True, field="Relation").upper().replace(" ", "_")
    if rel not in _relation_names(db):
        raise TreeError(400, f"Unknown relation type {rel}. Add it to the schema first.")
    if rel == IMPLICIT_RELATION and source.parent_id == target_id:
        raise TreeError(409, "The outline already places this node inside that one.")
    return source, rel


def add_edge(
    db: Session, role: str, user_id: str, *, source_id: str, target_id: str, rel_type: str,
    properties: dict | None = None,
) -> dict:
    _require_edit(role)
    source, rel = _check_edge(db, source_id, target_id, rel_type)
    dup = db.query(KnowledgeEdge.id).filter_by(source_id=source_id, target_id=target_id, rel_type=rel).first()
    if dup:
        raise TreeError(409, "This relation already exists.")
    if db.query(KnowledgeEdge.id).filter(KnowledgeEdge.domain_id == source.domain_id).count() >= MAX_EDGES_PER_DOMAIN:
        raise TreeError(400, "This domain has too many relations.")
    with _tracked(db, user_id, source.domain_id, "edge.create"):
        edge = KnowledgeEdge(domain_id=source.domain_id, source_id=source_id, target_id=target_id,
                             rel_type=rel, properties=clean_properties(properties), created_by=user_id)
        db.add(edge)
        db.commit()
    return _edge_view(edge)


def update_edge(
    db: Session, role: str, user_id: str, edge_id: str, *, rel_type: str | None = None,
    properties: dict | None = None,
) -> dict:
    _require_edit(role)
    edge = db.get(KnowledgeEdge, edge_id)
    if edge is None:
        raise TreeError(404, "Relation not found.")
    rel = edge.rel_type
    if rel_type is not None:
        _, rel = _check_edge(db, edge.source_id, edge.target_id, rel_type)
    with _tracked(db, user_id, edge.domain_id, "edge.update"):
        edge.rel_type = rel
        if properties is not None:
            edge.properties = clean_properties(properties)
        db.commit()
    return _edge_view(edge)


def delete_edge(db: Session, role: str, user_id: str, edge_id: str) -> None:
    _require_edit(role)
    edge = db.get(KnowledgeEdge, edge_id)
    if edge is None:
        raise TreeError(404, "Relation not found.")
    with _tracked(db, user_id, edge.domain_id, "edge.delete"):
        db.delete(edge)
        db.commit()


def drop_edges_of(db: Session, node_ids: set[str]) -> int:
    """Remove the edges touching deleted nodes (caller commits)."""
    if not node_ids:
        return 0
    return (
        db.query(KnowledgeEdge)
        .filter(or_(KnowledgeEdge.source_id.in_(node_ids), KnowledgeEdge.target_id.in_(node_ids)))
        .delete(synchronize_session=False)
    )


# ── reading ──────────────────────────────────────────────────────────────────


def entity_fields(n: KnowledgeNode) -> dict:
    """ID, names, description and provenance of a node (shared by every view)."""
    return {
        "key": n.key or "",
        "names": n.names or {},
        "description": n.description or "",
        "provenance": n.provenance or {},
        "created_by": n.created_by or "",
        "created_at": n.created_at.isoformat() + "Z" if n.created_at else None,
        "updated_by": n.updated_by or "",
        "updated_at": n.updated_at.isoformat() + "Z" if n.updated_at else None,
    }


def _node_view(n: KnowledgeNode) -> dict:
    return {
        "id": n.id,
        "parent_id": n.parent_id,
        "title": n.title,
        "summary": n.summary,
        "label": node_label(n),
        "layer": n.layer or "",
        "properties": n.properties or {},
        "position": n.position,
        **entity_fields(n),
    }


def graph_of(db: Session, role: str, domain_id: str) -> dict:
    """Nodes and edges of one domain; outline placement comes back as implicit PART_OF edges."""
    _require_view(role)
    domain = db.get(KnowledgeDomain, domain_id)
    if domain is None:
        raise TreeError(404, "Domain not found.")
    ensure_keys(db, domain_id)
    db.commit()
    nodes = db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain_id).all()
    edges = [_edge_view(e) for e in db.query(KnowledgeEdge).filter(KnowledgeEdge.domain_id == domain_id).all()]
    for n in nodes:
        if n.parent_id:
            edges.append({"id": f"part:{n.id}", "source_id": n.id, "target_id": n.parent_id,
                          "type": IMPLICIT_RELATION, "properties": {}, "implicit": True})
    return {
        "domain": {"id": domain.id, "name": domain.name, "description": domain.description},
        "nodes": [_node_view(n) for n in nodes],
        "edges": edges,
        "schema": schema(db),
    }


def relations_of(db: Session, node_id: str) -> dict:
    """Explicit and implicit relations in and out of one node, with the other side's title."""
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise TreeError(404, "Node not found.")
    rows = (
        db.query(KnowledgeEdge)
        .filter(or_(KnowledgeEdge.source_id == node_id, KnowledgeEdge.target_id == node_id))
        .all()
    )
    other_ids = {e.source_id for e in rows} | {e.target_id for e in rows}
    if node.parent_id:
        other_ids.add(node.parent_id)
    children = db.query(KnowledgeNode).filter(KnowledgeNode.parent_id == node_id).all()
    titles = {n.id: n.title for n in db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(other_ids)).all()}
    titles[node_id] = node.title
    outgoing = [_edge_view(e, titles) for e in rows if e.source_id == node_id]
    incoming = [_edge_view(e, titles) for e in rows if e.target_id == node_id]
    if node.parent_id:
        outgoing.append({"id": f"part:{node.id}", "source_id": node.id, "target_id": node.parent_id,
                         "type": IMPLICIT_RELATION, "properties": {}, "implicit": True,
                         "source_title": node.title, "target_title": titles.get(node.parent_id, "")})
    for c in children:
        incoming.append({"id": f"part:{c.id}", "source_id": c.id, "target_id": node.id,
                         "type": IMPLICIT_RELATION, "properties": {}, "implicit": True,
                         "source_title": c.title, "target_title": node.title})
    return {"outgoing": outgoing, "incoming": incoming}


def _fmt_props(props: dict | None) -> str:
    if not props:
        return ""
    return " {" + ", ".join(f"{k}: {v}" for k, v in props.items()) + "}"


def node_ref(n: KnowledgeNode) -> str:
    """``(Apple:Product {colour: red})`` — how the LLM sees a node."""
    return f"({n.title}:{node_label(n)}{_fmt_props(n.properties)})"


def triples(db: Session, node_ids: set[str], *, limit: int = 80) -> list[str]:
    """Graph facts around ``node_ids`` as ``(a:L) -[REL {p}]-> (b:L)`` lines, for the LLM."""
    if not node_ids:
        return []
    rows = (
        db.query(KnowledgeEdge)
        .filter(or_(KnowledgeEdge.source_id.in_(node_ids), KnowledgeEdge.target_id.in_(node_ids)))
        .limit(limit)
        .all()
    )
    ids = set(node_ids) | {e.source_id for e in rows} | {e.target_id for e in rows}
    nodes = {n.id: n for n in db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(ids)).all()}
    parents = {n.parent_id for n in nodes.values() if n.id in node_ids and n.parent_id}
    for p in db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(parents - set(nodes))).all():
        nodes[p.id] = p
    out: list[str] = []
    for e in rows:
        a, b = nodes.get(e.source_id), nodes.get(e.target_id)
        if a and b:
            out.append(f"{node_ref(a)} -[{e.rel_type}{_fmt_props(e.properties)}]-> {node_ref(b)}")
    for nid in node_ids:
        n = nodes.get(nid)
        if n and n.parent_id and n.parent_id in nodes:
            out.append(f"{node_ref(n)} -[{IMPLICIT_RELATION}]-> {node_ref(nodes[n.parent_id])}")
    return out[:limit]
