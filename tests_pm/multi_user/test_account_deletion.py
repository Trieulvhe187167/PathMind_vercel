"""Self-service account deletion erases the account and ends its sessions."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def app_client(mu_isolated_root, tmp_path, monkeypatch):
    from pathmind.api.routers import auth as auth_router
    from pathmind.database import connection
    from pathmind.database.models import Base
    import pathmind.services.auth as auth_service

    engine = create_engine(f"sqlite:///{tmp_path / 'del.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(connection, "engine", engine)
    monkeypatch.setattr(connection, "SessionLocal", factory)
    for mod in (auth_router, auth_service):
        monkeypatch.setattr(mod, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "_REGISTER_MAX_PER_WINDOW", 0)
    monkeypatch.setattr(auth_router, "_SECURE", False)
    monkeypatch.setattr(auth_router, "_SAMESITE", "lax")
    monkeypatch.setattr(auth_router, "_login_failures", {})
    monkeypatch.setattr(auth_router, "_login_locked_until", {})
    monkeypatch.delenv("SMTP_HOST", raising=False)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    with TestClient(app) as client:
        yield client, factory, mu_isolated_root


def _signup(client, username, email):
    return client.post(
        "/api/auth/register",
        json={
            "full_name": "Test Person",
            "email": email,
            "username": username,
            "password": "hoctap2026",
            "confirm_password": "hoctap2026",
            "accept_terms": True,
        },
    )


def _headers(username):
    from pathmind.services.auth import create_token, get_user_info

    info = get_user_info(username)
    return {"Authorization": f"Bearer {create_token(username, info['role'], info['id'])}"}


def test_user_can_delete_own_account(app_client):
    client, factory, root = app_client
    assert _signup(client, "boss", "boss@example.com").status_code == 201  # first = admin
    resp = _signup(client, "vana", "a@example.com")
    assert resp.status_code == 201
    user_id = resp.json()["user_id"]

    from pathmind.multi_user import paths

    user_dir = paths.USERS_ROOT / user_id
    (user_dir / "user").mkdir(parents=True, exist_ok=True)
    (user_dir / "user" / "note.txt").write_text("private", encoding="utf-8")

    assert client.post("/api/auth/login", json={"username": "vana", "password": "hoctap2026"}).status_code == 200
    headers = _headers("vana")
    # Wrong confirmation / password are refused.
    bad = client.post("/api/auth/account/delete", json={"password": "hoctap2026", "confirm": "nope"}, headers=headers)
    assert bad.status_code == 400 and bad.json()["detail"]["field"] == "confirm"
    bad = client.post("/api/auth/account/delete", json={"password": "wrong1234", "confirm": "DELETE"}, headers=headers)
    assert bad.status_code == 400 and bad.json()["detail"]["field"] == "password"

    ok = client.post("/api/auth/account/delete", json={"password": "hoctap2026", "confirm": "DELETE"}, headers=headers)
    assert ok.status_code == 200, ok.text

    from pathmind.services.auth import get_user_info

    assert get_user_info("vana") is None
    assert not user_dir.exists()
    assert client.post("/api/auth/login", json={"username": "vana", "password": "hoctap2026"}).status_code == 401

    from pathmind.database.models import User

    db = factory()
    try:
        row = db.get(User, user_id)
        assert row is not None and row.is_active is False
        assert row.email is None and row.full_name is None
        assert row.username.startswith("deleted~")
    finally:
        db.close()


def test_old_session_stops_working_after_deletion(app_client):
    client, *_ = app_client
    from pathmind.services.auth import create_token, decode_token, get_user_info

    assert _signup(client, "boss", "boss@example.com").status_code == 201
    assert _signup(client, "vanb", "b@example.com").status_code == 201
    info = get_user_info("vanb")
    token = create_token("vanb", "user", info["id"])
    assert decode_token(token) is not None
    from pathmind.services.account_deletion import delete_account

    delete_account("vanb", actor="test")
    assert decode_token(token) is None


def test_last_admin_cannot_delete_self(app_client):
    client, *_ = app_client
    assert _signup(client, "boss", "boss@example.com").status_code == 201
    resp = client.post(
        "/api/auth/account/delete",
        json={"password": "hoctap2026", "confirm": "DELETE"},
        headers=_headers("boss"),
    )
    assert resp.status_code == 409


def test_admin_can_lock_but_not_delete(app_client):
    client, *_ = app_client
    from pathmind.services.auth import create_token, decode_token, get_user_info

    assert _signup(client, "boss", "boss@example.com").status_code == 201
    assert _signup(client, "vanc", "c@example.com").status_code == 201
    admin = _headers("boss")
    info = get_user_info("vanc")
    user_token = create_token("vanc", "user", info["id"])

    # There is no admin delete endpoint any more.
    assert client.delete("/api/auth/users/vanc", headers=admin).status_code in (404, 405)
    assert get_user_info("vanc") is not None

    locked = client.put("/api/auth/users/vanc/status", json={"disabled": True}, headers=admin)
    assert locked.status_code == 200, locked.text
    assert decode_token(user_token) is None  # sessions end immediately
    login = client.post("/api/auth/login", json={"username": "vanc", "password": "hoctap2026"})
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == "account_disabled"
    # A wrong password on a locked account does not reveal the lock.
    wrong = client.post("/api/auth/login", json={"username": "vanc", "password": "nottheone1"})
    assert wrong.status_code == 401

    # Admin cannot lock themself.
    assert client.put("/api/auth/users/boss/status", json={"disabled": True}, headers=admin).status_code == 400

    assert client.put("/api/auth/users/vanc/status", json={"disabled": False}, headers=admin).status_code == 200
    assert client.post("/api/auth/login", json={"username": "vanc", "password": "hoctap2026"}).status_code == 200
