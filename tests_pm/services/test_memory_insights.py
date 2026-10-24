"""Opt-in anonymised memory aggregates: vocabulary-only data, consent, erasure, k-anonymity."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.models import MemoryInsight, MemorySharingConsent
from pathmind.multi_user.context import set_current_user, user_from_token_payload
from pathmind.services import memory_insights as mi


def _user(uid: str):
    return user_from_token_payload(SimpleNamespace(user_id=uid, username=uid, role="user"))


@pytest.fixture()
def factory(monkeypatch):
    import pathmind.database.connection as connection

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(connection, "SessionLocal", f)
    return f


@pytest.fixture()
def preview(monkeypatch):
    data = {
        "period": "2026-10",
        "items": [
            {"kind": "topic", "label": "math", "count": 1},
            {"kind": "preference", "label": "examples_first", "count": 1},
            {"kind": "activity", "label": "chat", "count": 12},
        ],
    }
    monkeypatch.setattr(mi, "build_preview", lambda period=None: data)
    return data


def test_tags_come_from_a_fixed_vocabulary_not_from_the_text():
    text = "I'm Nguyen Van A, email a@b.c, studying calculus and Python. Please give me examples."
    assert mi.match_tags(text, mi.TOPICS) == ["math", "programming"]
    assert mi.match_tags(text, mi.PREFERENCES) == ["examples_first"]
    assert mi.match_tags("toán và ví dụ", mi.TOPICS) == ["math"]
    assert mi.match_tags("nothing relevant", mi.TOPICS) == []


def test_only_known_labels_are_valid():
    assert mi.valid_label("topic", "math")
    assert not mi.valid_label("topic", "Nguyen Van A")
    assert not mi.valid_label("preference", "math")
    assert mi.valid_label("activity", "chat") and not mi.valid_label("activity", "secret")
    assert not mi.valid_label("other", "math")


def test_preview_carries_tags_and_counts_but_no_memory_text(monkeypatch, tmp_path):
    import pathmind.services.memory.store as store_mod

    docs = {
        "profile": "My name is Alice Smith, I study organic chemistry. Phone 0901234567.",
        "preferences": "I like short answers and quizzes.",
        "scope": "",
        "recent": "",
    }
    fake = SimpleNamespace(read_raw=lambda layer, slot: docs.get(slot, ""))
    monkeypatch.setattr(store_mod, "get_memory_store", lambda: fake)
    monkeypatch.setattr(mi, "_activity_counts", lambda period: {"chat": 3})
    out = mi.build_preview("2026-10")
    flat = repr(out)
    assert "Alice" not in flat and "0901234567" not in flat
    kinds = {(i["kind"], i["label"]) for i in out["items"]}
    assert ("topic", "chemistry") in kinds
    assert ("preference", "concise") in kinds and ("preference", "quizzes") in kinds
    assert ("activity", "chat") in kinds
    assert all(mi.valid_label(i["kind"], i["label"]) for i in out["items"])


def test_nothing_is_stored_before_consent_and_sharing_requires_it(factory, preview):
    db = factory()
    assert mi.status(db, "u1")["enabled"] is False
    with pytest.raises(PermissionError):
        mi.share_now(db, "u1")
    assert db.query(MemoryInsight).count() == 0


def test_rows_are_keyed_by_a_pseudonym_never_the_account(factory, preview):
    db = factory()
    mi.enable(db, "u1")
    mi.share_now(db, "u1")
    rows = db.query(MemoryInsight).all()
    assert len(rows) == 3
    consent = db.get(MemorySharingConsent, "u1")
    assert {r.pseudo_id for r in rows} == {consent.pseudo_id}
    assert "u1" not in consent.pseudo_id and len(consent.pseudo_id) == 32
    assert mi.status(db, "u1")["last_shared_at"]


def test_sharing_again_replaces_the_month_instead_of_double_counting(factory, preview):
    db = factory()
    mi.enable(db, "u1")
    mi.share_now(db, "u1")
    mi.share_now(db, "u1")
    assert db.query(MemoryInsight).count() == 3


def test_withdrawing_erases_the_data_and_the_link(factory, preview):
    db = factory()
    mi.enable(db, "u1")
    first = db.get(MemorySharingConsent, "u1").pseudo_id
    mi.share_now(db, "u1")
    assert mi.withdraw(db, "u1") == 3
    assert db.query(MemoryInsight).count() == 0
    assert db.get(MemorySharingConsent, "u1") is None
    assert mi.withdraw(db, "u1") == 0
    mi.enable(db, "u1")
    assert db.get(MemorySharingConsent, "u1").pseudo_id != first


def test_deleting_shared_data_keeps_consent_on(factory, preview):
    db = factory()
    mi.enable(db, "u1")
    mi.share_now(db, "u1")
    assert mi.delete_shared_data(db, "u1") == 3
    st = mi.status(db, "u1")
    assert st["enabled"] is True and st["last_shared_at"] is None and st["stale"] is True


def test_one_person_cannot_touch_anothers_rows(factory, preview):
    db = factory()
    for u in ("a", "b"):
        mi.enable(db, u)
        mi.share_now(db, u)
    mi.withdraw(db, "a")
    assert db.query(MemoryInsight).count() == 3
    assert db.get(MemorySharingConsent, "b") is not None


def test_admins_only_see_groups_of_at_least_k_people(factory, preview):
    db = factory()
    for i in range(4):
        mi.enable(db, f"u{i}")
        mi.share_now(db, f"u{i}")
    out = mi.aggregate(db, "2026-10")
    assert out["groups"] == [] and out["consenting_accounts"] is None
    mi.enable(db, "u4")
    mi.share_now(db, "u4")
    out = mi.aggregate(db, "2026-10")
    labels = {(g["kind"], g["label"]): g for g in out["groups"]}
    assert labels[("topic", "math")]["people"] == 5
    assert labels[("activity", "chat")]["total"] == 60
    assert out["consenting_accounts"] == 5
    with pytest.raises(ValueError):
        mi.aggregate(db, "last month")


def test_account_deletion_scrubs_shared_data(factory, preview, monkeypatch):
    import pathmind.database.connection as connection
    from pathmind.services import account_deletion

    monkeypatch.setattr(
        connection,
        "get_db_session",
        __import__("contextlib").contextmanager(lambda: (yield factory())),
    )
    db = factory()
    mi.enable(db, "u1")
    mi.share_now(db, "u1")
    account_deletion._scrub_database("u1")
    assert factory().query(MemoryInsight).count() == 0
    assert factory().get(MemorySharingConsent, "u1") is None


def _client(factory, uid: str, admin_ok: bool):
    from pathmind.api.routers import memory_sharing
    from pathmind.api.routers.auth import require_admin
    from pathmind.database.connection import get_db

    app = FastAPI()
    app.include_router(memory_sharing.router, prefix="/api/memory-sharing")

    def _db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    def _admin():
        if not admin_ok:
            from fastapi import HTTPException

            raise HTTPException(403)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_admin] = _admin
    set_current_user(_user(uid))
    return TestClient(app)


def test_api_toggle_share_withdraw(factory, preview):
    c = _client(factory, "u9", True)
    assert c.get("/api/memory-sharing").json()["enabled"] is False
    assert c.post("/api/memory-sharing/share").status_code == 409
    assert c.get("/api/memory-sharing/preview").json()["items"]
    assert c.put("/api/memory-sharing", json={"enabled": True}).json()["enabled"] is True
    assert c.post("/api/memory-sharing/share").json()["shared"] == 3
    assert c.delete("/api/memory-sharing/data").json()["deleted"] == 3
    c.post("/api/memory-sharing/share")
    out = c.put("/api/memory-sharing", json={"enabled": False}).json()
    assert out["enabled"] is False and out["deleted"] == 3


def test_admin_stats_is_admin_only(factory, preview):
    assert _client(factory, "u9", False).get("/api/memory-sharing/admin/stats").status_code == 403
    assert _client(factory, "u9", True).get("/api/memory-sharing/admin/stats").status_code == 200
    assert _client(factory, "u9", True).get("/api/memory-sharing/admin/stats?period=x").status_code == 400


def test_sharing_router_is_mounted_without_the_memory_plan_gate():
    import re
    from pathlib import Path

    import pathmind.api.main as main

    src = Path(main.__file__).read_text(encoding="utf-8")
    block = re.search(r"app\.include_router\(\s*memory_sharing\.router[^)]*\)", src, re.S)
    assert block and "dependencies=_auth" in block.group(0)
    assert "_memory" not in block.group(0)
