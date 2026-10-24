"""Personas, skills, partners, memory, friends, study rooms, community (hub, forum, reports)."""

from conftest import make_client, register


def _setup(app, plan="plus"):
    admin = make_client(app)
    register(admin, "alice")
    p = admin.post("/api/admin/ai/providers", json={"name": "Mock", "kind": "mock"}).json()["provider"]
    admin.post("/api/admin/ai/models", json={"provider_id": p["id"], "model_name": "mock-chat"})
    users = {}
    for name in ("bob", "carol"):
        c = make_client(app)
        uid = register(c, name, email=f"{name}@x.com").json()["user"]["id"]
        if plan:
            admin.post(f"/api/admin/billing/users/{uid}/plan", json={"plan_id": plan, "days": 30})
        users[name] = (c, uid)
    return admin, users


def test_personas_skills_partners(app):
    admin, users = _setup(app)
    bob, _ = users["bob"]
    personas = bob.get("/api/personas").json()["personas"]
    assert any(p["builtin"] and p["name"] == "Teacher" for p in personas)
    mine = bob.post("/api/personas", json={"name": "Strict coach", "system_prompt": "Be strict."}).json()["persona"]
    teacher = next(p for p in personas if p["name"] == "Teacher")
    assert bob.put(f"/api/personas/{teacher['id']}", json={"name": "x"}).status_code == 404  # built-ins are read-only
    skills = bob.get("/api/skills").json()
    assert skills["allowance"]["limit"] == 5 and any(s["builtin"] for s in skills["skills"])
    assert bob.post("/api/skills", json={"name": "Bad Name!", "content": "x"}).status_code == 422
    sk = bob.post("/api/skills", json={"name": "mnemonics", "content": "# Mnemonics"}).json()["skill"]
    assert bob.post("/api/skills", json={"name": "mnemonics", "content": "x"}).status_code == 409
    p = bob.post("/api/partners", json={"name": "Max", "persona_id": mine["id"], "model": "mock-chat"}).json()["partner"]
    assert p["mine"] and p["model"] == "mock-chat"
    assert bob.post("/api/partners", json={"name": "Bad", "model": "nonexistent"}).status_code == 422
    # A chat with the partner uses its persona.
    sid = bob.post("/api/chat/sessions", json={"partner_id": p["id"]}).json()["session"]["id"]
    assert bob.post(f"/api/chat/sessions/{sid}/messages", json={"content": "hi"}).status_code == 200
    carol, carol_id = users["carol"]
    # Sharing needs friendship.
    assert bob.post(f"/api/partners/{p['id']}/share", json={"user_id": carol_id}).status_code == 409
    bob.post("/api/social/friends/request", json={"target": "carol"})
    carol.post("/api/social/friends/respond", json={"user_id": users["bob"][1], "action": "accept"})
    bob.post(f"/api/partners/{p['id']}/share", json={"user_id": carol_id})
    shared = carol.get("/api/partners").json()["partners"][0]
    assert shared["name"] == "Max" and shared["mine"] is False and shared["system_prompt"] == ""
    assert carol.post("/api/chat/sessions", json={"partner_id": p["id"]}).status_code == 201
    # Unfriending stops the sharing.
    carol.post("/api/social/friends/respond", json={"user_id": users["bob"][1], "action": "remove"})
    assert carol.get("/api/partners").json()["partners"] == []
    assert bob.delete(f"/api/skills/{sk['id']}").json() == {"ok": True}


def test_memory_and_sharing(app):
    admin, users = _setup(app)
    bob, _ = users["bob"]
    m = bob.post("/api/memories", json={"kind": "goal", "content": "Pass the IELTS exam", "importance": 5}).json()["memory"]
    assert bob.get("/api/memories").json()["memories"][0]["id"] == m["id"]
    bob.put(f"/api/memories/{m['id']}", json={"kind": "goal", "content": "Pass IELTS 7.0", "importance": 5})
    assert bob.put("/api/memories/sharing", json={"share": True}).json() == {"sharing": True}
    ins = admin.get("/api/admin/insights").json()
    assert ins["people_sharing"] == 1 and any(i["label"] == "memory:goal" for i in ins["insights"])
    assert "IELTS" not in str(ins)  # only counts, never text
    bob.put("/api/memories/sharing", json={"share": False})
    assert admin.get("/api/admin/insights").json()["people_sharing"] == 0
    bob.delete("/api/memories")
    assert bob.get("/api/memories").json()["memories"] == []


