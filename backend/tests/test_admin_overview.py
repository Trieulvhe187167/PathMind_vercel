from conftest import make_client, register


def _setup(app):
    admin = make_client(app)
    register(admin, "alice")
    eve = make_client(app)
    register(eve, "eve", role="educator", educator_reason="chemistry")
    bob = make_client(app)
    bob_user = register(bob, "bob").json()["user"]
    return admin, eve, bob, bob_user


def test_dashboard_numbers(app):
    admin, eve, bob, bob_user = _setup(app)
    admin.patch(f"/api/admin/users/{bob_user['id']}", json={"is_active": False})
    d = admin.get("/api/admin/dashboard").json()
    assert d["users"]["total"] == 3
    assert d["users"]["inactive"] == 1
    assert d["users"]["by_role"] == {"learner": 2, "educator": 0, "rnd": 0, "admin": 1}
    assert d["users"]["new_7d"] == 3
    assert d["pending_role_requests"] == 1
    assert [u["username"] for u in d["recent_signups"]] == ["bob", "eve", "alice"]
    assert d["recent_activity"][0]["action"] == "user.deactivate"
    assert d["recent_activity"][0]["actor"] == "alice" and d["recent_activity"][0]["target"] == "bob"


def test_audit_log_paging_and_filter(app):
    admin, eve, bob, bob_user = _setup(app)
    for active in (False, True, False, True):
        admin.patch(f"/api/admin/users/{bob_user['id']}", json={"is_active": active})
    page = admin.get("/api/admin/audit?limit=3").json()
    assert len(page["entries"]) == 3 and page["next_before_id"] == page["entries"][-1]["id"]
    rest = admin.get(f"/api/admin/audit?limit=3&before_id={page['next_before_id']}").json()
    assert all(e["id"] < page["next_before_id"] for e in rest["entries"])
    assert rest["next_before_id"] is None  # 5 rows in total: 1 request + 4 activation changes
    only = admin.get("/api/admin/audit?action=role_request").json()["entries"]
    assert [e["action"] for e in only] == ["role_request.create"]


def test_overview_is_admin_only(app):
    admin, eve, bob, _ = _setup(app)
    for path in ("/api/admin/dashboard", "/api/admin/audit"):
        assert eve.get(path).status_code == 403
        assert make_client(app).get(path).status_code == 401


def test_require_roles_dependency():
    import pytest
    from fastapi import HTTPException

    from app.deps import require_roles
    from app.models import User

    dep = require_roles("rnd")
    assert dep(User(username="r", password_hash="x", role="rnd")).role == "rnd"
    with pytest.raises(HTTPException) as e:
        dep(User(username="a", password_hash="x", role="admin"))
    assert e.value.status_code == 403
