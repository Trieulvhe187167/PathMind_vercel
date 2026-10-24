"""Knowledge tree: permissions, drafts and publishing, versions, documents, contributions, rewards, import/export."""

import io
import json
import zipfile

from openpyxl import Workbook

from conftest import make_client, register

DOC = b"""# Safety rules
Always wear gloves in the lab. Goggles protect your eyes.

## Chemicals
Store acids below eye level. Never mix bleach and ammonia.

## Fire
Know where the extinguisher is. Pull, aim, squeeze, sweep.
"""


def _people(app):
    admin = make_client(app)
    register(admin, "alice")
    ids = {}
    clients = {}
    for name, role in (("rina", "rnd"), ("eddie", "educator"), ("lena", "learner")):
        c = make_client(app)
        ids[name] = register(c, name, email=f"{name}@x.com").json()["user"]["id"]
        if role != "learner":
            admin.patch(f"/api/admin/users/{ids[name]}", json={"role": role})
        clients[name] = c
    return admin, clients["rina"], clients["eddie"], clients["lena"], ids


def _domain(rnd, name="Lab safety"):
    return rnd.post("/api/tree/domains", json={"name": name, "answer_policy": "strict"}).json()["domain"]


def test_permissions(app):
    admin, rnd, edu, learner, _ = _people(app)
    d = _domain(rnd)
    assert d["slug"] == "lab-safety"
    assert learner.get("/api/tree/domains").status_code == 403
    assert edu.get("/api/tree/domains").status_code == 403
    assert admin.get("/api/tree/domains").status_code == 200  # admins may read...
    assert admin.post("/api/tree/domains", json={"name": "X"}).status_code == 403  # ...but not edit
    assert admin.get(f"/api/tree/domains/{d['id']}/tree").status_code == 200
    # Educators only see domain names (to file contributions).
    assert edu.get("/api/contribute/domains").json()["domains"] == [{"id": d["id"], "name": "Lab safety", "description": ""}]
    assert learner.get("/api/contribute/domains").status_code == 403


