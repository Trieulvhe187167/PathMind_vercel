"""Knowledge tree (main release): admin-only edits, scope ranking, unified tree + GraphRAG retrieval."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.services import knowledge_tree as kt
from pathmind.services.knowledge_tree.unified import knowledge_search

DOC = (
    "# Algebra\nIntro to algebra.\n## Linear equations\nSolve ax+b=0.\n"
    "## Quadratics\nUse the quadratic formula.\n# Geometry\n## Triangles\nAngles sum to 180.\n"
).encode()


@pytest.fixture()
def factory(monkeypatch):
    import pathmind.database.connection as connection

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(connection, "SessionLocal", f)
    monkeypatch.setattr(kt.service, "admin_kb_names", lambda: ["maths-kb", "other-kb"])
    monkeypatch.setattr(kt, "admin_kb_names", lambda: ["maths-kb", "other-kb"])
    monkeypatch.setattr(
        kt.service, "linkable_kbs",
        lambda: [{"ref": n, "name": n, "owner_id": "", "owner": "admin"} for n in ("maths-kb", "other-kb")],
    )
    return f


def _build(f, kb=None):
    db = f()
    out = kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="Maths", filename="m.md", data=DOC)
    if kb:
        kt.update_domain(db, "rnd", out["domain_id"], kb_name=kb, set_kb=True)
    return db, out["domain_id"]


def test_only_rnd_edits(factory):
    db = factory()
    for role in ("user", "educator", "admin", "wizard"):
        with pytest.raises(kt.TreeError) as e:
            kt.create_domain(db, role, "u", "X")
        assert e.value.status == 403
    assert kt.create_domain(db, "rnd", "r", "Y").id
    assert kt.EDIT_ROLES == frozenset({"rnd"})
    assert kt.VIEW_ROLES == frozenset({"rnd", "admin"})


def test_import_and_scope_ranking(factory):
    db, did = _build(factory)
    hits = kt.find_scope(db, "how do I solve linear equations")
    assert hits[0]["title"] == "Linear equations"
    assert hits[0]["path"] == ["Algebra"]
    assert hits[0]["domain"] == "Maths"
    assert kt.find_scope(db, "zzzz nothing") == []
    assert kt.find_scope(db, "triangles", domain_id=did)[0]["title"] == "Triangles"


def test_kb_link_is_validated_and_clearable(factory):
    db, did = _build(factory)
    with pytest.raises(kt.TreeError) as e:
        kt.update_domain(db, "rnd", did, kb_name="ghost", set_kb=True)
    assert e.value.status == 400
    with pytest.raises(kt.TreeError):
        kt.update_domain(db, "user", did, kb_name="maths-kb", set_kb=True)
    assert kt.update_domain(db, "rnd", did, kb_name="maths-kb", set_kb=True).kb_name == "maths-kb"
    assert kt.domain_summaries(db)[0]["kb_name"] == "maths-kb"
    assert kt.update_domain(db, "rnd", did, kb_name="", set_kb=True).kb_name is None


def test_unified_merges_tree_and_graph(factory):
    _build(factory, kb="maths-kb")
    calls = []

    async def graph(*, query, kb_name, event_sink=None):
        calls.append((kb_name, query))
        return {"answer": "GRAPH ANSWER", "sources": [{"file": "book.pdf"}]}

    out = asyncio.run(knowledge_search("solve linear equations", graph_search=graph))
    assert calls and calls[0][0] == "maths-kb"
    assert "Linear equations" in calls[0][1]  # scope hints reach GraphRAG
    assert "## Curated knowledge graph" in out["content"]
    assert "Algebra > Linear equations" in out["content"]
    assert "ax+b=0" in out["content"]
    assert "## GraphRAG (maths-kb)" in out["content"] and "GRAPH ANSWER" in out["content"]
    types = {s["type"] for s in out["sources"]}
    assert types == {"knowledge_tree", "rag"}
    assert out["notes"] == []


def test_unified_degrades_when_graph_fails(factory):
    _build(factory, kb="maths-kb")

    async def boom(**_):
        raise RuntimeError("index missing")

    out = asyncio.run(knowledge_search("quadratics formula", graph_search=boom))
    assert "Quadratics" in out["content"]
    assert any("unavailable" in n for n in out["notes"])

    async def err(**_):
        return {"error_type": "needs_reindex"}

    out = asyncio.run(knowledge_search("quadratics formula", graph_search=err))
    assert any("could not answer" in n for n in out["notes"])


def test_unified_without_link_or_match(factory):
    _build(factory)  # no KB linked

    async def graph(**_):  # must not be called
        raise AssertionError("no KB is linked")

    out = asyncio.run(knowledge_search("triangles", graph_search=graph))
    assert "Triangles" in out["content"]
    assert any("No GraphRAG" in n for n in out["notes"])


def test_unified_graph_only_when_tree_has_no_hit(factory):
    _build(factory, kb="maths-kb")

    async def graph(*, query, kb_name, event_sink=None):
        assert "Focus on" not in query
        return {"answer": "only graph"}

    out = asyncio.run(knowledge_search("unrelated zebra question", graph_search=graph))
    assert "only graph" in out["content"]
    assert any("No curated knowledge-graph node" in n for n in out["notes"])


def test_empty_query_rejected(factory):
    with pytest.raises(ValueError):
        asyncio.run(knowledge_search("  "))


def test_has_content_and_mount_flag(factory):
    from pathmind.agents._shared import tool_composition as tc

    assert tc.user_has_knowledge_graph() is False
    _build(factory)
    assert tc.user_has_knowledge_graph() is True
    assert tc.user_has_knowledge_tree() is True  # backward-compatible alias
    assert tc._CONDITIONAL_MOUNT_FLAGS["knowledge_search"] == "has_knowledge_graph"
    assert tc._CONDITIONAL_MOUNT_FLAGS["knowledge_graph"] == "has_knowledge_graph"


def test_tools_registered():
    from pathmind.tools.builtin import BUILTIN_TOOL_NAMES

    from pathmind.tools.builtin_specs import TOOL_ALIASES

    assert "knowledge_graph" in BUILTIN_TOOL_NAMES and "knowledge_search" in BUILTIN_TOOL_NAMES
    assert TOOL_ALIASES["knowledge_tree"][0] == "knowledge_graph"


# ── API ────────────────────────────────────────────────────────────────────


def _client(factory, uid, role):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pathmind.api.routers import knowledge_tree as router_mod
    from pathmind.database.connection import get_db
    from pathmind.multi_user.context import set_current_user, user_from_token_payload

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/knowledge-tree")

    def _db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    set_current_user(user_from_token_payload(SimpleNamespace(user_id=uid, username=uid, role=role)))
    return TestClient(app)


def test_api_rnd_builds_admin_reads_others_blind(factory):
    rnd = _client(factory, "r1", "rnd")
    assert rnd.get("/api/knowledge-tree/me").json()["can_edit"] is True
    up = rnd.post("/api/knowledge-tree/domains/import", files={"file": ("m.md", DOC, "text/markdown")}, data={"name": "Maths"})
    assert up.status_code == 200 and up.json()["nodes_created"] == 5
    did = up.json()["domain_id"]
    tree = rnd.get(f"/api/knowledge-tree/domains/{did}/tree").json()
    nid = tree["nodes"][0]["id"]
    # Administrators see the tree, its documents and its history, but cannot edit it.
    admin = _client(factory, "a1", "admin")
    me = admin.get("/api/knowledge-tree/me").json()
    assert me["can_edit"] is False and me["can_view"] is True
    assert admin.get("/api/knowledge-tree/domains").json()["domains"][0]["name"] == "Maths"
    assert admin.get("/api/knowledge-tree/search?q=triangle").json()["results"]
    assert admin.get("/api/knowledge-tree/history").status_code == 200
    assert admin.get(f"/api/knowledge-tree/domains/{did}/markdown").status_code == 200
    assert admin.post(f"/api/knowledge-tree/domains/{did}/nodes", json={"title": "x"}).status_code == 403
    assert admin.patch(f"/api/knowledge-tree/nodes/{nid}", json={"title": "x"}).status_code == 403
    assert admin.delete(f"/api/knowledge-tree/domains/{did}").status_code == 403
    assert admin.post("/api/knowledge-tree/sync", json={"domain_id": did, "markdown": "# x"}).status_code == 403
    assert admin.post("/api/knowledge-tree/domains/import", files={"file": ("a.md", DOC, "text/markdown")}).status_code == 403
    # D9: the main tree is hidden from learners and educators (the tutor still uses it)
    for role in ("user", "educator"):
        other = _client(factory, f"{role}1", role)
        assert other.get("/api/knowledge-tree/me").json()["can_edit"] is False
        assert other.get("/api/knowledge-tree/domains").status_code == 403
        assert other.get(f"/api/knowledge-tree/domains/{did}/tree").status_code == 403
        assert other.get(f"/api/knowledge-tree/nodes/{nid}").status_code == 403
        assert other.get("/api/knowledge-tree/search?q=triangle").status_code == 403
    user = _client(factory, "u1", "user")
    # every write / admin-only path is refused for a normal user
    assert user.post("/api/knowledge-tree/domains", json={"name": "x"}).status_code == 403
    assert user.post(f"/api/knowledge-tree/domains/{did}/nodes", json={"title": "x"}).status_code == 403
    assert user.patch(f"/api/knowledge-tree/nodes/{nid}", json={"title": "x"}).status_code == 403
    assert user.delete(f"/api/knowledge-tree/nodes/{nid}").status_code == 403
    assert user.delete(f"/api/knowledge-tree/domains/{did}").status_code == 403
    assert user.patch(f"/api/knowledge-tree/domains/{did}", json={"kb_name": "maths-kb"}).status_code == 403
    assert user.get("/api/knowledge-tree/kb-options").status_code == 403
    assert user.post("/api/knowledge-tree/ask", json={"query": "q"}).status_code == 403
    assert user.post("/api/knowledge-tree/domains/import", files={"file": ("a.md", DOC, "text/markdown")}).status_code == 403
    # R&D can link + edit; bad file refused (user context is per-client: switch back)
    rnd = _client(factory, "r1", "rnd")
    assert rnd.get("/api/knowledge-tree/kb-options").json()["knowledge_bases"] == ["maths-kb", "other-kb"]
    assert rnd.patch(f"/api/knowledge-tree/domains/{did}", json={"kb_name": "maths-kb"}).json()["kb_name"] == "maths-kb"
    assert rnd.patch(f"/api/knowledge-tree/domains/{did}", json={"kb_name": "ghost"}).status_code == 400
    assert rnd.patch(f"/api/knowledge-tree/nodes/{nid}", json={"title": "Algebra I"}).status_code == 200
    bad = rnd.post("/api/knowledge-tree/domains/import", files={"file": ("a.exe", b"x", "application/octet-stream")})
    assert bad.status_code == 400
    assert rnd.delete(f"/api/knowledge-tree/nodes/{nid}").json()["deleted"] >= 1


def test_tools_run(factory):
    from pathmind.services.knowledge_tree.samples import SAMPLE_MARKDOWN
    from pathmind.tools.builtin import KnowledgeGraphTool, KnowledgeSearchTool, KnowledgeTreeTool

    assert KnowledgeTreeTool is KnowledgeGraphTool
    db = factory()
    kt.import_file(db, "rnd", "r1", domain_id=None, new_domain_name="Sample", filename="s.md",
                   data=SAMPLE_MARKDOWN.encode())
    _build(factory)
    tool = KnowledgeGraphTool()

    def run(**kw):
        res = asyncio.run(tool.execute(**kw))
        assert res.success is not False, res.content
        return str(res.content)

    assert "Maths" in run(action="domains")
    schema = run(action="schema")
    assert "Category" in schema and "principle" not in schema and "TYPE_OF" in schema
    dom = next(d for d in kt.domain_summaries(db) if d["name"] == "Maths")
    assert "[Concept]" in run(action="outline", domain_id=dom["id"])
    hit = kt.search_nodes(db, "Triangles")[0]
    node = run(action="node", node_id=hit["id"])
    assert "Type:" in node and "Layer:" not in node and "PART_OF" in node
    assert run(action="read", node_id=hit["id"]) == node  # old action name
    assert "-[PART_OF]->" in run(action="neighbors", node_id=hit["id"])
    assert "(none)" in run(action="neighbors", node_id=hit["id"], relation="PREREQUISITE_OF")
    assert "Triangles" in run(action="search", query="triangles")
    bad = asyncio.run(tool.execute(action="nope"))
    assert bad.success is False
    res = asyncio.run(KnowledgeSearchTool().execute(query="triangles"))
    assert "Triangles" in str(res.content) and "## Graph facts" in str(res.content)


def test_linked_kb_reference_points_at_the_right_library(monkeypatch, tmp_path):
    from pathmind.multi_user import knowledge_access, paths

    monkeypatch.setattr(knowledge_access, "admin_kb_base_dir", lambda: tmp_path / "admin-kbs")
    base, name = kt.service.resolve_kb_ref("maths-kb")
    assert (base, name) == (str(tmp_path / "admin-kbs"), "maths-kb")  # plain name = admin library
    monkeypatch.setattr(paths, "scope_for_user", lambda uid, is_admin: uid)

    class _Paths:
        def __init__(self, uid):
            self.uid = uid

        def get_knowledge_bases_root(self):
            return tmp_path / self.uid / "kbs"

    monkeypatch.setattr(paths, "get_path_service_for_scope", _Paths)
    base, name = kt.service.resolve_kb_ref("u_rnd1/graph-kb")
    assert (base, name) == (str(tmp_path / "u_rnd1" / "kbs"), "graph-kb")  # owner/name = that R&D's library
