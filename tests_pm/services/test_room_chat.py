"""Study rooms: create / join, shared chat, asking the room's Partner, en/zh messages."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.multi_user.context import reset_current_user, set_current_user
from pathmind.multi_user.models import CurrentUser, UserScope

USERS = {"u1": "alice", "u2": "bob", "u3": "carol"}


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    from pathmind.services import quota_service

    monkeypatch.setattr(quota_service, "_auth_store_lookup", lambda uid: None)
    for uid, name in USERS.items():
        quota_service.ensure_db_user(session, uid, name, "user")
    yield session
    session.close()


@pytest.fixture()
def client(db, monkeypatch):
    from pathmind.api.routers import partner_shares
    from pathmind.database.connection import get_db

    monkeypatch.setattr(partner_shares, "_usernames", lambda: dict(USERS))
    monkeypatch.setattr(partner_shares, "_partner_names", lambda: {"tutor": "Tutor Bot"})
    monkeypatch.setattr(partner_shares, "_require_manageable", lambda pid: None)
    import pathmind.services.quota_service as qs

    monkeypatch.setattr(qs, "check_turn_allowed", lambda *a, **k: (True, ""))

    app = FastAPI()

    @app.middleware("http")
    async def as_user(request: Request, call_next):
        uid = request.headers.get("x-user", "u1")
        token = set_current_user(
            CurrentUser(uid, USERS[uid], "user", UserScope("user", uid, Path("/tmp")))
        )
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)

    app.include_router(partner_shares.router, prefix="/api/social")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _as(uid):
    return {"x-user": uid}


def test_create_join_and_chat_together(client):
    room = client.post("/api/social/rooms", json={"room_name": "Physics"}).json()
    code = room["room_code"]
    assert room["is_host"] and len(code) == 6

    # Non-members can't read the chat.
    assert client.get(f"/api/social/rooms/{code}/messages", headers=_as("u2")).status_code == 403
    assert client.post(f"/api/social/rooms/{code.lower()}/join", headers=_as("u2")).status_code == 200

    sent = client.post(f"/api/social/rooms/{code}/messages", json={"content": "hi bob"}).json()
    assert sent["messages"][0]["author"] == "alice"
    client.post(f"/api/social/rooms/{code}/messages", json={"content": "hey"}, headers=_as("u2"))

    msgs = client.get(f"/api/social/rooms/{code}/messages", headers=_as("u2")).json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("system", "bob joined the room."),
        ("user", "hi bob"),
        ("user", "hey"),
    ]
    newer = client.get(f"/api/social/rooms/{code}/messages?after={msgs[1]['id']}").json()["messages"]
    assert [m["content"] for m in newer] == ["hey"]

    # Asking without a partner is refused and stores nothing.
    res = client.post(f"/api/social/rooms/{code}/messages", json={"content": "?", "ask_partner": True})
    assert res.status_code == 400

    client.post(f"/api/social/rooms/{code}/leave", headers=_as("u2"))
    assert client.get(f"/api/social/rooms/{code}/messages", headers=_as("u2")).status_code == 403
    last = client.get(f"/api/social/rooms/{code}/messages").json()["messages"][-1]
    assert last["content"] == "bob left the room."


def test_partner_answers_publicly(client, monkeypatch):
    from pathmind.api.routers import partner_shares

    seen = {}

    async def fake_ask(db, room, question):
        seen["question"] = question
        seen["context"] = partner_shares._partner_context(db, room, dict(USERS), "Tutor Bot")
        return "F = m·a"

    monkeypatch.setattr(partner_shares, "_ask_room_partner", fake_ask)
    code = client.post("/api/social/rooms", json={"room_name": "Mech", "partner_id": "tutor"}).json()[
        "room_code"
    ]
    client.post(f"/api/social/rooms/{code}/join", headers=_as("u3"))
    res = client.post(
        f"/api/social/rooms/{code}/messages",
        json={"content": "Newton 2?", "ask_partner": True},
        headers=_as("u3"),
    ).json()
    assert [(m["role"], m["author"], m["content"]) for m in res["messages"]] == [
        ("user", "carol", "Newton 2?"),
        ("partner", "Tutor Bot", "F = m·a"),
    ]
    assert seen["question"] == "Newton 2?"
    assert "carol: Newton 2?" in seen["context"] and "alice, carol" in seen["context"]
    # Everyone sees it.
    msgs = client.get(f"/api/social/rooms/{code}/messages").json()["messages"]
    assert msgs[-1]["role"] == "partner"


def test_partner_failure_becomes_a_system_note(client, monkeypatch):
    from pathmind.api.routers import partner_shares

    async def boom(db, room, question):
        raise RuntimeError("provider down")

    monkeypatch.setattr(partner_shares, "_ask_room_partner", boom)
    code = client.post("/api/social/rooms", json={"room_name": "R", "partner_id": "tutor"}).json()["room_code"]
    res = client.post(f"/api/social/rooms/{code}/messages", json={"content": "q", "ask_partner": True})
    assert res.status_code == 200
    assert res.json()["messages"][-1]["role"] == "system"
    assert "provider down" in res.json()["messages"][-1]["content"]


def test_room_errors_follow_ui_language(client, monkeypatch):
    import pathmind.services.i18n as i18n

    monkeypatch.setattr(i18n, "current_language", lambda default="en": "zh")
    res = client.get("/api/social/rooms/NOPE00/messages")
    assert res.status_code == 404 and res.json()["detail"] == "房间不存在或已关闭。"
    monkeypatch.setattr(i18n, "current_language", lambda default="en": "en")
    assert client.get("/api/social/rooms/NOPE00").json()["detail"] == "This room doesn't exist or has been closed."


def test_no_vietnamese_left_in_social_routers():
    import re

    root = Path(__file__).resolve().parents[2] / "pathmind" / "api" / "routers"
    for name in ("friends.py", "partner_shares.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert not re.search("[ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđĐ]", text), name