def test_draft_publish_versions_restore(app):
    admin, rnd, *_ = _people(app)
    d = _domain(rnd)
    cs = rnd.post("/api/tree/change-sets", json={"domain_id": d["id"], "title": "First topics"}).json()["change_set"]
    a = rnd.post(f"/api/tree/change-sets/{cs['id']}/changes", json={"op": "add_node", "temp_key": "a",
                                                                    "payload": {"title": "Chemistry", "summary": "Acids and bases"}}).json()["change"]
    rnd.post(f"/api/tree/change-sets/{cs['id']}/changes", json={"op": "add_node", "payload": {"title": "Acids", "parent_temp_key": "a"}})
    skipped = rnd.post(f"/api/tree/change-sets/{cs['id']}/changes", json={"op": "add_node", "payload": {"title": "Skip me"}}).json()["change"]
    rnd.patch(f"/api/tree/change-sets/{cs['id']}/changes/{skipped['id']}", json={"accepted": False})
    assert a["temp_key"] == "a"
    # Nothing is live before publishing.
    assert rnd.get(f"/api/tree/domains/{d['id']}/tree").json()["nodes"] == []
    pub = rnd.post(f"/api/tree/change-sets/{cs['id']}/publish").json()
    assert pub["counts"]["added"] == 2
    assert rnd.post(f"/api/tree/change-sets/{cs['id']}/publish").status_code == 409
    nodes = rnd.get(f"/api/tree/domains/{d['id']}/tree").json()["nodes"]
    assert [n["title"] for n in nodes] == ["Chemistry"] and nodes[0]["children"][0]["title"] == "Acids"
    chem, acids = nodes[0]["id"], nodes[0]["children"][0]["id"]

    cs2 = rnd.post("/api/tree/change-sets", json={"domain_id": d["id"], "title": "Edit"}).json()["change_set"]
    rnd.post(f"/api/tree/change-sets/{cs2['id']}/changes", json={"op": "update_node", "node_id": chem, "payload": {"title": "Chemistry basics"}})
    rnd.post(f"/api/tree/change-sets/{cs2['id']}/publish")
    node = rnd.get(f"/api/tree/nodes/{chem}").json()
    assert node["node"]["title"] == "Chemistry basics" and [v["version"] for v in node["versions"]] == [2, 1]
    # Moving a node under its own child is refused, and nothing from that draft is applied.
    cs3 = rnd.post("/api/tree/change-sets", json={"domain_id": d["id"], "title": "Bad move"}).json()["change_set"]
    rnd.post(f"/api/tree/change-sets/{cs3['id']}/changes", json={"op": "update_node", "node_id": acids, "payload": {"title": "Changed"}})
    rnd.post(f"/api/tree/change-sets/{cs3['id']}/changes", json={"op": "move_node", "node_id": chem, "payload": {"parent_id": acids}})
    assert rnd.post(f"/api/tree/change-sets/{cs3['id']}/publish").status_code == 422
    assert rnd.get(f"/api/tree/nodes/{acids}").json()["node"]["title"] == "Acids"
    # Restore version 1 through a draft.
    r = rnd.post(f"/api/tree/nodes/{chem}/restore", json={"version": 1}).json()["change_set"]
    assert r["origin"] == "restore"
    rnd.post(f"/api/tree/change-sets/{r['id']}/publish")
    assert rnd.get(f"/api/tree/nodes/{chem}").json()["node"]["title"] == "Chemistry"
    # Archive hides the branch.
    cs4 = rnd.post("/api/tree/change-sets", json={"domain_id": d["id"], "title": "Archive"}).json()["change_set"]
    rnd.post(f"/api/tree/change-sets/{cs4['id']}/changes", json={"op": "archive_node", "node_id": chem})
    rnd.post(f"/api/tree/change-sets/{cs4['id']}/publish")
    assert rnd.get(f"/api/tree/domains/{d['id']}/tree").json()["nodes"] == []
    assert len(rnd.get(f"/api/tree/domains/{d['id']}/tree?archived=true").json()["nodes"]) == 1
    actions = [e["action"] for e in admin.get("/api/admin/audit?action=tree.").json()["entries"]]
    assert actions.count("tree.publish") == 4


def test_document_propose_publish_and_chunks(app):
    _, rnd, *_ = _people(app)
    d = _domain(rnd)
    doc = rnd.post("/api/tree/documents", files={"file": ("safety.md", DOC, "text/markdown")},
                   data={"domain_id": d["id"], "doc_version": "v1"}).json()["document"]
    assert doc["status"] == "pending" and doc["text_length"] > 50
    cs = rnd.post(f"/api/tree/documents/{doc['id']}/propose", json={}).json()["change_set"]
    ops = [c["op"] for c in cs["changes"]]
    assert ops == ["add_node", "add_node", "add_node", "attach_document", "verify_document"]
    titles = [c["payload"]["title"] for c in cs["changes"] if c["op"] == "add_node"]
    assert titles == ["Safety rules", "Chemicals", "Fire"]
    assert cs["changes"][1]["payload"]["parent_temp_key"] == "n1"
    rnd.post(f"/api/tree/change-sets/{cs['id']}/publish")
    got = rnd.get(f"/api/tree/documents/{doc['id']}").json()
    assert got["document"]["status"] == "verified" and got["document"]["review_due_at"]
    assert len(got["chunks"]) == 3 and all(c["node_id"] for c in got["chunks"])
    tree = rnd.get(f"/api/tree/domains/{d['id']}/tree").json()["nodes"]
    assert tree[0]["title"] == "Safety rules" and [c["title"] for c in tree[0]["children"]] == ["Chemicals", "Fire"]
    # A new version supersedes the old one and takes its place.
    v2 = rnd.post("/api/tree/documents", files={"file": ("safety2.md", DOC + b"\n## Spills\nUse the spill kit.\n", "text/markdown")},
                  data={"domain_id": d["id"], "doc_version": "v2", "supersedes_id": doc["id"]}).json()["document"]
    rnd.post(f"/api/tree/documents/{v2['id']}/verify", json={"review_days": 30})
    old = rnd.get(f"/api/tree/documents/{doc['id']}").json()
    assert old["document"]["status"] == "superseded" and not any(c["is_active"] for c in old["chunks"])
    new = rnd.get(f"/api/tree/documents/{v2['id']}").json()["document"]
    assert new["node_ids"] and new["chunks"] == 4
    assert rnd.post(f"/api/tree/documents/{v2['id']}/retire", json={"note": "outdated"}).json()["document"]["status"] == "retired"
    assert rnd.get("/api/tree/overview").json()["verified_documents"] == 0


