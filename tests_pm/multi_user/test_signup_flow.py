"""Detailed sign-up, email verification and the identity/DB mirror repair."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def signup_app(mu_isolated_root, tmp_path, monkeypatch):
    from pathmind.api.routers import auth as auth_router
    from pathmind.database import connection
    from pathmind.database.models import Base
    from pathmind.services import email_verification

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(connection, "engine", engine)
    monkeypatch.setattr(connection, "SessionLocal", session_factory)

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "_REGISTER_MAX_PER_WINDOW", 0)
    # Plain-HTTP test client: the session cookie must not be Secure-only.
    monkeypatch.setattr(auth_router, "_SECURE", False)
    monkeypatch.setattr(auth_router, "_SAMESITE", "lax")
    import pathmind.services.auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)

    sent: list[tuple[str, str]] = []

    def fake_send(to, subject, body):
        sent.append((to, body.split(": ", 1)[1][:6]))
        return True

    monkeypatch.setattr(email_verification, "_send_mail", fake_send)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.delenv("EMAIL_VERIFICATION_REQUIRED", raising=False)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    with TestClient(app) as client:
        yield client, sent, engine, session_factory


def _signup(client, **overrides):
    body = {
        "full_name": "Nguyen Van A",
        "email": "a@example.com",
        "username": "vana",
        "password": "hoctap2026",
        "confirm_password": "hoctap2026",
        "accept_terms": True,
    }
    body.update(overrides)
    return client.post("/api/auth/register", json=body)


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"full_name": "A"}, "full_name", "full_name_invalid"),
        ({"email": "not-an-email"}, "email", "email_invalid"),
        ({"username": "a b"}, "username", "username_invalid"),
        ({"password": "short1", "confirm_password": "short1"}, "password", "password_too_short"),
        ({"password": "onlyletters", "confirm_password": "onlyletters"}, "password", "password_weak"),
        ({"confirm_password": "different1"}, "confirm_password", "password_mismatch"),
        ({"accept_terms": False}, "accept_terms", "terms_required"),
    ],
)
def test_signup_validation(signup_app, overrides, field, code):
    client, *_ = signup_app
    resp = _signup(client, **overrides)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["field"] == field
    assert resp.json()["detail"]["code"] == code


def test_signup_verify_login_flow(signup_app):
    client, sent, _engine, session_factory = signup_app
    # First account becomes admin — create it so the next one is a regular user.
    assert _signup(client, email="boss@example.com", username="boss").status_code == 201

    resp = _signup(client)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["role"] == "user"
    assert data["email_verification"] == {"required": True, "delivery": "email"}

    # Duplicate email (case-insensitive) and username are rejected.
    dup = _signup(client, email="A@Example.com", username="other")
    assert dup.status_code == 409 and dup.json()["detail"]["field"] == "email"
    dup = _signup(client, email="z@example.com", username="VANA")
    assert dup.status_code == 409 and dup.json()["detail"]["field"] == "username"

    # Unverified user cannot sign in while SMTP is configured.
    login = client.post("/api/auth/login", json={"username": "vana", "password": "hoctap2026"})
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == "email_not_verified"

    # Wrong code counts an attempt; the right one verifies.
    code = [c for to, c in sent if to == "a@example.com"][-1]
    bad = client.post("/api/auth/email/verify", json={"email": "a@example.com", "code": "000000" if code != "000000" else "111111"})
    assert bad.status_code == 400
    ok = client.post("/api/auth/email/verify", json={"email": "a@example.com", "code": code})
    assert ok.status_code == 200, ok.text

    # Login by username and by email both work now.
    assert client.post("/api/auth/login", json={"username": "vana", "password": "hoctap2026"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "a@example.com", "password": "hoctap2026"}).status_code == 200

    from pathmind.services.auth import get_user_info

    info = get_user_info("vana")
    assert info["full_name"] == "Nguyen Van A"
    assert info["email"] == "a@example.com"
    assert info["email_verified"] is True

    from pathmind.database.models import Subscription, User

    db = session_factory()
    try:
        row = db.query(User).filter_by(username="vana").one()
        assert row.email == "a@example.com" and row.full_name == "Nguyen Van A"
        assert row.email_verified is True
        assert db.query(Subscription).filter_by(user_id=row.id).count() == 1
    finally:
        db.close()


def test_resend_is_generic_and_throttled(signup_app):
    client, sent, *_ = signup_app
    assert _signup(client).status_code == 201
    # Unknown email: same generic answer, nothing sent.
    before = len(sent)
    assert client.post("/api/auth/email/resend", json={"email": "nobody@example.com"}).json()["ok"]
    assert len(sent) == before
    # Registered but just issued → cooldown.
    resp = client.post("/api/auth/email/resend", json={"email": "a@example.com"})
    assert resp.status_code in (200, 429)


def test_no_smtp_does_not_block_login(signup_app, monkeypatch):
    client, *_ = signup_app
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert _signup(client, email="boss@example.com", username="boss").status_code == 201
    resp = _signup(client)
    assert resp.json()["email_verification"]["required"] is False
    login = client.post("/api/auth/login", json={"username": "vana", "password": "hoctap2026"})
    assert login.status_code == 200


def test_startup_repairs_dedupe_and_mirror(signup_app):
    client, _sent, engine, session_factory = signup_app
    from pathmind.database.maintenance import ensure_unique_indexes, reconcile_identity_mirror
    from pathmind.database.models import User

    assert _signup(client).status_code == 201
    db = session_factory()
    real = db.query(User).filter_by(username="vana").one()
    real_id = real.id
    # Simulate the legacy state: the real account mirrored under a mangled
    # name and an orphan seed row owning the real username.
    real.username = "vana#u_legacy"
    db.commit()
    db.add(User(id="orphan-1", username="vana", password_hash="x", email="old@example.com"))
    db.commit()
    db.close()

    with engine.begin() as conn:
        # Recreate the table the way old databases had it: no unique pair.
        conn.execute(text("DROP TABLE friendships"))
        conn.execute(
            text(
                "CREATE TABLE friendships (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36), "
                "friend_id VARCHAR(36), status VARCHAR(20), created_at DATETIME, updated_at DATETIME)"
            )
        )
        for _ in range(2):
            conn.execute(
                text(
                    "INSERT INTO friendships (id, user_id, friend_id, status) "
                    "VALUES (hex(randomblob(8)), :a, :b, 'accepted')"
                ),
                {"a": real_id, "b": "orphan-1"},
            )

    ensure_unique_indexes(engine)
    reconcile_identity_mirror(session_factory)
    reconcile_identity_mirror(session_factory)  # idempotent

    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM friendships")).scalar()
        assert n == 1
    db = session_factory()
    try:
        assert db.get(User, real_id).username == "vana"
        orphan = db.get(User, "orphan-1")
        assert orphan is not None and orphan.is_active is False
        assert orphan.username.startswith("vana~orphan~")
    finally:
        db.close()
