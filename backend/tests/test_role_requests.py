from conftest import make_client, register


def _admin(app):
    a = make_client(app)
    register(a, "alice")
    return a


def test_status_tells_the_signup_page_what_is_allowed(app):
    s = make_client(app).get("/api/auth/status").json()
    assert (s["has_users"], s["educator_signup"], s["educator_requires_approval"]) == (False, True, True)


def test_signup_as_educator_starts_as_learner_with_pending_request(app):
    admin = _admin(app)
    c = make_client(app)
    r = register(c, "eve", role="educator", educator_reason="I teach chemistry")
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "learner"
    assert r.json()["role_request"]["status"] == "pending"
    assert c.get("/api/me/role-request").json()["role_request"]["reason"] == "I teach chemistry"
    # a second request while one is pending is refused
    assert c.post("/api/me/role-request", json={"reason": "again"}).status_code == 409
    pending = admin.get("/api/admin/role-requests").json()["role_requests"]
    assert [p["user"]["username"] for p in pending] == ["eve"]


def test_first_account_is_admin_even_if_it_picks_educator(app):
    r = register(make_client(app), "alice", role="educator")
    assert r.json()["user"]["role"] == "admin" and r.json()["role_request"] is None


def test_admin_approves_and_user_is_told(app):
    admin = _admin(app)
    c = make_client(app)
    register(c, "eve", role="educator")
    rid = admin.get("/api/admin/role-requests").json()["role_requests"][0]["id"]
    r = admin.post(f"/api/admin/role-requests/{rid}/approve", json={})
    assert r.status_code == 200 and r.json()["role_request"]["status"] == "approved"
    assert c.get("/api/auth/me").json()["user"]["role"] == "educator"
    notes = c.get("/api/me/notifications").json()
    assert notes["unread"] == 1 and notes["notifications"][0]["kind"] == "role_request_approved"
    c.post("/api/me/notifications/read", json={})
    assert c.get("/api/me/notifications").json()["unread"] == 0
    # deciding twice is refused
    assert admin.post(f"/api/admin/role-requests/{rid}/reject", json={"note": "x"}).status_code == 409
    assert admin.get("/api/admin/role-requests").json()["role_requests"] == []


def test_reject_needs_a_note_and_keeps_learner(app):
    admin = _admin(app)
    c = make_client(app)
    register(c, "eve", role="educator")
    rid = admin.get("/api/admin/role-requests").json()["role_requests"][0]["id"]
    r = admin.post(f"/api/admin/role-requests/{rid}/reject", json={"note": ""})
    assert r.status_code == 422
    r = admin.post(f"/api/admin/role-requests/{rid}/reject", json={"note": "Please use your company email"})
    assert r.json()["role_request"]["status"] == "rejected"
    assert c.get("/api/auth/me").json()["user"]["role"] == "learner"
    assert "company email" in c.get("/api/me/notifications").json()["notifications"][0]["preview"]
    # after a rejection the learner may ask again
    assert c.post("/api/me/role-request", json={"reason": "now with company email"}).status_code == 201


def test_only_admins_see_and_decide_requests(app):
    _admin(app)
    c = make_client(app)
    register(c, "eve", role="educator")
    assert c.get("/api/admin/role-requests").status_code == 403
    assert c.post("/api/admin/role-requests/x/approve", json={}).status_code == 403
    assert c.get("/api/admin/settings/registration").status_code == 403
    assert make_client(app).get("/api/me/role-request").status_code == 401


def test_learner_can_cancel_own_request(app):
    _admin(app)
    c = make_client(app)
    register(c, "eve", role="educator")
    assert c.delete("/api/me/role-request").status_code == 200
    assert c.get("/api/me/role-request").json()["role_request"] is None
    assert c.delete("/api/me/role-request").status_code == 404


def test_settings_turn_off_approval_or_educator_signup(app):
    admin = _admin(app)
    r = admin.put("/api/admin/settings/registration", json={"educator_requires_approval": False, "bogus": 1})
    s = r.json()["settings"]
    assert s["allow_educator_signup"] is True and s["educator_requires_approval"] is False and "bogus" not in s
    c = make_client(app)
    r = register(c, "eve", role="educator")
    assert r.json()["user"]["role"] == "educator" and r.json()["role_request"] is None
    admin.put("/api/admin/settings/registration", json={"allow_educator_signup": False})
    assert make_client(app).get("/api/auth/status").json()["educator_signup"] is False
    r = register(make_client(app), "bob", role="educator")
    assert r.status_code == 403
    assert register(make_client(app), "bob").status_code == 201  # plain learner sign-up still works


def test_admin_setting_role_by_hand_settles_pending_request_and_is_audited(app):
    admin = _admin(app)
    c = make_client(app)
    eve = register(c, "eve", role="educator").json()["user"]
    admin.patch(f"/api/admin/users/{eve['id']}", json={"role": "educator"})
    assert admin.get("/api/admin/role-requests").json()["role_requests"] == []
    done = admin.get("/api/admin/role-requests?status=approved").json()["role_requests"]
    assert done[0]["decision_note"] == "Role changed by an administrator."
    from sqlalchemy import select

    from app.models import AuditLog

    with app.state.session_factory() as db:
        actions = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions == ["role_request.create", "user.role_change"]


def test_approving_a_deactivated_account_is_refused(app):
    admin = _admin(app)
    c = make_client(app)
    eve = register(c, "eve", role="educator").json()["user"]
    admin.patch(f"/api/admin/users/{eve['id']}", json={"is_active": False})
    rid = admin.get("/api/admin/role-requests").json()["role_requests"][0]["id"]
    assert admin.post(f"/api/admin/role-requests/{rid}/approve", json={}).status_code == 409


def test_signup_response_carries_the_saved_request(app):
    _admin(app)
    c = make_client(app)
    rr = register(c, "eve", role="educator", educator_reason="maths").json()["role_request"]
    assert rr["id"] and rr["created_at"] and rr["decision_note"] == ""
    assert c.get("/api/me/role-request").json()["role_request"]["id"] == rr["id"]


def test_refused_educator_signup_leaves_no_account_and_cancel_is_audited(app):
    admin = _admin(app)
    admin.put("/api/admin/settings/registration", json={"allow_educator_signup": False})
    assert register(make_client(app), "bob", role="educator").status_code == 403
    users = admin.get("/api/admin/users").json()["users"]
    assert [u["username"] for u in users] == ["alice"]
    admin.put("/api/admin/settings/registration", json={"allow_educator_signup": True})
    c = make_client(app)
    register(c, "eve", role="educator")
    assert c.delete("/api/me/role-request").status_code == 200
    from sqlalchemy import select

    from app.models import AuditLog

    with app.state.session_factory() as db:
        actions = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions == ["settings.registration", "settings.registration", "role_request.create", "role_request.cancel"]
