from conftest import make_client, register


def _setup(app):
    admin = make_client(app)
    register(admin, "alice")
    user = make_client(app)
    bob = register(user, "bob").json()["user"]
    return admin, user, bob


def test_only_admins_manage_users(app):
    admin, user, bob = _setup(app)
    assert user.get("/api/admin/users").status_code == 403
    assert make_client(app).get("/api/admin/users").status_code == 401
    assert user.patch(f"/api/admin/users/{bob['id']}", json={"role": "admin"}).status_code == 403
    users = admin.get("/api/admin/users").json()["users"]
    assert [u["username"] for u in users] == ["alice", "bob"]
    assert [u["username"] for u in admin.get("/api/admin/users?q=BO").json()["users"]] == ["bob"]


def test_promote_and_validate_role(app):
    admin, user, bob = _setup(app)
    assert admin.patch(f"/api/admin/users/{bob['id']}", json={"role": "wizard"}).status_code == 422
    assert admin.patch("/api/admin/users/missing", json={"role": "learner"}).status_code == 404
    r = admin.patch(f"/api/admin/users/{bob['id']}", json={"role": "admin"})
    assert r.json()["user"]["role"] == "admin"
    assert user.get("/api/admin/users").status_code == 200  # role is read fresh each request


def test_deactivate_signs_user_out_and_blocks_login(app):
    admin, user, bob = _setup(app)
    assert user.get("/api/auth/me").status_code == 200
    assert admin.patch(f"/api/admin/users/{bob['id']}", json={"is_active": False}).json()["user"]["is_active"] is False
    assert user.get("/api/auth/me").status_code == 401
    r = make_client(app).post("/api/auth/login", json={"login": "bob", "password": "secret123"})
    assert r.status_code == 403
    admin.patch(f"/api/admin/users/{bob['id']}", json={"is_active": True})
    assert make_client(app).post("/api/auth/login", json={"login": "bob", "password": "secret123"}).status_code == 200


def test_cannot_drop_own_or_last_admin(app):
    admin, user, bob = _setup(app)
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"role": "learner"}).status_code == 409
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"is_active": False}).status_code == 409
    admin.patch(f"/api/admin/users/{bob['id']}", json={"role": "admin"})
    # bob (now admin) may demote alice since another admin remains
    assert user.patch(f"/api/admin/users/{me['id']}", json={"role": "learner"}).status_code == 200
    # and now bob is the last admin; nobody may demote him (he cannot demote himself)
    assert user.patch(f"/api/admin/users/{bob['id']}", json={"role": "learner"}).status_code == 409


def test_search_treats_wildcards_literally_and_handles_null_email(app):
    admin = make_client(app)
    register(admin, "alice")
    register(make_client(app), "a_b", email="ab@x.io")
    register(make_client(app), "axb")  # no email: NULL must not break the OR
    assert [u["username"] for u in admin.get("/api/admin/users?q=a_b").json()["users"]] == ["a_b"]
    assert [u["username"] for u in admin.get("/api/admin/users?q=axb").json()["users"]] == ["axb"]
    assert admin.get("/api/admin/users?q=%25").json()["users"] == []


def test_two_admins_demoting_each_other_concurrently_keep_one(app, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time

    from app.routers import admin_users

    admin, user, bob = _setup(app)
    alice = admin.get("/api/auth/me").json()["user"]
    admin.patch(f"/api/admin/users/{bob['id']}", json={"role": "admin"})
    real = admin_users._active_admins

    def slow(db):  # widen the check-then-write window
        n = real(db)
        time.sleep(0.2)
        return n

    monkeypatch.setattr(admin_users, "_active_admins", slow)
    jobs = [(admin, bob["id"]), (user, alice["id"])]
    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(lambda j: j[0].patch(f"/api/admin/users/{j[1]}", json={"role": "learner"}).status_code, jobs))
    assert codes == [200, 403]  # the loser was demoted while waiting, so it is no longer an admin
    with app.state.session_factory() as db:
        assert real(db) == 1


def test_admin_can_grant_every_role(app):
    admin, user, bob = _setup(app)
    for role in ("educator", "rnd", "admin", "learner"):
        r = admin.patch(f"/api/admin/users/{bob['id']}", json={"role": role})
        assert r.status_code == 200 and r.json()["user"]["role"] == role