def test_contribution_workflow_rewards_and_gaps(app):
    from app.services import knowledge_tree as kt

    admin, rnd, edu, learner, ids = _people(app)
    d = _domain(rnd)
    with app.state.session_factory() as db:
        gap = kt.record_gap(db, "How do I store acids safely?", d["id"])
        kt.record_gap(db, "how do i STORE acids safely", d["id"])
        db.commit()
        gap_id = gap.id
    gaps = edu.get("/api/contribute/gaps").json()["gaps"]
    assert gaps[0]["ask_count"] == 2 and gaps[0]["domain"] == "Lab safety"

    c = edu.post("/api/contribute/contributions", json={"title": "Acid storage guide", "description": "Covers storage",
                                                         "gap_id": gap_id}).json()["contribution"]
    assert edu.post(f"/api/contribute/contributions/{c['id']}/submit").status_code == 422  # no file
    edu.post(f"/api/contribute/contributions/{c['id']}/files", files={"file": ("acids.md", DOC, "text/markdown")})
    assert edu.post(f"/api/contribute/contributions/{c['id']}/submit").status_code == 422  # no domain
    edu.put(f"/api/contribute/contributions/{c['id']}", json={"title": "Acid storage guide", "domain_id": d["id"], "gap_id": gap_id})
    assert edu.post(f"/api/contribute/contributions/{c['id']}/submit").json()["contribution"]["status"] == "submitted"
    assert edu.get("/api/contribute/gaps").json()["gaps"][0]["status"] == "requested"
    queue = rnd.get("/api/rnd/contributions").json()["contributions"]
    assert [x["id"] for x in queue] == [c["id"]]
    rnd.post(f"/api/rnd/contributions/{c['id']}/claim")
    assert rnd.post(f"/api/rnd/contributions/{c['id']}/request-changes", json={"note": ""}).status_code == 422
    rnd.post(f"/api/rnd/contributions/{c['id']}/request-changes", json={"note": "Add a section on fire."})
    edu.post(f"/api/contribute/contributions/{c['id']}/comments", json={"body": "Done, see new file."})
    edu.post(f"/api/contribute/contributions/{c['id']}/files", files={"file": ("acids2.md", DOC + b"\n## More\nx\n", "text/markdown")})
    again = edu.post(f"/api/contribute/contributions/{c['id']}/submit").json()["contribution"]
    assert again["revision"] == 2
    rnd.post(f"/api/rnd/contributions/{c['id']}/claim")
    ap = rnd.post(f"/api/rnd/contributions/{c['id']}/approve", json={"note": "Great"}).json()
    assert len(ap["documents"]) == 1  # only files of the current revision
    cs = rnd.post(f"/api/rnd/contributions/{c['id']}/propose", json={}).json()["change_set"]
    assert cs["contribution_id"] == c["id"]
    rnd.post(f"/api/tree/change-sets/{cs['id']}/publish")
    view = edu.get(f"/api/contribute/contributions/{c['id']}").json()["contribution"]
    assert view["status"] == "integrated" and [e["kind"] for e in view["events"]].count("comment") == 1
    rewards = edu.get("/api/contribute/rewards").json()
    assert rewards["balance"] == 80 and {b["badge"] for b in rewards["badges"]} == {"first_contribution"}
    assert rewards["leaderboard"][0]["name"] == "eddie"
    assert rnd.get("/api/rnd/gaps?status=resolved").json()["gaps"][0]["id"] == gap_id
    rnd.post(f"/api/rnd/contributions/{c['id']}/bonus", json={"points": 20, "note": "Very clear"})
    # Redeem 5 plan days (20 points per day by default).
    r = edu.post("/api/contribute/rewards/redeem", json={"kind": "plan_days", "amount": 5}).json()
    assert r["balance"] == 0 and r["summary"]["plan"]["id"] == "plus"
    assert edu.post("/api/contribute/rewards/redeem", json={"kind": "plan_days", "amount": 1}).status_code == 409
    # Learners and R&D have no educator pages.
    assert learner.get("/api/contribute/contributions").status_code == 403
    assert edu.get("/api/rnd/contributions").status_code == 403
    assert admin.get("/api/rnd/contributions?status=all").status_code == 200