def test_friends_and_rooms(app):
    admin, users = _setup(app)
    (bob, bob_id), (carol, carol_id) = users["bob"], users["carol"]
    assert bob.post("/api/social/friends/request", json={"target": "nobody"}).status_code == 404
    assert bob.post("/api/social/friends/request", json={"target": "carol@x.com"}).json() == {"status": "pending"}
    assert carol.get("/api/social/friends").json()["incoming"][0]["username"] == "bob"
    # Asking back accepts.
    assert carol.post("/api/social/friends/request", json={"target": "bob"}).json() == {"status": "accepted"}
    assert bob.get("/api/social/friends").json()["friends"][0]["username"] == "carol"
    partner = bob.post("/api/partners", json={"name": "Max"}).json()["partner"]
    room = bob.post("/api/social/rooms", json={"title": "Exam prep", "partner_id": partner["id"]}).json()["room"]
    code = room["code"]
    assert len(code) == 6
    assert carol.get(f"/api/social/rooms/{code}/messages").status_code == 403
    carol.post(f"/api/social/rooms/{code.lower()}/join")
    r = carol.post(f"/api/social/rooms/{code}/messages", json={"content": "Hi all", "ask_partner": True}).json()["messages"]
    assert [m["role"] for m in r] == ["user", "partner"]
    msgs = bob.get(f"/api/social/rooms/{code}/messages").json()["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "partner"]
    after = msgs[-1]["id"]
    assert bob.get(f"/api/social/rooms/{code}/messages?after={after}").json()["messages"] == []
    assert carol.delete(f"/api/social/rooms/{code}").status_code == 403
    bob.delete(f"/api/social/rooms/{code}")
    assert carol.get(f"/api/social/rooms/{code}").status_code == 404
    # Blocking.
    carol.post("/api/social/friends/respond", json={"user_id": bob_id, "action": "block"})
    assert bob.post("/api/social/friends/request", json={"target": "carol"}).status_code == 409


def test_community_hub_forum_reports(app):
    admin, users = _setup(app)
    (bob, _), (carol, _) = users["bob"], users["carol"]
    sk = bob.post("/api/community/skills", json={"title": "Pomodoro Coach", "category": "productivity",
                                                  "content": "# Pomodoro\nWork 25 minutes."}).json()["skill"]
    assert sk["slug"] == "pomodoro-coach"
    hub = carol.get("/api/community/skills?q=pomodoro").json()["skills"]
    assert hub[0]["title"] == "Pomodoro Coach"
    inst = carol.post("/api/community/skills/pomodoro-coach/install", json={}).json()
    assert inst["install_count"] == 1
    assert carol.post("/api/community/skills/pomodoro-coach/install", json={}).status_code == 409
    assert any(s["origin"] == "community" for s in carol.get("/api/skills").json()["skills"])
    assert carol.post("/api/community/skills/pomodoro-coach/like").json() == {"liked": True, "like_count": 1}
    assert any(n["kind"] == "skill_installed" for n in bob.get("/api/me/notifications").json()["notifications"])

    post = bob.post("/api/community/posts", json={"category": "help", "title": "How to memorise faster?", "body": "Tips?"}).json()["post"]
    assert carol.post("/api/community/posts", json={"category": "announcements", "title": "Hello there"}).status_code == 403
    c1 = carol.post(f"/api/community/posts/{post['id']}/comments", json={"body": "Spaced repetition!"}).json()["comment"]
    bob.post(f"/api/community/posts/{post['id']}/comments", json={"body": "Thanks", "parent_id": c1["id"]})
    kinds = [n["kind"] for n in carol.get("/api/me/notifications").json()["notifications"]]
    assert "comment_reply" in kinds
    assert carol.post("/api/community/vote", json={"target_type": "post", "target_id": post["id"], "value": 1}).json()["score"] == 1
    assert carol.post("/api/community/vote", json={"target_type": "post", "target_id": post["id"], "value": -1}).json()["score"] == -1
    got = bob.get(f"/api/community/posts/{post['id']}").json()
    assert got["post"]["comment_count"] == 2 and got["comments"][1]["parent_id"] == c1["id"]

    assert bob.post("/api/community/reports", json={"target_type": "post", "target_id": post["id"]}).status_code == 409
    carol.post("/api/community/reports", json={"target_type": "post", "target_id": post["id"], "reason": "spam"})
    reports = admin.get("/api/community/reports").json()["reports"]
    assert reports[0]["reports"] == 1 and reports[0]["reasons"] == {"spam": 1}
    admin.post("/api/community/reports/resolve", json={"target_type": "post", "target_id": post["id"], "action": "remove"})
    assert admin.get("/api/community/reports").json()["reports"] == []
    assert bob.get(f"/api/community/posts/{post['id']}").json()["post"]["deleted"] is True
    assert any(n["kind"] == "content_removed" for n in bob.get("/api/me/notifications").json()["notifications"])


def test_free_plan_has_no_community(app):
    admin, users = _setup(app, plan=None)
    bob, _ = users["bob"]
    r = bob.get("/api/community/posts")
    assert r.status_code == 402
