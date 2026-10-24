"""Learner groups and bulk import of learners + plans."""

import io

from openpyxl import Workbook

from conftest import make_client, register


def _admin(app):
    c = make_client(app)
    register(c, "alice")
    return c


CSV = (
    "email;Họ tên;role;gói;số ngày;nhóm\n"
    "an@corp.com;An Nguyen;learner;plus;90;Sales\n"
    "BINH@corp.com;Binh Tran;educator;pro;;Sales\n"
    "bad-email;X;learner;plus;10;\n"
    "an@corp.com;Dup;learner;plus;10;\n"
    "carl@corp.com;Carl;wizard;nope;-3;\n"
    "bob@corp.com;Bob existing;;plus;0;Old\n"
)


def test_import_preview_apply_with_passwords(app):
    admin = _admin(app)
    bob = make_client(app)
    register(bob, "bob", email="bob@corp.com")
    r = admin.post("/api/admin/import/learners", files={"file": ("learners.csv", CSV.encode("utf-8"), "text/csv")},
                   data={"default_plan": "plus", "mode": "password"})
    assert r.status_code == 201, r.text
    imp, rows = r.json()["import"], r.json()["rows"]
    assert imp["status"] == "previewed" and imp["total_rows"] == 6
    by_row = {x["row"]: x for x in rows}
    assert by_row[2]["status"] == "pending" and by_row[2]["action"] == "create_user"
    assert by_row[3]["data"]["email"] == "binh@corp.com" and by_row[3]["data"]["days"] == 30
    assert by_row[4]["status"] == "error" and "invalid e-mail" in by_row[4]["error"]
    assert "twice" in by_row[5]["error"]
    assert all(s in by_row[6]["error"] for s in ("role", "unknown plan", "days"))
    assert by_row[7]["action"] == "update_user" and by_row[7]["data"]["days"] == 0
    assert imp["ok_rows"] == 3 and imp["error_rows"] == 3

    report = admin.get(f"/api/admin/import/{imp['id']}/report.csv?errors_only=true").content.decode("utf-8-sig")
    assert report.count("\n") == 4  # header + 3 errors

    done = admin.post(f"/api/admin/import/{imp['id']}/apply").json()
    assert done["summary"] == {"created": 2, "updated": 1, "plans_assigned": 3, "invitations": 0}
    creds = {c["email"]: c for c in done["credentials"]}
    assert set(creds) == {"an@corp.com", "binh@corp.com"}
    assert admin.post(f"/api/admin/import/{imp['id']}/apply").status_code == 409

    an = make_client(app)
    login = an.post("/api/auth/login", json={"login": "an@corp.com", "password": creds["an@corp.com"]["password"]}).json()
    assert login["user"]["must_change_password"] is True and login["user"]["role"] == "learner"
    binh_role = make_client(app).post("/api/auth/login", json={"login": creds["binh@corp.com"]["username"],
                                                               "password": creds["binh@corp.com"]["password"]}).json()["user"]["role"]
    assert binh_role == "educator"
    assert bob.get("/api/billing/summary").json()["subscription"]["period_end"] is None  # days 0 = never expires
    groups = {g["name"]: g for g in admin.get("/api/admin/groups").json()["groups"]}
    assert groups["Sales"]["members"] == 2 and groups["Old"]["members"] == 1


def test_import_with_invitations_applies_plan_on_accept(app):
    admin = _admin(app)
    wb = Workbook()
    ws = wb.active
    ws.append(["Email", "Full name", "Plan", "Expires on", "Group"])
    ws.append(["dan@corp.com", "Dan", "pro", "2099-01-31", "Interns"])
    buf = io.BytesIO()
    wb.save(buf)
    r = admin.post("/api/admin/import/learners", files={"file": ("l.xlsx", buf.getvalue(), "application/octet-stream")},
                   data={"mode": "invite"}).json()
    assert r["rows"][0]["status"] == "pending", r
    done = admin.post(f"/api/admin/import/{r['import']['id']}/apply").json()
    assert done["summary"]["invitations"] == 1
    token = done["credentials"][0]["token"]
    guest = make_client(app)
    acc = guest.post("/api/auth/invitations/accept", json={"token": token, "username": "dan", "password": "pass-1234",
                                                           "confirm_password": "pass-1234"})
    assert acc.status_code == 201, acc.text
    s = guest.get("/api/billing/summary").json()
    assert s["plan"]["id"] == "pro" and s["subscription"]["period_end"].startswith("2099-01")
    assert admin.get("/api/admin/groups").json()["groups"][0]["name"] == "Interns"


def test_templates_and_bad_files(app):
    admin = _admin(app)
    assert admin.get("/api/admin/import/template").content.decode("utf-8-sig").startswith("email,full_name")
    assert admin.get("/api/admin/import/template?format=xlsx").content[:2] == b"PK"
    bad = admin.post("/api/admin/import/learners", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
    assert bad.status_code == 422
    nohead = admin.post("/api/admin/import/learners", files={"file": ("x.csv", b"name\nAn\n", "text/csv")})
    assert nohead.status_code == 422


def test_groups_and_group_plans(app):
    admin = _admin(app)
    users = []
    for name in ("bob", "carol"):
        c = make_client(app)
        users.append(register(c, name, email=f"{name}@x.com").json()["user"])
    g = admin.post("/api/admin/groups", json={"name": "Team A"}).json()["group"]
    assert admin.post("/api/admin/groups", json={"name": "team a"}).status_code == 409
    r = admin.post(f"/api/admin/groups/{g['id']}/members", json={"emails": ["bob@x.com", "carol@x.com", "ghost@x.com"]}).json()
    assert r["added"] == 2 and r["not_found"] == ["ghost@x.com"]
    assert admin.post(f"/api/admin/groups/{g['id']}/plan", json={"plan_id": "plus", "days": 60}).json() == {"updated": 2}
    members = admin.get(f"/api/admin/groups/{g['id']}/members").json()["members"]
    assert {m["plan_id"] for m in members} == {"plus"}
    assert admin.post(f"/api/admin/groups/{g['id']}/plan/revoke").json() == {"updated": 2}
    members = admin.get(f"/api/admin/groups/{g['id']}/members").json()["members"]
    assert {m["plan_id"] for m in members} == {"free"}
    assert admin.delete(f"/api/admin/groups/{g['id']}/members/{users[0]['id']}").json() == {"ok": True}
    assert admin.delete(f"/api/admin/groups/{g['id']}").json() == {"ok": True}
