"""AI tutor chat: streaming, grounding in the company tree, policies, citations, feedback, KBs, memory."""

import json

import pytest

from conftest import make_client, register
from app.services import llm

DOC = b"""# Chemical storage
Store acids in a ventilated acid cabinet below eye level. Keep acids away from bases and oxidisers.

# Eye protection
Wear safety goggles whenever handling chemicals in the laboratory.
"""


def sse(resp):
    events = []
    for block in resp.text.split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) >= 2 and lines[0].startswith("event:"):
            events.append((lines[0][6:].strip(), json.loads(lines[1][5:].strip())))
    return events


def setup(app, policy="prefer"):
    admin = make_client(app)
    register(admin, "alice")
    p = admin.post("/api/admin/ai/providers", json={"name": "Mock", "kind": "mock"}).json()["provider"]
    admin.post("/api/admin/ai/models", json={"provider_id": p["id"], "model_name": "mock-chat"})
    rnd = make_client(app)
    rid = register(rnd, "rina").json()["user"]["id"]
    admin.patch(f"/api/admin/users/{rid}", json={"role": "rnd"})
    d = rnd.post("/api/tree/domains", json={"name": "Lab safety", "answer_policy": policy}).json()["domain"]
    doc = rnd.post("/api/tree/documents", files={"file": ("lab.md", DOC, "text/markdown")},
                   data={"domain_id": d["id"], "title": "Lab handbook"}).json()["document"]
    cs = rnd.post(f"/api/tree/documents/{doc['id']}/propose", json={}).json()["change_set"]
    rnd.post(f"/api/tree/change-sets/{cs['id']}/publish")
    learner = make_client(app)
    register(learner, "lena")
    return admin, rnd, learner, d, doc


def ask(client, sid, text, **extra):
    r = client.post(f"/api/chat/sessions/{sid}/messages", json={"content": text, **extra})
    assert r.status_code == 200, r.text
    return sse(r)