def test_import_and_export(app):
    admin, rnd, *_ = _people(app)
    data = {"format": "pathmind-tree", "version": 1, "domains": [{"name": "HR", "answer_policy": "strict", "nodes": [
        {"title": "Leave", "summary": "Annual leave", "children": [{"title": "Sick leave", "body": "Up to 30 days"}]}]}]}
    r = rnd.post("/api/tree/import", files={"file": ("hr.json", json.dumps(data).encode(), "application/json")}).json()
    assert r["nodes"] == 2
    cs = r["change_sets"][0]
    rnd.post(f"/api/tree/change-sets/{cs['id']}/publish")
    hr = next(d for d in rnd.get("/api/tree/domains").json()["domains"] if d["name"] == "HR")
    assert hr["answer_policy"] == "strict" and hr["node_count"] == 2
    # Markdown into an existing domain, under a node.
    leave = rnd.get(f"/api/tree/domains/{hr['id']}/tree").json()["nodes"][0]["id"]
    md = rnd.post("/api/tree/import", files={"file": ("x.md", b"# Maternity\nSix months\n## Paternity\nTwo weeks", "text/markdown")},
                  data={"domain_id": hr["id"], "parent_node_id": leave}).json()
    rnd.post(f"/api/tree/change-sets/{md['change_sets'][0]['id']}/publish")
    tree = rnd.get(f"/api/tree/domains/{hr['id']}/tree").json()["nodes"]
    assert [c["title"] for c in tree[0]["children"]] == ["Sick leave", "Maternity"]
    # Excel rows with paths.
    wb = Workbook()
    ws = wb.active
    ws.append(["domain", "path", "summary", "body"])
    ws.append(["IT", "Accounts > Passwords", "Rules", "12+ chars"])
    ws.append(["IT", "Accounts > MFA", "", "Use the app"])
    buf = io.BytesIO()
    wb.save(buf)
    x = rnd.post("/api/tree/import", files={"file": ("it.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert x["nodes"] == 3
    assert rnd.post("/api/tree/import", files={"file": ("bad.json", b"{oops", "application/json")}).status_code == 422

    exp = admin.get(f"/api/tree/export?format=json&domain_id={hr['id']}").json()
    assert exp["format"] == "pathmind-tree" and exp["domains"][0]["nodes"][0]["children"][1]["title"] == "Maternity"
    # Re-importing the export reproduces the tree in a new draft.
    again = rnd.post("/api/tree/import", files={"file": ("hr.json", json.dumps(exp).encode(), "application/json")}).json()
    assert again["nodes"] == 4
    assert admin.get("/api/tree/export?format=xlsx").content[:2] == b"PK"
    assert "## Leave" in admin.get(f"/api/tree/export?format=md&domain_id={hr['id']}").text
    z = zipfile.ZipFile(io.BytesIO(admin.get("/api/tree/export?format=zip").content))
    assert {"tree.json", "tree.md"} <= set(z.namelist())
    assert any(e["action"] == "tree.export" for e in admin.get("/api/admin/audit").json()["entries"])
