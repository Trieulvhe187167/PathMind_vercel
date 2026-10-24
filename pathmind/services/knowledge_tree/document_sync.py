"""Edit the main knowledge graph as a document: export a domain to Markdown, sync it back.

R&D can open a domain (or the sample) in Co-Writer, keep writing, and update
the graph from the text. Syncing matches nodes by their heading path (parent
titles + title), so a node that is still in the document keeps its id: the
personal branches anchored to it and the merged recommendations stay valid.
Headings that are new become nodes, headings that disappeared are removed.
Every sync is one entry in the edit history, so it can be reverted.

Graph data is written under a heading with directive lines:

    @id linear-equations        the node's readable id, unique in the domain
    @name vi = Phương trình bậc nhất   its name in another language (one per line)
    @description One paragraph  a short summary of the entity
    @source Textbook p. 12      where the facts come from
    @confidence 0.9             how sure that source is (0 to 1)
    @label Type                 the node's label (from the schema)
    @layer core                 an optional free grouping (empty unless set)
    @prop colour = red          one property per line
    @rel TYPE_OF -> Fruit       an outgoing relation to the node titled "Fruit" (or #fruit by id)
    @rel CONTAINS -> Salmon {qty: 2}
    @rel none                   no outgoing relations

A node is recognised by its @id first, then by its heading path, so renaming or
moving a heading that carries an @id keeps the node.

A heading without a given kind of directive keeps what the node already has,
so text written before the graph existed never wipes it. Exporting writes the
directives back, so a round trip through Co-Writer loses nothing.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy.orm import Session

from pathmind.database.models import KnowledgeDomain, KnowledgeEdge, KnowledgeNode, KnowledgeSource

from . import ingest
from .service import (
    MAX_SUMMARY,
    TreeError,
    _clean,
    _create_domain,
    _now,
    _require_edit,
    _require_view,
    _tracked,
)

MAX_MARKDOWN_CHARS = 2_000_000

_DIRECTIVE = re.compile(
    r"^\s*@(id|name|description|source|confidence|label|layer|prop|rel)\b[ \t]*(.*)$", re.I
)
_REL_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_ ]*?)\s*->\s*(.+?)\s*(\{.*\})?\s*$")


# ── directives ───────────────────────────────────────────────────────────────


def _scalar(raw: str) -> Any:
    v = raw.strip().strip("\"'")
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def _inline_props(raw: str | None) -> dict[str, Any]:
    """``{qty: 2, unit: kg}`` -> ``{"qty": 2, "unit": "kg"}``."""
    out: dict[str, Any] = {}
    body = raw.strip()[1:-1] if raw else ""
    for part in body.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            if k.strip():
                out[k.strip()] = _scalar(v)
    return out


def parse_directives(body: str) -> tuple[str, dict]:
    """Split a section into its plain text and its graph directives."""
    kept: list[str] = []
    d: dict[str, Any] = {
        "id": None, "names": None, "description": None, "source": None, "confidence": None,
        "label": None, "layer": None, "props": None, "rels": None,
    }
    for line in body.split("\n"):
        m = _DIRECTIVE.match(line)
        if not m:
            kept.append(line)
            continue
        kind, rest = m.group(1).lower(), m.group(2).strip()
        if kind in ("id", "description", "source", "confidence", "label", "layer"):
            d[kind] = rest
        elif kind == "name":
            lang, _, value = rest.partition("=")
            if lang.strip() and value.strip():
                d["names"] = {**(d["names"] or {}), lang.strip(): value.strip()}
        elif kind == "prop":
            key, _, value = rest.partition("=")
            if key.strip():
                d["props"] = {**(d["props"] or {}), key.strip(): _scalar(value)}
        else:
            if d["rels"] is None:
                d["rels"] = []
            if rest.lower() != "none":
                r = _REL_LINE.match(rest)
                if r:
                    d["rels"].append((r.group(1).strip(), r.group(2).strip(), _inline_props(r.group(3))))
    return "\n".join(kept).strip(), d


def _directive_lines(node: KnowledgeNode, edges: list[KnowledgeEdge], titles: dict[str, str]) -> list[str]:
    from .graph import _fmt_props

    lines = []
    if node.key:
        lines.append(f"@id {node.key}")
    for lang, name in (node.names or {}).items():
        lines.append(f"@name {lang} = {name}")
    if node.description:
        lines.append(f"@description {' '.join(node.description.split())}")
    prov = node.provenance or {}
    if prov.get("source"):
        lines.append(f"@source {prov['source']}")
    if prov.get("confidence") is not None:
        lines.append(f"@confidence {prov['confidence']}")
    if node.label:
        lines.append(f"@label {node.label}")
    if node.layer:
        lines.append(f"@layer {node.layer}")
    for k, v in (node.properties or {}).items():
        lines.append(f"@prop {k} = {v}")
    for e in edges:
        if e.target_id in titles:  # by id: unambiguous even when two nodes share a title
            lines.append(f"@rel {e.rel_type} -> #{titles[e.target_id]}{_fmt_props(e.properties)}")
    return lines


def apply_directives(
    db: Session, user_id: str, domain_id: str, declared: list[tuple[KnowledgeNode, dict]]
) -> list[str]:
    """Write ids, names, labels, properties and outgoing relations declared in a document.

    Relation targets are found by title in the same domain (case-insensitive).
    Problems come back as warnings; they never stop the sync.
    """
    from . import graph

    warnings: list[str] = []
    labels = graph._label_names(db)
    relations = graph._relation_names(db)
    by_key: dict[str, str] = {}
    by_title: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for n in db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain_id).all():
        if n.key:
            by_key[n.key] = n.id
        by_title.setdefault(n.title.strip().lower(), n.id)
        for name in (n.names or {}).values():
            by_name.setdefault(str(name).strip().lower(), n.id)

    def find(ref: str) -> str | None:
        ref = ref.strip()
        if ref.startswith("#"):
            return by_key.get(ref[1:].lower())
        low = ref.lower()
        return by_key.get(low) or by_title.get(low) or by_name.get(low)

    for node, d in declared:
        if d["names"] is not None:
            try:
                node.names = graph.clean_names(d["names"])
            except TreeError as exc:
                warnings.append(f"“{node.title}”: {exc.message}")
        if d["description"] is not None:
            node.description = _clean(d["description"], 1000) or None
        if d["source"] is not None or d["confidence"] is not None:
            prov = dict(node.provenance or {})
            if d["source"] is not None:
                prov["source"] = d["source"]
            if d["confidence"] is not None:
                prov["confidence"] = d["confidence"]
            try:
                node.provenance = graph.clean_provenance(prov)
            except TreeError as exc:
                warnings.append(f"“{node.title}”: {exc.message}")
        if d["label"] is not None:
            found = labels.get(d["label"].strip().lower())
            if found is None:
                warnings.append(f"“{node.title}”: unknown label {d['label']}")
            else:
                node.label = found.name
        if d["layer"] is not None:
            try:
                node.layer = graph.clean_layer(d["layer"])
            except TreeError as exc:
                warnings.append(f"“{node.title}”: {exc.message}")
        if d["props"] is not None:
            try:
                node.properties = graph.clean_properties(d["props"])
            except TreeError as exc:
                warnings.append(f"“{node.title}”: {exc.message}")
        if d["rels"] is None:
            continue
        db.query(KnowledgeEdge).filter(KnowledgeEdge.source_id == node.id).delete(synchronize_session=False)
        seen: set[tuple[str, str]] = set()
        for rel, target_title, props in d["rels"]:
            rel = rel.upper().replace(" ", "_")
            target = find(target_title)
            if rel not in relations:
                warnings.append(f"“{node.title}”: unknown relation {rel}")
                continue
            if target is None:
                warnings.append(f"“{node.title}”: no node titled “{target_title}”")
                continue
            if target == node.id or (rel, target) in seen:
                continue
            seen.add((rel, target))
            try:
                clean = graph.clean_properties(props)
            except TreeError as exc:
                warnings.append(f"“{node.title}”: {exc.message}")
                clean = {}
            db.add(KnowledgeEdge(domain_id=domain_id, source_id=node.id, target_id=target,
                                 rel_type=rel, properties=clean, created_by=user_id))
    db.flush()
    return warnings[:50]


# ── export ───────────────────────────────────────────────────────────────────


def _children(nodes: list[KnowledgeNode]) -> dict[str | None, list[KnowledgeNode]]:
    by_parent: dict[str | None, list[KnowledgeNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)
    for items in by_parent.values():
        items.sort(key=lambda n: (n.position, n.title))
    return by_parent


def _body(node: KnowledgeNode, sources: dict[str, str]) -> str:
    """The node's section text when it came from a document, else its summary (no directives)."""
    parts = []
    for ref in node.refs or []:
        text = sources.get(str(ref.get("source_id") or ""))
        if text is not None:
            parts.append(parse_directives(text[int(ref.get("start") or 0) : int(ref.get("end") or 0)])[0])
    return "\n\n".join(p for p in parts if p) or (node.summary or "").strip()


