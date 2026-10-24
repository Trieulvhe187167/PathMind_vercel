from conftest import make_client, register


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_first_user_is_admin_then_users(app):
    a = make_client(app)
    assert a.get("/api/auth/status").json()["has_users"] is False
    r = register(a, "alice", email="Alice@Example.com")
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "admin"
    assert r.json()["user"]["email"] == "alice@example.com"
    assert "password_hash" not in r.json()["user"]
    assert a.get("/api/auth/me").json()["user"]["username"] == "alice"  # cookie session
    b = make_client(app)
    assert register(b, "bob").json()["user"]["role"] == "learner"
    assert b.get("/api/auth/status").json()["has_users"] is True


def test_register_validation(client):
    bad = [
        ({"username": "ab"}, "username"),
        ({"username": "has space"}, "username"),
        ({"password": "short1"}, "password"),
        ({"password": "onlyletters"}, "password"),
        ({"password": "12345678"}, "password"),
        ({"password": "carol1234", "username": "carol1234"}, "password"),
    ]
    for extra, field in bad:
        body = {"username": "carol", "password": "secret123", **extra}
        body["confirm_password"] = body["password"]
        r = client.post("/api/auth/register", json=body)
        assert r.status_code == 422, extra
        assert r.json()["detail"]["field"] == field, extra
    r = client.post("/api/auth/register", json={"username": "carol", "password": "secret123", "confirm_password": "secret124"})
    assert r.json()["detail"]["field"] == "confirm_password"
    r = client.post("/api/auth/register", json={"username": "carol", "email": "nope", "password": "secret123", "confirm_password": "secret123"})
    assert r.status_code == 422


def test_duplicates_are_case_insensitive(app):
    register(make_client(app), "alice", email="a@x.io")
    r = register(make_client(app), "ALICE")
    assert r.status_code == 409 and r.json()["detail"]["field"] == "username"
    r = register(make_client(app), "alice2", email="A@X.IO")
    assert r.status_code == 409 and r.json()["detail"]["field"] == "email"


def test_login_by_username_or_email_and_logout(app):
    register(make_client(app), "alice", email="a@x.io")
    c = make_client(app)
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/login", json={"login": "ALICE", "password": "secret123"}).status_code == 200
    assert c.get("/api/auth/me").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401
    r = c.post("/api/auth/login", json={"login": "a@x.io", "password": "secret123"})
    assert r.status_code == 200
    token = r.cookies.get("pm_session")
    assert token
    bearer = make_client(app)
    assert bearer.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_wrong_password_and_lockout(app):
    register(make_client(app), "alice")
    c = make_client(app)
    for _ in range(3):
        r = c.post("/api/auth/login", json={"login": "alice", "password": "wrong1234"})
        assert r.status_code == 401
    r = c.post("/api/auth/login", json={"login": "alice", "password": "secret123"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    # unknown accounts give the same answer as wrong passwords
    r = c.post("/api/auth/login", json={"login": "ghost", "password": "secret123"})
    assert r.status_code == 401


def test_password_change_signs_out_other_sessions(app):
    a = make_client(app)
    register(a, "alice")
    other = make_client(app)
    other.post("/api/auth/login", json={"login": "alice", "password": "secret123"})
    assert other.get("/api/auth/me").status_code == 200
    r = a.post("/api/auth/password", json={"current_password": "nope", "new_password": "newpass123"})
    assert r.status_code == 400
    r = a.post("/api/auth/password", json={"current_password": "secret123", "new_password": "weak"})
    assert r.status_code == 422
    r = a.post("/api/auth/password", json={"current_password": "secret123", "new_password": "newpass123"})
    assert r.status_code == 200
    assert a.get("/api/auth/me").status_code == 200  # this session got a fresh cookie
    assert other.get("/api/auth/me").status_code == 401
    assert make_client(app).post("/api/auth/login", json={"login": "alice", "password": "newpass123"}).status_code == 200


def test_garbage_cookie_is_just_signed_out(client):
    client.cookies.set("pm_session", "abc.def")
    assert client.get("/api/auth/me").status_code == 401


def test_concurrent_first_registrations_make_one_admin(app):
    from concurrent.futures import ThreadPoolExecutor

    names = [f"user{i}" for i in range(4)]
    with ThreadPoolExecutor(len(names)) as pool:
        results = list(pool.map(lambda n: register(make_client(app), n), names))
    assert all(r.status_code == 201 for r in results)
    assert sorted(r.json()["user"]["role"] for r in results) == ["admin", "learner", "learner", "learner"]


def test_throttle_shared_across_username_email_case_and_spaces(app):
    register(make_client(app), "alice", email="a@x.io")
    c = make_client(app)
    for login in ("alice", " ALICE ", "A@X.IO"):
        assert c.post("/api/auth/login", json={"login": login, "password": "wrong1234"}).status_code == 401
    assert c.post("/api/auth/login", json={"login": "a@x.io", "password": "secret123"}).status_code == 429


def test_client_ip_trusts_forwarded_only_from_loopback():
    from types import SimpleNamespace

    from app.routers.auth import _client_ip

    def req(host, fwd=None):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers={"x-forwarded-for": fwd} if fwd else {})

    assert _client_ip(req("127.0.0.1", "6.6.6.6, 10.0.0.5")) == "10.0.0.5"  # last hop added by the Next proxy
    assert _client_ip(req("127.0.0.1")) == "127.0.0.1"
    assert _client_ip(req("10.0.0.9", "1.2.3.4")) == "10.0.0.9"  # not from our proxy: ignore the header
