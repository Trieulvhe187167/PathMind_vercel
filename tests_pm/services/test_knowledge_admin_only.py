"""Deployment-wide knowledge settings and server-side sources are admin-only."""

from __future__ import annotations

from fastapi import HTTPException
import pytest

ADMIN_ONLY = {
    ("PUT", "/knowledge-bases/rag-providers/{provider}/mode"),
    ("PUT", "/knowledge-bases/rag-pipelines/graphrag/config"),
    ("PUT", "/knowledge-bases/rag-pipelines/lightrag/config"),
    ("PUT", "/knowledge-bases/rag-pipelines/active-model"),
    ("POST", "/knowledge-bases/connect-obsidian"),
    ("POST", "/knowledge-bases/connect-marginnote4"),
    ("POST", "/knowledge-bases/probe-folder"),
    ("POST", "/knowledge-bases/connect-folder"),
    ("POST", "/knowledge-bases/{kb_name}/link-folder"),
    ("POST", "/knowledge-bases/{kb_name}/sync-folder/{folder_id}"),
}

USER_ROUTES = {
    ("GET", "/knowledge-bases"),
    ("POST", "/knowledge-bases"),
    ("POST", "/knowledge-bases/{kb_name}/upload"),
    ("POST", "/knowledge-bases/{kb_name}/web-source"),
    ("POST", "/knowledge-bases/{kb_name}/github-source"),
    ("DELETE", "/knowledge-bases/{kb_name}"),
}


def _routes():
    from pathmind.api.routers import knowledge
    from pathmind.api.routers.auth import require_admin

    found = {}
    for route in knowledge.router.routes:
        deps = {d.dependency for d in getattr(route, "dependencies", [])}
        for method in getattr(route, "methods", set()):
            found[(method, route.path)] = require_admin in deps
    return found


def test_admin_only_routes_require_admin():
    found = _routes()
    missing = [key for key in ADMIN_ONLY if not found.get(key)]
    assert not missing, missing


# D2: GraphRAG and LightRAG are the only engines; the old engines' routes are gone.
REMOVED_ENGINE_ROUTES = {
    ("POST", "/knowledge-bases/probe-lightrag-server"),
    ("POST", "/knowledge-bases/connect-lightrag-server"),
    ("POST", "/knowledge-bases/probe-weknora"),
    ("POST", "/knowledge-bases/connect-weknora"),
    ("POST", "/knowledge-bases/list-ima"),
    ("POST", "/knowledge-bases/probe-ima"),
    ("POST", "/knowledge-bases/connect-ima"),
    ("PUT", "/knowledge-bases/rag-pipelines/llamaindex/config"),
    ("PUT", "/knowledge-bases/rag-pipelines/pageindex/config"),
}


def test_removed_engine_routes_are_gone():
    found = _routes()
    assert not [key for key in REMOVED_ENGINE_ROUTES if key in found]


def test_regular_routes_stay_open():
    found = _routes()
    for key in USER_ROUTES:
        assert key in found, key
        assert found[key] is False, key


def test_web_source_blocks_private_hosts_and_caps_users(monkeypatch):
    from pathmind.api.routers import knowledge

    with pytest.raises(HTTPException) as exc:
        knowledge._web_source_limits("http://127.0.0.1:8001/", 3, 200)
    assert exc.value.status_code == 400

    class _User:
        is_admin = False

    monkeypatch.setattr(knowledge, "get_current_user", lambda: _User())
    monkeypatch.setattr("pathmind.tools.web_fetch._is_disallowed_host", lambda host: False)
    assert knowledge._web_source_limits("https://docs.example.org/", 5, 200) == (3, 50)
    monkeypatch.setenv("KB_WEB_SOURCE_MAX_PAGES", "20")
    assert knowledge._web_source_limits("https://docs.example.org/", 2, 200) == (2, 20)

    _User.is_admin = True
    assert knowledge._web_source_limits("https://docs.example.org/", 5, 200) == (5, 200)


def test_github_repo_format():
    from pathmind.api.routers import knowledge

    assert knowledge._GITHUB_REPO_RE.match("HKUDS/DeepTutor")
    assert not knowledge._GITHUB_REPO_RE.match("../../users")
    assert not knowledge._GITHUB_REPO_RE.match("../..")
    assert knowledge._GITHUB_REPO_RE.match("owner/repo.js")
    assert not knowledge._GITHUB_REPO_RE.match("owner/repo/contents")


# ---------------------------------------------------------------------------
# Web / GitHub source content counts toward the plan's storage limit
# ---------------------------------------------------------------------------


class _Manager:
    def __init__(self, sources):
        self._sources = sources

    def get_github_sources(self, _name):
        return self._sources

    def get_web_sources(self, _name):
        return self._sources


def _wire(monkeypatch, tmp_path, sources):
    from pathmind.api.routers import knowledge

    base = tmp_path / "kbs"
    (base / "kb1" / "raw").mkdir(parents=True)
    monkeypatch.setattr(
        knowledge, "_writable_kb", lambda name: (_Manager(sources), "kb1", base)
    )
    charged: list[int] = []
    monkeypatch.setattr(knowledge, "_record_source_storage", charged.append)
    monkeypatch.setattr(knowledge, "_source_storage_precheck", lambda: None)
    monkeypatch.setattr(knowledge, "_indexing_credit_precheck", lambda: None)
    return knowledge, base / "kb1" / "raw", charged


def test_github_sync_charges_downloaded_bytes(monkeypatch, tmp_path):
    import asyncio

    from pathmind.services.github_source import sync as gh_sync

    knowledge, raw, charged = _wire(monkeypatch, tmp_path, [{"id": "s1", "repo": "o/r"}])

    async def fake_sync(*, kb_name, source, base_dir, client=None):
        (raw / "a.md").write_bytes(b"x" * 1500)
        return gh_sync.SyncResult(ok=True)

    monkeypatch.setattr(gh_sync, "sync_source", fake_sync)
    asyncio.run(knowledge.sync_github_sources("kb1"))
    assert charged == [1500]


def test_web_sync_charges_growth_and_refunds_removed_pages(monkeypatch, tmp_path):
    import asyncio

    from pathmind.services.web_source import sync as web_sync

    knowledge, raw, charged = _wire(
        monkeypatch, tmp_path, [{"id": "w1", "url": "https://docs.example.org/"}]
    )
    (raw / "old.md").write_bytes(b"y" * 800)

    async def fake_sync(*, kb_name, source, base_dir, max_depth=None, max_pages=None):
        (raw / "old.md").unlink()
        (raw / "new.md").write_bytes(b"z" * 300)
        return web_sync.WebSyncResult(ok=True, pages_added=1, pages_removed=1)

    monkeypatch.setattr(web_sync, "sync_source", fake_sync)
    asyncio.run(knowledge.sync_web_sources("kb1"))
    assert charged == [-500]


def test_source_sync_refused_when_storage_is_full(monkeypatch):
    from contextlib import contextmanager

    from pathmind.api.routers import knowledge
    from pathmind.database import connection
    from pathmind.services import quota_service

    class _User:
        id = "u1"
        role = "user"
        is_admin = False

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(knowledge, "get_current_user", lambda: _User())
    monkeypatch.setattr(connection, "get_db_session", fake_session)
    monkeypatch.setattr(
        quota_service, "check_storage_quota", lambda *a, **k: (False, "Storage is full")
    )
    with pytest.raises(HTTPException) as exc:
        knowledge._source_storage_precheck()
    assert exc.value.status_code == 403
