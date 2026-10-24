"""Devices, e-mail codes, password reset, profile, account deletion, Google sign-in, closed sign-up."""

import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client, register
from app.routers import google_auth
from app.services import mailer


@pytest.fixture()
def outbox(monkeypatch):
    box: list[dict] = []
    monkeypatch.setattr(mailer, "OUTBOX", box)
    return box


def _code(box, to):
    msg = [m for m in box if m["to"] == to][-1]
    return re.search(r"\b(\d{6})\b", msg["body"]).group(1)


def test_devices_list_and_revoke(app):
    a = make_client(app)
    register(a, "alice")
    b = make_client(app)
    assert b.post("/api/auth/login", json={"login": "alice", "password": "secret123"}).status_code == 200
    sessions = a.get("/api/me/sessions").json()["sessions"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    assert a.post("/api/me/sessions/revoke-others").json() == {"ok": True}
    assert b.get("/api/auth/me").status_code == 401  # the other device is signed out
    assert a.get("/api/auth/me").status_code == 200
    a.post("/api/auth/logout")
    assert a.get("/api/auth/me").status_code == 401
    # Even a copied cookie no longer works after logout: the session is revoked on the server.
    c = make_client(app)
    c.post("/api/auth/login", json={"login": "alice", "password": "secret123"})
    cookie = c.cookies.get("pm_session")
    c.post("/api/auth/logout")
    d = make_client(app)
    d.cookies.set("pm_session", cookie)
    assert d.get("/api/auth/me").status_code == 401


def test_email_verification_and_required_flow(app, outbox):
    admin = make_client(app)
    register(admin, "alice", email="alice@x.com")
    admin.put("/api/admin/settings/smtp", json={"host": "smtp.x.com", "from_address": "pm@x.com"})
    admin.put("/api/admin/settings/registration", json={"require_email_verification": True})
    bob = make_client(app)
    r = register(bob, "bob", email="bob@x.com").json()
    assert r["needs_verification"] is True
    assert bob.get("/api/auth/me").status_code == 401  # not signed in yet
    login = bob.post("/api/auth/login", json={"login": "bob", "password": "secret123"})
    assert login.status_code == 403 and login.json()["detail"]["code"] == "email_not_verified"
    assert bob.post("/api/auth/email/confirm", json={"login": "bob", "code": "000000"}).status_code == 400
    ok = bob.post("/api/auth/email/confirm", json={"login": "bob", "code": _code(outbox, "bob@x.com")})
    assert ok.status_code == 200 and ok.json()["user"]["email_verified"] is True
    assert bob.get("/api/auth/me").status_code == 200
    # Signed-in verification of a changed address.
    r = bob.put("/api/me/email", json={"email": "bob2@x.com", "password": "secret123"}).json()
    assert r["user"]["email"] == "bob2@x.com" and r["user"]["email_verified"] is False
    assert bob.post("/api/auth/email/verify", json={"code": _code(outbox, "bob2@x.com")}).json()["user"]["email_verified"]


def test_forgot_and_reset_password(app, outbox):
    a = make_client(app)
    register(a, "alice", email="alice@x.com")
    other = make_client(app)
    assert other.post("/api/auth/password/forgot", json={"email": "nobody@x.com"}).json()["ok"] is True
    other.post("/api/auth/password/forgot", json={"email": "alice@x.com"})
    code = _code(outbox, "alice@x.com")
    bad = other.post("/api/auth/password/reset", json={"email": "alice@x.com", "code": code, "new_password": "short"})
    assert bad.status_code == 422
    r = other.post("/api/auth/password/reset", json={"email": "alice@x.com", "code": code, "new_password": "brandnew42"})
    assert r.status_code == 200
    assert a.get("/api/auth/me").status_code == 401  # old sessions end
    assert make_client(app).post("/api/auth/login", json={"login": "alice", "password": "brandnew42"}).status_code == 200
    # A used code cannot be used twice.
    again = other.post("/api/auth/password/reset", json={"email": "alice@x.com", "code": code, "new_password": "another42"})
    assert again.status_code == 400


def test_profile_and_avatar(app):
    a = make_client(app)
    register(a, "alice")
    r = a.patch("/api/me/profile", json={"full_name": "Alice A", "locale": "zh", "theme": "dark",
                                        "timezone": "Europe/Paris"}).json()["user"]
    assert (r["full_name"], r["locale"], r["theme"], r["timezone"]) == ("Alice A", "zh", "dark", "Europe/Paris")
    assert a.patch("/api/me/profile", json={"timezone": "Mars/Base"}).status_code == 422
    assert a.patch("/api/me/profile", json={"locale": "fr"}).status_code == 422
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    f = a.post("/api/files", files={"file": ("me.png", png, "image/png")}, data={"purpose": "avatar"}).json()["file"]
    u = a.patch("/api/me/profile", json={"avatar_file_id": f["id"]}).json()["user"]
    assert u["avatar_url"] == f"/api/files/{f['id']}/download?inline=true"
    b = make_client(app)
    register(b, "bob")
    assert b.get(u["avatar_url"]).status_code == 200  # avatars are visible to signed-in people


def test_self_delete_removes_personal_data_keeps_payments(app):
    admin = make_client(app)
    register(admin, "alice")
    bob = make_client(app)
    bob_user = register(bob, "bob", email="bob@x.com").json()["user"]
    admin.put("/api/admin/settings/billing", json={"demo_mode": True})
    txn = bob.post("/api/billing/checkout", json={"plan_id": "plus", "gateway": "demo"}).json()["payment"]["transaction_id"]
    bob.post(f"/api/billing/payments/{txn}/simulate")
    bob.post("/api/files", files={"file": ("n.txt", b"private", "text/plain")})
    assert bob.post("/api/me/delete", json={"password": "secret123", "confirm": "delete"}).status_code == 422
    assert bob.post("/api/me/delete", json={"password": "wrong", "confirm": "DELETE"}).status_code == 400
    assert bob.post("/api/me/delete", json={"password": "secret123", "confirm": "DELETE"}).json() == {"ok": True}
    assert bob.get("/api/auth/me").status_code == 401
    assert make_client(app).post("/api/auth/login", json={"login": "bob", "password": "secret123"}).status_code == 401
    from app.models import AuthSession, Payment, StoredFile, User

    with app.state.session_factory() as db:
        u = db.get(User, bob_user["id"])
        assert u.username.startswith("deleted~") and u.email is None and u.password_hash is None
        assert db.query(Payment).filter_by(user_id=u.id).count() == 2 or db.query(Payment).filter_by(user_id=u.id).count() == 1
        assert db.query(StoredFile).filter_by(owner_id=u.id).count() == 0
        assert db.query(AuthSession).filter_by(user_id=u.id).count() == 0
    # The last admin cannot delete themselves.
    r = admin.post("/api/me/delete", json={"password": "secret123", "confirm": "DELETE"})
    assert r.status_code == 409


def test_signup_can_be_closed(app):
    admin = make_client(app)
    register(admin, "alice")
    admin.put("/api/admin/settings/registration", json={"allow_signup": False})
    assert make_client(app).get("/api/auth/status").json()["signup_open"] is False
    r = register(make_client(app), "bob")
    assert r.status_code == 403


def test_google_sign_in(app, monkeypatch):
    def fake_google(request: httpx.Request):
        if request.url.path == "/token":
            assert b"code=abc" in request.content and b"client_secret=gsecret" in request.content
            return httpx.Response(200, json={"access_token": "at"})
        assert request.headers["authorization"] == "Bearer at"
        return httpx.Response(200, json={"sub": "g-1", "email": "Gina@Gmail.com", "email_verified": True,
                                         "name": "Gina G", "picture": "https://x/p.png"})

    monkeypatch.setattr(google_auth, "TRANSPORT", httpx.MockTransport(fake_google))
    admin = make_client(app)
    register(admin, "alice")
    admin.put("/api/admin/settings/general", json={"public_url": "https://learn.example.com"})
    admin.put("/api/admin/settings/google_oauth", json={"enabled": True, "client_id": "cid", "client_secret": "gsecret"})
    assert make_client(app).get("/api/auth/status").json()["google"] is True

    g = make_client(app)
    start = g.get("/api/auth/google/start?next=/dashboard", follow_redirects=False)
    assert start.status_code == 303
    q = parse_qs(urlsplit(start.headers["location"]).query)
    assert q["redirect_uri"] == ["https://learn.example.com/api/auth/google/callback"]
    # A forged state is refused.
    bad = g.get("/api/auth/google/callback?code=abc&state=wrong", follow_redirects=False)
    assert bad.headers["location"].endswith("/login?error=google_failed")
    cb = g.get(f"/api/auth/google/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
    assert cb.status_code == 303 and cb.headers["location"] == "https://learn.example.com/dashboard"
    me = g.get("/api/auth/me").json()["user"]
    assert me["username"] == "gina" and me["email"] == "gina@gmail.com" and me["email_verified"] and not me["has_password"]
    assert me["role"] == "learner"
    # A Google-only account can set a password without knowing a current one.
    assert g.post("/api/auth/password", json={"current_password": "", "new_password": "fromgoogle1"}).status_code == 200
    # Signing in again links to the same account.
    g2 = make_client(app)
    s2 = g2.get("/api/auth/google/start", follow_redirects=False)
    st = parse_qs(urlsplit(s2.headers["location"]).query)["state"][0]
    g2.get(f"/api/auth/google/callback?code=abc&state={st}", follow_redirects=False)
    assert g2.get("/api/auth/me").json()["user"]["id"] == me["id"]


def test_invitation_is_emailed_when_smtp_is_set(app, outbox):
    admin = make_client(app)
    register(admin, "alice", email="alice@x.com")
    r = admin.post("/api/admin/invitations", json={"email": "new@x.com", "role": "learner", "link_base": "https://learn.x.com/"}).json()
    assert r["emailed"] is False and not outbox  # no SMTP yet: the admin copies the link
    admin.delete(f"/api/admin/invitations/{r['invitation']['id']}")
    admin.put("/api/admin/settings/smtp", json={"host": "smtp.x.com", "from_address": "pm@x.com"})
    r = admin.post("/api/admin/invitations", json={"email": "new@x.com", "role": "educator", "link_base": "https://learn.x.com/"}).json()
    assert r["emailed"] is True
    assert f"https://learn.x.com/invite/{r['token']}" in outbox[-1]["body"]