def test_no_model_configured(app):
    c = make_client(app)
    register(c, "alice")
    sid = c.post("/api/chat/sessions", json={}).json()["session"]["id"]
    r = c.post(f"/api/chat/sessions/{sid}/messages", json={"content": "hi"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ai_not_configured"


def test_grounded_answer_with_citations_and_visibility(app):
    admin, rnd, learner, d, doc = setup(app)
    sid = learner.post("/api/chat/sessions", json={}).json()["session"]["id"]
    ev = ask(learner, sid, "How should I store acids in the lab?")
    kinds = [e for e, _ in ev]
    assert kinds[0] == "meta" and "delta" in kinds and kinds[-2:] == ["sources", "done"]
    sources = dict(ev)["sources"]
    assert sources["grounding"] == "verified"
    cite = sources["citations"][0]
    assert cite["kind"] == "tree" and cite["title"] == "Lab handbook" and cite["section"] == "Chemical storage"
    assert "quote" not in cite and "document_id" not in cite  # default detail: title + section only
    done = dict(ev)["done"]["message"]
    assert "[1]" in done["content"] and done["grounding"] == "verified"
    s = learner.get(f"/api/chat/sessions/{sid}").json()
    assert s["session"]["title"].startswith("How should I store acids") and len(s["messages"]) == 2
    # The domain decides how much a learner sees.
    rnd.put(f"/api/tree/domains/{d['id']}", json={"name": "Lab safety", "citation_detail": "quote"})
    c = learner.get(f"/api/chat/sessions/{sid}").json()["messages"][1]["citations"][0]
    assert "ventilated acid cabinet" in c["quote"]
    rnd.put(f"/api/tree/domains/{d['id']}", json={"name": "Lab safety", "citation_detail": "none"})
    c = learner.get(f"/api/chat/sessions/{sid}").json()["messages"][1]["citations"][0]
    assert c["title"] is None and c["verified"] is True
    # Learners can never download the verified document itself.
    assert learner.get(f"/api/tree/documents/{doc['id']}/download").status_code == 403
    assert learner.get(f"/api/files/{doc['file_id']}/download").status_code == 404


def test_strict_policy_refuses_and_logs_gap(app):
    admin, rnd, learner, d, _ = setup(app, policy="strict")
    sid = learner.post("/api/chat/sessions", json={}).json()["session"]["id"]
    ev = ask(learner, sid, "What is the lab safety budget approval process?")
    done = dict(ev)["done"]["message"]
    assert done["grounding"] == "none" and "R&D" in done["content"]
    gaps = rnd.get("/api/rnd/gaps").json()["gaps"]
    assert gaps and gaps[0]["question"].startswith("What is the lab safety budget") and gaps[0]["domain"] == "Lab safety"
    # Off-topic questions in a deployment with a tree but no matching domain fall back to general knowledge.
    ev2 = ask(learner, sid, "Write a haiku about autumn leaves")
    assert dict(ev2)["done"]["message"]["grounding"] == "general"


def test_prefer_policy_marks_unverified(app):
    seen = {}

    def handler(messages, purpose):
        seen["system"] = messages[0]["content"]
        return "General answer."

    admin, rnd, learner, d, _ = setup(app)
    llm.MOCK_HANDLER = handler
    try:
        sid = learner.post("/api/chat/sessions", json={}).json()["session"]["id"]
        ev = ask(learner, sid, "What is the lab safety budget approval process?")
    finally:
        llm.MOCK_HANDLER = None
    assert dict(ev)["done"]["message"]["grounding"] == "general"
    assert "Chưa kiểm duyệt:" in seen["system"]  # learners default to Vietnamese


def test_feedback_reaches_rnd(app):
    admin, rnd, learner, *_ = setup(app)
    sid = learner.post("/api/chat/sessions", json={}).json()["session"]["id"]
    mid = dict(ask(learner, sid, "How should I store acids?"))["done"]["message"]["id"]
    assert learner.post(f"/api/chat/messages/{mid}/feedback", json={"rating": -1, "reason": "bogus"}).status_code == 422
    learner.post(f"/api/chat/messages/{mid}/feedback", json={"rating": -1, "reason": "outdated", "comment": "Rules changed"})
    fb = rnd.get("/api/rnd/feedback").json()["feedback"]
    assert fb[0]["reason"] == "outdated" and fb[0]["question"] == "How should I store acids?"
    assert rnd.get("/api/tree/overview").json()["feedback_new"] == 1
    other = make_client(app)
    register(other, "otto")
    assert other.post(f"/api/chat/messages/{mid}/feedback", json={"rating": 1}).status_code == 404


def test_rnd_try_shows_debug(app):
    admin, rnd, learner, d, _ = setup(app)
    r = rnd.post("/api/rnd/try", json={"question": "Where do acids go?"}).json()
    assert r["grounding"] == "verified" and r["debug"]["tree"][0]["title"] == "Lab handbook"
    assert r["citations"][0]["learner_sees"]["title"] == "Lab handbook" and r["citations"][0]["full"]["quote"]
    assert learner.post("/api/rnd/try", json={"question": "x?"}).status_code == 403
    assert rnd.get("/api/rnd/gaps").json()["gaps"] == []  # trying does not log gaps


def test_personal_kb_index_search_chat_and_share(app):
    admin, rnd, learner, *_ = setup(app)
    kb = learner.post("/api/kb", json={"name": "Biology notes"}).json()["knowledge_base"]
    r = learner.post(f"/api/kb/{kb['id']}/documents",
                     files=[("files", ("cells.md", b"# Mitochondria\nThe mitochondria is the powerhouse of the cell.", "text/markdown"))])
    assert r.status_code == 201, r.text
    got = learner.get(f"/api/kb/{kb['id']}").json()
    assert got["knowledge_base"]["status"] == "ready" and got["documents"][0]["status"] == "indexed"
    assert got["task"]["status"] == "done"
    hits = learner.post(f"/api/kb/{kb['id']}/search", json={"query": "powerhouse of the cell"}).json()["results"]
    assert hits and "Mitochondria" in hits[0]["text"]
    sid = learner.post("/api/chat/sessions", json={"kb_ids": [kb["id"]]}).json()["session"]["id"]
    ev = ask(learner, sid, "What is the powerhouse of the cell mitochondria?")
    src = dict(ev)["sources"]
    assert src["grounding"] == "personal_kb" and src["citations"][0]["kind"] == "kb"
    # Sharing.
    other = make_client(app)
    register(other, "otto")
    assert other.get(f"/api/kb/{kb['id']}").status_code == 404
    learner.post(f"/api/kb/{kb['id']}/access", json={"login": "otto"})
    assert other.get(f"/api/kb/{kb['id']}").status_code == 200
    assert other.post(f"/api/kb/{kb['id']}/documents", files=[("files", ("x.md", b"x", "text/markdown"))]).status_code == 404
    # Free plan: two knowledge bases at most.
    learner.post("/api/kb", json={"name": "Second"})
    third = learner.post("/api/kb", json={"name": "Third"})
    assert third.status_code == 402 and third.json()["detail"]["code"] == "plan_required"


def test_cited_contribution_rewards_and_memory(app):
    admin, rnd, learner, d, _ = setup(app)
    edu = make_client(app)
    eid = register(edu, "eddie").json()["user"]["id"]
    admin.patch(f"/api/admin/users/{eid}", json={"role": "educator"})
    c = edu.post("/api/contribute/contributions", json={"title": "Fume hoods", "domain_id": d["id"]}).json()["contribution"]
    edu.post(f"/api/contribute/contributions/{c['id']}/files",
             files={"file": ("hood.md", b"# Fume hood\nAlways keep the fume hood sash low when working with volatile solvents.", "text/markdown")})
    edu.post(f"/api/contribute/contributions/{c['id']}/submit")
    rnd.post(f"/api/rnd/contributions/{c['id']}/approve", json={})
    cs = rnd.post(f"/api/rnd/contributions/{c['id']}/propose", json={}).json()["change_set"]
    rnd.post(f"/api/tree/change-sets/{cs['id']}/publish")

    def handler(messages, purpose):
        if purpose == "memory":
            return json.dumps({"memories": [{"kind": "goal", "content": "Preparing for a lab exam", "importance": 4}]})
        return "Keep the sash low [1]."

    llm.MOCK_HANDLER = handler
    try:
        sid = learner.post("/api/chat/sessions", json={}).json()["session"]["id"]
        for q in ("How high should the fume hood sash be with volatile solvents?", "and for acids?",
                  "what about goggles?", "fume hood sash again?"):
            ask(learner, sid, q)
    finally:
        llm.MOCK_HANDLER = None
    pts = edu.get("/api/contribute/rewards").json()
    assert any(r["reason"] == "cited" for r in pts["ledger"])
    from app.models import Memory

    with app.state.session_factory() as db:
        assert db.query(Memory).filter_by(content="Preparing for a lab exam").count() == 1