def export_markdown(db: Session, role: str, domain_id: str) -> dict:
    """The domain as one Markdown document (``#`` per outline level, deepest is ``####``)."""
    _require_view(role)
    domain = db.get(KnowledgeDomain, domain_id)
    if domain is None:
        raise TreeError(404, "Domain not found.")
    nodes = db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain_id).all()
    sources = {s.id: s.text for s in db.query(KnowledgeSource).filter(KnowledgeSource.domain_id == domain_id).all()}
    from .graph import ensure_keys

    ensure_keys(db, domain_id)
    db.commit()
    by_parent = _children(nodes)
    titles = {n.id: n.key for n in nodes}  # relations are written by id
    out_edges: dict[str, list[KnowledgeEdge]] = {}
    rows = db.query(KnowledgeEdge).filter(KnowledgeEdge.domain_id == domain_id).order_by(KnowledgeEdge.created_at)
    for e in rows.all():
        out_edges.setdefault(e.source_id, []).append(e)
    lines: list[str] = []

    def walk(parent: str | None, depth: int) -> None:
        for n in by_parent.get(parent, []):
            lines.append(f"{'#' * min(depth, 4)} {n.title}")  # the reader knows four levels
            graph_lines = _directive_lines(n, out_edges.get(n.id, []), titles)
            if graph_lines:
                lines.extend(["", *graph_lines])
            body = _body(n, sources)
            if body:
                lines.extend(["", body])
            lines.append("")
            walk(n.id, depth + 1)

    walk(None, 1)
    return {"domain_id": domain.id, "name": domain.name, "markdown": "\n".join(lines).strip() + "\n"}


