from conftest import make_client, register


def _admin(app):
    admin = make_client(app)
    register(admin, "alice")
    return admin


def _login(app, login, password):
    c = make_client(app)
    return c, c.post("/api/auth/login", json={"login": login, "password": password})


def test_admin_creates_account_with_temporary_password(app):
    admin = _admin(app)
    r = admin.post("/api/admin/users", json={"username": "rina", "email": "Rina@Corp.com", "role": "rnd"})
    assert r.status_code == 201, r.text
    body = r.json()
    temp = body["temporary_password"]
    assert len(temp) == 12 and body["user"]["role"] == "rnd" and body["user"]["email"] == "rina@corp.com"
    assert body["user"]["must_change_password"] is True

    rina, r = _login(app, "rina", temp)
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    # Until the temporary password is replaced, only "who am I" and "change password" work.
    assert rina.get("/api/auth/me").status_code == 200
    blocked = rina.get("/api/me/notifications")
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "password_change_required"
    same = rina.post("/api/auth/password", json={"current_password": temp, "new_password": temp})
    assert same.status_code == 422
    ok = rina.post("/api/auth/password", json={"current_password": temp, "new_password": "rnd-pass-42"})
    assert ok.status_code == 200
    assert rina.get("/api/auth/me").json()["user"]["must_change_password"] is False
    assert rina.get("/api/me/notifications").status_code == 200

    audit = admin.get("/api/admin/audit?action=user.create").json()["entries"]
    assert audit[0]["target"] == "rina" and audit[0]["data"] == {"role": "rnd"}


def test_create_account_validation(app):
    admin = _admin(app)
    assert admin.post("/api/admin/users", json={"username": "x"}).status_code == 422
    assert admin.post("/api/admin/users", json={"username": "bob", "role": "wizard"}).status_code == 422
    assert admin.post("/api/admin/users", json={"username": "ALICE"}).status_code == 409
    assert admin.post("/api/admin/users", json={"username": "bob", "password": "short"}).status_code == 422
    r = admin.post("/api/admin/users", json={"username": "bob", "password": "chosen-pass-1"})
    assert r.json()["temporary_password"] == "chosen-pass-1"
    learner = make_client(app)
    register(learner, "carol")
    assert learner.post("/api/admin/users", json={"username": "dave"}).status_code == 403


def test_reset_password_signs_out_and_forces_change(app):
    admin = _admin(app)
    bob_client = make_client(app)
    bob = register(bob_client, "bob").json()["user"]
    r = admin.post(f"/api/admin/users/{bob['id']}/reset-password")
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert bob_client.get("/api/auth/me").status_code == 401  # old session ended
    assert _login(app, "bob", "secret123")[1].status_code == 401
    c, r = _login(app, "bob", temp)
    assert r.json()["user"]["must_change_password"] is True
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.post(f"/api/admin/users/{me['id']}/reset-password").status_code == 409
    assert admin.post("/api/admin/users/missing/reset-password").status_code == 404


def test_invitation_flow(app):
    admin = _admin(app)
    r = admin.post("/api/admin/invitations", json={"email": "New@Corp.com", "role": "rnd", "days": 3})
    assert r.status_code == 201, r.text
    token, inv = r.json()["token"], r.json()["invitation"]
    assert inv["status"] == "open" and inv["email"] == "new@corp.com" and inv["invited_by"] == "alice"
    # A second open invitation for the same address is refused.
    assert admin.post("/api/admin/invitations", json={"email": "new@corp.com"}).status_code == 409

    guest = make_client(app)
    view = guest.post("/api/auth/invitations/lookup", json={"token": token})
    assert view.status_code == 200 and view.json()["invitation"]["role"] == "rnd"
    assert guest.post("/api/auth/invitations/lookup", json={"token": "not-a-token"}).status_code == 404

    bad = guest.post("/api/auth/invitations/accept", json={"token": token, "username": "newbie", "password": "pass-1234", "confirm_password": "other-1234"})
    assert bad.status_code == 422
    ok = guest.post("/api/auth/invitations/accept", json={"token": token, "username": "newbie", "full_name": "New Person", "password": "pass-1234",
                          "confirm_password": "pass-1234"})
    assert ok.status_code == 201, ok.text
    user = ok.json()["user"]
    assert user["role"] == "rnd" and user["email"] == "new@corp.com" and user["must_change_password"] is False
    assert guest.get("/api/auth/me").json()["user"]["username"] == "newbie"  # signed in

    # Used links stop working.
    assert make_client(app).post("/api/auth/invitations/lookup", json={"token": token}).status_code == 404
    again = make_client(app).post("/api/auth/invitations/accept", json={"token": token, "username": "other", "password": "pass-1234", "confirm_password": "pass-1234"})
    assert again.status_code == 404
    listed = admin.get("/api/admin/invitations?status=accepted").json()["invitations"]
    assert listed[0]["accepted_username"] == "newbie"
    # The email now has an account, so it cannot be invited again.
    assert admin.post("/api/admin/invitations", json={"email": "new@corp.com"}).status_code == 409


def test_revoke_invitation(app):
    admin = _admin(app)
    r = admin.post("/api/admin/invitations", json={"email": "x@corp.com"})
    token, inv_id = r.json()["token"], r.json()["invitation"]["id"]
    assert admin.delete(f"/api/admin/invitations/{inv_id}").json()["invitation"]["status"] == "revoked"
    assert admin.delete(f"/api/admin/invitations/{inv_id}").status_code == 409
    assert make_client(app).post("/api/auth/invitations/lookup", json={"token": token}).status_code == 404
    assert admin.get("/api/admin/invitations").json()["invitations"] == []  # default: open only
    assert len(admin.get("/api/admin/invitations?status=all").json()["invitations"]) == 1
    # Revoked, so the address can be invited again.
    assert admin.post("/api/admin/invitations", json={"email": "x@corp.com"}).status_code == 201
    assert admin.post("/api/admin/invitations", json={"email": "y@corp.com", "days": 99}).status_code == 422


def test_invitation_username_taken(app):
    admin = _admin(app)
    token = admin.post("/api/admin/invitations", json={"email": "z@corp.com"}).json()["token"]
    r = make_client(app).post("/api/auth/invitations/accept", json={"token": token, "username": "Alice", "password": "pass-1234", "confirm_password": "pass-1234"})
    assert r.status_code == 409 and r.json()["detail"]["field"] == "username"
    # The link still works after a failed attempt.
    assert make_client(app).post("/api/auth/invitations/lookup", json={"token": token}).status_code == 200


def test_invitation_void_when_inviter_loses_admin(app):
    admin = _admin(app)
    second = make_client(app)
    bob = register(second, "bob").json()["user"]
    admin.patch(f"/api/admin/users/{bob['id']}", json={"role": "admin"})
    token = second.post("/api/admin/invitations", json={"email": "w@corp.com", "role": "admin"}).json()["token"]
    assert make_client(app).post("/api/auth/invitations/lookup", json={"token": token}).status_code == 200
    admin.patch(f"/api/admin/users/{bob['id']}", json={"role": "learner"})
    guest = make_client(app)
    assert guest.post("/api/auth/invitations/lookup", json={"token": token}).status_code == 404
    r = guest.post("/api/auth/invitations/accept",
                   json={"token": token, "username": "wendy", "password": "pass-1234", "confirm_password": "pass-1234"})
    assert r.status_code == 404


def test_invitation_list_rejects_unknown_status(app):
    admin = _admin(app)
    assert admin.get("/api/admin/invitations?status=bogus").status_code == 422
