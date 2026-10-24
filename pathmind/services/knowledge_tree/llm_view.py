"""Text renderings of the knowledge graph for the tutor's tools.

Nodes read ``(Title:Label {k: v})`` and relations ``(a:Label) -[REL {p}]-> (b:Label)``
(see ``graph.node_ref`` / ``graph.triples``), so the model gets the graph's
shape - node types and typed edges - rather than a flat page of text.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from pathmind.database.models import KnowledgeEdge, KnowledgeNode

from . import graph, service as svc

MAX_TRIPLES = 60
_DIRECTIVE = re.compile(r"^\s*@(id|name|description|source|confidence|label|layer|prop|rel)\b", re.I)


def clean_excerpt(text: str) -> str:
    """Drop the ``@id/@label/@prop/@rel``... authoring lines; the graph carries that data."""
    kept = [ln for ln in (text or "").splitlines() if not _DIRECTIVE.match(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def domains_text(db: Session) -> str:
    rows = svc.domain_summaries(db)
    return "\n".join(
        f"- {d['name']} [{d['id']}] ({d['nodes']} nodes) {(d['description'] or '')[:120]}" for d in rows
    ) or "(no domains yet)"


def schema_text(db: Session) -> str:
    s = graph.schema(db)
    lines = ["Node labels (node types):"]
    for x in s["labels"]:
        lines.append(f"- {x['name']}: {x['description']}".rstrip(": "))
    lines.append("")
    lines.append(f"Relation types (read as (a:Label) -[REL {{props}}]-> (b:Label); the outline is {s['implicit_relation']}):")
    for x in s["relations"]:
        lines.append(f"- {x['name']}: {x['description']}".rstrip(": "))
    return "\n".join(lines)


def outline_graph_text(db: Session, domain_id: str, limit: int = 200) -> str:
    data = svc.tree_of(db, domain_id)
    lines = [f"{data['domain']['name']} ({data['domain']['slug']})"]

    def walk(items: list[dict], depth: int) -> None:
        for item in items:
            if len(lines) >= limit:
                return
            lines.append(f"{'  ' * depth}- {item['title']} [{item['label']}] [{item['id']}]")
            walk(item["children"], depth + 1)

    walk(data["nodes"], 0)
    return "\n".join(lines)


def _props(d: dict | None) -> str:
    return graph._fmt_props(d)  # noqa: SLF001 - same package


def neighbor_triples(db: Session, node_id: str, rel: str | None = None, limit: int = MAX_TRIPLES) -> list[str]:
    """One hop around ``node_id`` in both directions, outline included, optionally one relation type."""
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise svc.TreeError(404, "Node not found.")
    rel = (rel or "").strip().upper().replace(" ", "_") or None
    q = db.query(KnowledgeEdge).filter(or_(KnowledgeEdge.source_id == node_id, KnowledgeEdge.target_id == node_id))
    if rel:
        q = q.filter(KnowledgeEdge.rel_type == rel)
    edges = q.limit(limit).all()
    want_part = rel in (None, graph.IMPLICIT_RELATION)
    parent = db.get(KnowledgeNode, node.parent_id) if (want_part and node.parent_id) else None
    children = db.query(KnowledgeNode).filter(KnowledgeNode.parent_id == node_id).all() if want_part else []
    ids = {e.source_id for e in edges} | {e.target_id for e in edges}
    nodes = {n.id: n for n in db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(ids)).all()}
    nodes[node.id] = node
    out = []
    for e in edges:
        a, b = nodes.get(e.source_id), nodes.get(e.target_id)
        if a and b:
            out.append(f"{graph.node_ref(a)} -[{e.rel_type}{_props(e.properties)}]-> {graph.node_ref(b)}")
    if parent is not None:
        out.append(f"{graph.node_ref(node)} -[{graph.IMPLICIT_RELATION}]-> {graph.node_ref(parent)}")
    for c in children:
        out.append(f"{graph.node_ref(c)} -[{graph.IMPLICIT_RELATION}]-> {graph.node_ref(node)}")
    return out[:limit]


def node_text(db: Session, node_id: str, excerpt_chars: int = 3000) -> str:
    d = svc.node_detail(db, node_id, excerpt_chars=excerpt_chars)
    head = f"# {d['title']}\nID: {d['key'] or d['id']}   Type: {d['label']}"
    if d["layer"]:
        head += f"   Layer: {d['layer']}"
    if d["names"]:
        head += "\nNames: " + ", ".join(f"{lang}: {name}" for lang, name in d["names"].items())
    if d["description"]:
        head += "\nDescription: " + d["description"]
    if d["properties"]:
        head += "\nProperties: " + ", ".join(f"{k}: {v}" for k, v in d["properties"].items())
    prov = d["provenance"] or {}
    sources = [ex["filename"] for ex in d["excerpts"]]
    if prov or sources or d["updated_at"]:
        bits = []
        if prov.get("source"):
            bits.append(f"source: {prov['source']}")
        if sources:
            bits.append("documents: " + ", ".join(sources))
        if prov.get("confidence") is not None:
            bits.append(f"confidence: {prov['confidence']}")
        if d["updated_at"]:
            bits.append(f"updated: {d['updated_at'][:10]}")
        head += "\nProvenance: " + "; ".join(bits)
    parts = [head, d["summary"]]
    for ex in d["excerpts"]:
        text = clean_excerpt(ex["text"])
        if text and text != (d["summary"] or "").strip():
            parts.append(f"--- source: {ex['filename']} ---\n{text}")
    rels = neighbor_triples(db, node_id)
    if rels:
        parts.append("Relations:\n" + "\n".join(rels))
    return "\n\n".join(p for p in parts if p)


def neighbors_text(db: Session, node_id: str, rel: str | None = None) -> str:
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise svc.TreeError(404, "Node not found.")
    lines = neighbor_triples(db, node_id, rel)
    title = f"Relations of {graph.node_ref(node)}" + (f" filtered to {rel.upper()}" if rel else "")
    return title + ":\n" + ("\n".join(lines) if lines else "(none)")


def search_text(db: Session, query: str, limit: int = 10) -> str:
    hits = svc.search_nodes(db, query, limit=limit)
    if not hits:
        return "(no matches)"
    lines = [
        f"- {h['title']} [{h['label']}] [{h['id']}] ({h['domain']}): {(h['summary'] or '')[:160]}"
        for h in hits
    ]
    facts = graph.triples(db, {h["id"] for h in hits}, limit=MAX_TRIPLES)
    if facts:
        lines += ["", "Graph facts:", *facts]
    return "\n".join(lines)


ACTION_ALIASES = {"read": "node", "get": "node", "list": "domains", "tree": "outline", "relations": "neighbors"}


def run_action(db: Session, action: str, **kw: Any) -> str:
    action = ACTION_ALIASES.get(action, action)
    if action == "domains":
        return domains_text(db)
    if action == "schema":
        return schema_text(db)
    if action == "outline":
        return outline_graph_text(db, str(kw.get("domain_id") or ""))
    if action == "node":
        return node_text(db, str(kw.get("node_id") or ""))
    if action == "neighbors":
        return neighbors_text(db, str(kw.get("node_id") or ""), str(kw.get("relation") or "") or None)
    if action == "search":
        return search_text(db, str(kw.get("query") or ""))
    raise ValueError(action)