# ── sync ─────────────────────────────────────────────────────────────────────


def _key(path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in path)


def sync_markdown(
    db: Session, role: str, user_id: str, *, domain_id: str | None, new_domain_name: str | None,
    title: str, markdown: str,
) -> dict:
    """Make the domain's graph match the document (see module docstring)."""
    _require_edit(role)
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if not text.strip():
        raise TreeError(400, "The document is empty.")
    if len(text) > MAX_MARKDOWN_CHARS:
        raise TreeError(400, "The document is too long for one domain.")
    sections = ingest.build_outline(text)
    if domain_id and db.get(KnowledgeDomain, domain_id) is None:
        raise TreeError(404, "Domain not found.")
    label = _clean(title, 200) or "Co-Writer document"
    with _tracked(db, user_id, domain_id, "document.sync", summary=f"Updated from “{label}”",
                  source_text=True) as box:
        result = _sync(db, user_id, domain_id, new_domain_name or label, label, text, sections)
        box["domain_id"] = result["domain_id"]
    return result


def _sync(
    db: Session, user_id: str, domain_id: str | None, new_name: str, label: str, text: str,
    sections: list[ingest.Section],
) -> dict:
    domain = db.get(KnowledgeDomain, domain_id) if domain_id else _create_domain(db, user_id, new_name, "")
    # One source per synced document (same file name): replaced on every sync.
    filename = f"{label[:240]}.md"
    source = (
        db.query(KnowledgeSource)
        .filter(KnowledgeSource.domain_id == domain.id, KnowledgeSource.filename == filename)
        .first()
    )
    data = text.encode("utf-8")
    if source is None:
        source = KnowledgeSource(domain_id=domain.id, filename=filename, uploaded_by=user_id)
        db.add(source)
    source.text, source.size = text, len(data)
    source.sha256 = hashlib.sha256(data).hexdigest()
    db.flush()

    existing = db.query(KnowledgeNode).filter(KnowledgeNode.domain_id == domain.id).all()
    by_parent = _children(existing)
    paths: dict[tuple[str, ...], KnowledgeNode] = {}

    def index(parent: str | None, prefix: tuple[str, ...]) -> None:
        for n in by_parent.get(parent, []):
            key = _key(prefix + (n.title,))
            if key not in paths:  # duplicate sibling titles: the first one keeps its id
                paths[key] = n
                index(n.id, prefix + (n.title,))

    index(None, ())
    by_key = {n.key: n for n in existing if n.key}
    kept: set[str] = set()
    created = updated = 0
    declared: list[tuple[KnowledgeNode, dict]] = []
    warnings_early: list[str] = []
    from .graph import clean_key as graph_clean_key

    stack: list[tuple[int, str, tuple[str, ...]]] = []  # (level, node id, title path)
    positions: dict[str | None, int] = {}
    for sec in sections:
        while stack and stack[-1][0] >= sec.level:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        path = (stack[-1][2] if stack else ()) + (sec.title,)
        pos = positions.get(parent_id, 0)
        positions[parent_id] = pos + 1
        plain, directives = parse_directives(text[sec.start : sec.end])
        summary = _clean(re.sub(r"\s+", " ", plain).strip()[:400], MAX_SUMMARY)
        refs = [{"source_id": source.id, "start": sec.start, "end": sec.end}]
        wanted_key = None
        if directives["id"]:
            try:
                wanted_key = graph_clean_key(directives["id"])
            except TreeError:
                warnings_early.append(f"“{sec.title}”: “{directives['id'][:40]}” is not a valid id")
        node = by_key.get(wanted_key) if wanted_key else None
        if node is None or node.id in kept:
            node = paths.get(_key(path))
        if node is not None and node.id not in kept:
            if (node.parent_id, node.position, node.summary, node.title) != (parent_id, pos, summary, sec.title):
                node.version += 1
                updated += 1
            node.parent_id, node.position, node.title, node.summary = parent_id, pos, sec.title, summary
            node.refs, node.updated_by, node.updated_at = refs, user_id, _now()
        else:
            node = KnowledgeNode(
                domain_id=domain.id, parent_id=parent_id, title=sec.title, summary=summary,
                position=pos, refs=refs, updated_by=user_id, created_by=user_id,
            )
            db.add(node)
            db.flush()
            created += 1
        kept.add(node.id)
        if wanted_key:
            taken = by_key.get(wanted_key)
            if taken is not None and taken.id != node.id:
                warnings_early.append(f"“{sec.title}”: id {wanted_key} is used by “{taken.title}”")
            else:
                node.key = wanted_key
                by_key[wanted_key] = node
        declared.append((node, directives))
        stack.append((sec.level, node.id, path))
    removed = [n for n in existing if n.id not in kept]
    from .graph import drop_edges_of

    drop_edges_of(db, {n.id for n in removed})
    for n in removed:
        db.delete(n)
    db.flush()
    from .graph import ensure_keys

    ensure_keys(db, domain.id)
    db.flush()
    warnings = warnings_early + apply_directives(db, user_id, domain.id, declared)
    domain.updated_at = _now()
    db.commit()
    from .store import store_original

    store_original(source, filename, data, db)
    db.commit()
    return {
        "domain_id": domain.id, "name": domain.name, "created": created, "updated": updated,
        "removed": len(removed), "kept": len(kept) - created, "warnings": warnings,
    }
