"""One retrieval call that uses the curated knowledge graph *and* GraphRAG together.

Why both: the knowledge graph is small, authoritative and human-curated (it says *where* a
question lives and carries exact source excerpts); GraphRAG is broad and
semantic (it finds relationships and passages across the whole corpus). The
adapter therefore works in two stages:

1. **Scope** - rank graph nodes against the question (``find_scope``). The hits
   give the topic path(s) and the domain(s) the question belongs to.
2. **Retrieve** - query the GraphRAG knowledge base the administrator linked to
   that domain, nudging the query with the scope's topic names, and merge both
   halves into one answer context with node paths and sources.

If either half is missing (nothing matched in the graph, no KB linked, the KB
call fails) the other still answers and the result says what was skipped.
``graph_search`` is injectable so the merge logic is testable without an index.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pathmind.database.models import KnowledgeDomain

from . import graph as kgraph
from . import service as svc

GraphSearch = Callable[..., Awaitable[dict[str, Any]]]

MAX_KBS = 2
MAX_FACTS = 40


async def _admin_graph_search(*, query: str, kb_name: str, event_sink=None, **kwargs: Any) -> dict[str, Any]:
    """Query a KB linked to a domain (admin library or an R&D account's own library).

    Linking a KB to a domain is R&D's explicit act of publishing it through the
    tutor, so this deliberately does not require the *asking* user to hold a
    per-KB grant.
    """
    from pathmind.services.knowledge_tree.service import resolve_kb_ref
    from pathmind.tools.rag_tool import rag_search

    base_dir, name = resolve_kb_ref(kb_name)
    return await rag_search(query=query, kb_name=name, kb_base_dir=base_dir, event_sink=event_sink, **kwargs)


def _excerpt(db, node_id: str, chars: int) -> tuple[str, str]:
    try:
        detail = svc.node_detail(db, node_id, excerpt_chars=chars)
    except svc.TreeError:
        return "", ""
    first = detail["excerpts"][0] if detail["excerpts"] else None
    if not first:
        return "", ""
    from .llm_view import clean_excerpt

    return clean_excerpt(first["text"]), first["filename"]


async def knowledge_search(
    query: str,
    *,
    domain_id: str | None = None,
    kb_name: str | None = None,
    graph_search: GraphSearch | None = None,
    event_sink=None,
    max_nodes: int = 4,
    excerpt_chars: int = 1200,
) -> dict[str, Any]:
    from pathmind.database.connection import get_db_session

    query = (query or "").strip()
    if not query:
        raise ValueError("knowledge_search needs a non-empty query.")
    graph = graph_search or _admin_graph_search

    with get_db_session() as db:
        hits = svc.find_scope(db, query, domain_id=domain_id, limit=max_nodes)
        tree_blocks: list[str] = []
        tree_sources: list[dict[str, Any]] = []
        for h in hits:
            text, filename = _excerpt(db, h["id"], excerpt_chars)
            trail = " > ".join([h["domain"], *h["path"], h["title"]])
            props = kgraph._fmt_props(h.get("properties"))  # noqa: SLF001
            kind = f"{h.get('label', 'Concept')}{props}"
            block = f"### {trail}\n{kind}\n{h['summary']}".strip()
            if text:
                block += f"\n\n(source excerpt: {filename})\n{text}"
            tree_blocks.append(block)
            tree_sources.append(
                {"type": "knowledge_tree", "title": h["title"], "node_id": h["id"],
                 "label": h.get("label"), "domain": h["domain"], "path": h["path"],
                 "source": filename or h["domain"]}
            )
        facts = kgraph.triples(db, {h["id"] for h in hits}, limit=MAX_FACTS) if hits else []
        # Which GraphRAG knowledge bases to ask: explicit > the asked domain's >
        # the hits' domains' > (nothing matched) every linked domain, capped.
        domains = {d.id: d for d in db.query(KnowledgeDomain).all()}
        kbs: list[str] = []

        def want(name: str | None) -> None:
            if name and name not in kbs and len(kbs) < MAX_KBS:
                kbs.append(name)

        if kb_name:
            want(kb_name.strip())
        elif domain_id and domain_id in domains:
            want(domains[domain_id].kb_name)
        else:
            for h in hits:
                want(domains.get(h["domain_id"]).kb_name if h["domain_id"] in domains else None)
            if not hits:
                for d in sorted(domains.values(), key=lambda d: d.name):
                    want(d.kb_name)
        focus = list(dict.fromkeys(h["title"] for h in hits))[:5]
        # Personal overlay (hidden from the learner): supplements, never overrides.
        personal_blocks: list[str] = []
        personal_sources: list[dict[str, Any]] = []
        try:
            from pathmind.multi_user.context import get_current_user
            from pathmind.services.knowledge_branch import overlay

            uid = str(get_current_user().id or "")
            main_titles = {h["title"].strip().lower() for h in hits}
            for p in overlay.search(db, uid, query, limit=3) if uid else []:
                if p["title"].strip().lower() in main_titles:
                    continue  # main knowledge graph wins on conflict
                personal_blocks.append(f"### {p['title']}\n{p['summary']}".strip())
                personal_sources.append(
                    {"type": "personal_branch", "title": p["title"], "node_id": p["id"],
                     "source": p.get("source_doc") or "personal notes"}
                )
        except Exception:  # noqa: BLE001 - overlay is optional
            pass

    notes: list[str] = []
    graph_results: list[dict[str, Any]] = []
    graph_sources: list[dict[str, Any]] = []
    graph_query = query if not focus else f"{query}\n\nFocus on these topics: {'; '.join(focus)}."
    for name in kbs:
        try:
            result = await graph(query=graph_query, kb_name=name, event_sink=event_sink)
        except Exception as exc:  # noqa: BLE001 - a broken index must not hide the knowledge-graph half
            notes.append(f"GraphRAG '{name}' was unavailable ({type(exc).__name__}).")
            continue
        if result.get("error_type"):
            notes.append(f"GraphRAG '{name}' could not answer ({result.get('error_type')}).")
            continue
        answer = str(result.get("answer") or result.get("content") or "").strip()
        graph_results.append({"kb_name": name, "answer": answer})
        graph_sources.extend(
            {"type": "rag", "kb_name": name, **item}
            for item in (result.get("sources") or []) if isinstance(item, dict)
        )
    if not kbs:
        notes.append("No GraphRAG knowledge base is linked to the matching domain.")
    if not hits:
        notes.append("No curated knowledge-graph node matched the question.")

    parts: list[str] = []
    if tree_blocks:
        parts.append("## Curated knowledge graph\n\n" + "\n\n".join(tree_blocks))
    if facts:
        parts.append("## Graph facts\n\n" + "\n".join(facts))
    if personal_blocks:
        parts.append("## Personal notes (supplementary; the curated knowledge graph wins on conflict)\n\n" + "\n\n".join(personal_blocks))
    for g in graph_results:
        if g["answer"]:
            parts.append(f"## GraphRAG ({g['kb_name']})\n\n{g['answer']}")
    if notes:
        parts.append("_Notes: " + " ".join(notes) + "_")
    return {
        "query": query,
        "content": "\n\n".join(parts) if parts else "Nothing found.",
        "scope": [{k: h[k] for k in ("id", "title", "domain", "path", "score")} for h in hits],
        "graph": graph_results,
        "facts": facts,
        "sources": [*tree_sources, *personal_sources, *graph_sources],
        "notes": notes,
    }
