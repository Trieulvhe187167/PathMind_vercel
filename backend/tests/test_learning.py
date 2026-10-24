"""Courses, notebooks, practice + spaced repetition, mastery paths, books, reading, co-writer."""

import json

import pytest

from conftest import make_client, register
from app.services import llm


@pytest.fixture()
def mock(app):
    def handler(messages, purpose):
        last = messages[-1]["content"]
        if purpose == "quiz_generate":
            return json.dumps({"questions": [
                {"type": "mcq", "prompt": "2 + 2 = ?", "options": ["3", "4", "5"], "answer": {"correct": [1]}, "explanation": "Basic sum"},
                {"type": "true_false", "prompt": "The sky is green.", "answer": "false"},
                {"type": "short", "prompt": "Capital of France?", "answer": {"text": "Paris"}},
                {"type": "mcq", "prompt": "bad", "options": ["only one"], "answer": 0},
            ]})
        if purpose == "mastery_path":
            return json.dumps({"units": [{"title": "Basics", "lessons": [{"title": "Numbers"}, {"title": "Addition"}]},
                                         {"title": "More", "lessons": [{"title": "Subtraction"}]}]})
        if purpose == "book_outline":
            return json.dumps({"chapters": [{"title": "Intro"}, {"title": "Deep dive"}]})
        if purpose == "book_chapter":
            return "# Chapter\nText."
        if purpose == "grade":
            return json.dumps({"score": 0.7, "feedback": "Mostly right"})
        if purpose == "cowriter":
            return "IMPROVED: " + last
        return "answer"

    llm.MOCK_HANDLER = handler
    yield
    llm.MOCK_HANDLER = None


def _setup(app, plan="plus"):
    admin = make_client(app)
    register(admin, "alice")
    p = admin.post("/api/admin/ai/providers", json={"name": "Mock", "kind": "mock"}).json()["provider"]
    admin.post("/api/admin/ai/models", json={"provider_id": p["id"], "model_name": "mock-chat"})
    u = make_client(app)
    uid = register(u, "lena").json()["user"]["id"]
    if plan:
        admin.post(f"/api/admin/billing/users/{uid}/plan", json={"plan_id": plan, "days": 30})
    return admin, u


def test_free_plan_locks_paid_features(app, mock):
    _, u = _setup(app, plan=None)
    assert u.get("/api/courses").json() == {"courses": [], "locked": True}
    r = u.post("/api/courses", json={"title": "Math"})
    assert r.status_code == 402 and r.json()["detail"]["upgrade_to"]["id"] == "plus"
    assert u.post("/api/notebooks", json={"title": "N"}).status_code == 402
    assert u.get("/api/practice/questions").status_code == 402
    assert u.post("/api/writing", json={"title": "x"}).status_code == 402
    # Free plan: one mastery path and one book, ever.
    assert u.post("/api/mastery/topics", json={"title": "Arithmetic"}).status_code == 201
    assert u.post("/api/mastery/topics", json={"title": "Algebra"}).status_code == 402
    assert u.post("/api/books", json={"title": "Book one"}).status_code == 201
    assert u.post("/api/books", json={"title": "Book two"}).status_code == 402


def test_courses_and_notebooks(app, mock):
    _, u = _setup(app)
    c = u.post("/api/courses", json={"title": "Math", "color": "#f00"}).json()["course"]
    n = u.post("/api/notebooks", json={"title": "Algebra notes", "course_id": c["id"]}).json()["notebook"]
    tag = u.post("/api/tags", json={"name": "exam"}).json()["tag"]
    e = u.post(f"/api/notebooks/{n['id']}/entries", json={"title": "Quadratics", "content": "ax^2+bx+c", "tag_ids": [tag["id"]]}).json()["entry"]
    assert e["tags"][0]["name"] == "exam"
    assert len(u.get(f"/api/notebooks/{n['id']}?tag={tag['id']}").json()["entries"]) == 1
    assert u.get(f"/api/notebooks/{n['id']}?q=quadr").json()["entries"][0]["id"] == e["id"]
    s = u.post(f"/api/notebooks/{n['id']}/summarise", json={}).json()["entry"]
    assert s["kind"] == "summary"
    course = u.get(f"/api/courses/{c['id']}").json()
    assert course["course"]["counts"]["notebooks"] == 1
    # Save a chat answer into the notebook.
    sid = u.post("/api/chat/sessions", json={"course_id": c["id"]}).json()["session"]["id"]
    u.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Explain derivatives"})
    msg = u.get(f"/api/chat/sessions/{sid}").json()["messages"][1]
    x = u.post(f"/api/notebooks/{n['id']}/entries", json={"kind": "chat_excerpt", "source_message_id": msg["id"]}).json()["entry"]
    assert x["content"] == "answer" and x["title"] == "Explain derivatives"
    other = make_client(app)
    register(other, "otto")
    assert other.get(f"/api/notebooks/{n['id']}").status_code in (402, 404)


def test_practice_generate_attempt_review(app, mock):
    _, u = _setup(app)
    qs = u.post("/api/practice/generate", json={"topic": "Mixed", "count": 5}).json()["questions"]
    assert len(qs) == 3  # the invalid one was dropped
    mcq, tf, short = qs
    assert tf["answer"] == {"correct": [1]}
    review = u.get("/api/practice/review").json()
    assert review["new"] == 3 and "answer" not in review["questions"][0]
    r = u.post(f"/api/practice/questions/{mcq['id']}/attempt", json={"choice": 1}).json()
    assert r["correct"] is True and r["next_review"] > "2026"
    r2 = u.post(f"/api/practice/questions/{short['id']}/attempt", json={"text": " paris "}).json()
    assert r2["correct"] is True
    r3 = u.post(f"/api/practice/questions/{tf['id']}/attempt", json={"choice": 0}).json()
    assert r3["correct"] is False
    # The wrong one comes back soon; the right ones later.
    review = u.get("/api/practice/review").json()
    assert review["due"] == 0 and review["new"] == 0
    stats = u.get("/api/practice/stats").json()
    assert stats["attempts"] == 3 and stats["accuracy"] == pytest.approx(2 / 3, abs=0.01) and stats["streak_days"] == 1
    essay = u.post("/api/practice/questions", json={"type": "essay", "prompt": "Explain gravity", "answer": "mass attracts"}).json()["question"]
    g = u.post(f"/api/practice/questions/{essay['id']}/attempt", json={"text": "Things with mass pull each other"}).json()
    assert g["score"] == 0.7 and g["feedback"] == "Mostly right"


def test_practice_import_csv(app, mock):
    _, u = _setup(app)
    csv = ("type,prompt,option_a,option_b,option_c,answer,explanation,topic\n"
           "mcq,Pick B,x,y,z,B,because,Letters\n"
           "short,Say hi,,,,hi,,Greetings\n"
           "mcq,No options,,,,A,,\n")
    r = u.post("/api/practice/import", files={"file": ("q.csv", csv.encode(), "text/csv")}).json()
    assert r["imported"] == 2
    qs = u.get("/api/practice/questions?topic=Letters").json()["questions"]
    assert qs[0]["answer"] == {"correct": [1]}


def test_mastery_path_flow(app, mock):
    _, u = _setup(app)
    t = u.post("/api/mastery/topics", json={"title": "Arithmetic", "goal": "Add numbers"}).json()["topic"]
    lessons = [n for n in t["nodes"] if n["kind"] == "lesson"]
    assert [n["title"] for n in lessons] == ["Numbers", "Addition", "Subtraction"]
    assert [n["state"] for n in lessons] == ["available", "locked", "locked"]
    assert u.post(f"/api/mastery/nodes/{lessons[1]['id']}/start").status_code == 409
    start = u.post(f"/api/mastery/nodes/{lessons[0]['id']}/start").json()
    chat = u.get(f"/api/chat/sessions/{start['chat_session_id']}").json()
    assert chat["session"]["mode"] == "tutor"
    r = u.post(f"/api/mastery/nodes/{lessons[0]['id']}/evidence", json={"kind": "quiz", "score": 1.0}).json()
    assert r["node"]["state"] == "in_progress"  # one result is not enough
    r = u.post(f"/api/mastery/nodes/{lessons[0]['id']}/evidence", json={"kind": "quiz", "score": 1.0}).json()
    assert r["node"]["state"] == "mastered"
    t = u.get(f"/api/mastery/topics/{t['id']}").json()["topic"]
    assert [n["state"] for n in t["nodes"] if n["kind"] == "lesson"][1] == "available"
    quiz = u.post(f"/api/mastery/nodes/{lessons[1]['id']}/quiz").json()["questions"]
    assert quiz and "answer" not in quiz[0]
    # Practising the lesson quiz counts as evidence for the lesson.
    u.post(f"/api/practice/questions/{quiz[0]['id']}/attempt", json={"choice": 1})
    t = u.get(f"/api/mastery/topics/{t['id']}").json()["topic"]
    assert [n for n in t["nodes"] if n["kind"] == "lesson"][1]["mastery"] > 0
    assert u.post(f"/api/mastery/topics/{t['id']}/regenerate").json()["topic"]["path_version"] == 2


def test_books_and_reading_and_cowriter(app, mock):
    _, u = _setup(app)
    b = u.post("/api/books", json={"title": "Learning Python", "topic": "Python basics"}).json()["book"]
    assert b["status"] == "ready" and [c["title"] for c in b["chapters"]] == ["Intro", "Deep dive"]
    assert b["chapters"][0]["content"].startswith("# Chapter") and b["task"]["status"] == "done"
    ch = b["chapters"][1]
    u.put(f"/api/books/{b['id']}/chapters/{ch['id']}", json={"title": "Deeper", "content": "mine"})
    assert u.get(f"/api/books/{b['id']}").json()["book"]["chapters"][1]["content"] == "mine"

    r = u.post("/api/reading", files={"file": ("notes.md", b"# Cells\nCells have membranes.\n\n# Atoms\nAtoms have nuclei.", "text/markdown")}).json()["document"]
    assert r["kind"] == "text"
    assert "membranes" in u.get(f"/api/reading/{r['id']}/text").json()["text"]
    u.put(f"/api/reading/{r['id']}/progress", json={"progress": 0.5, "position": {"scroll": 120}})
    a = u.post(f"/api/reading/{r['id']}/annotations", json={"quote": "Cells have membranes.", "note": "important"}).json()["annotation"]
    got = u.get(f"/api/reading/{r['id']}").json()
    assert got["document"]["progress"] == 0.5 and got["annotations"][0]["id"] == a["id"]
    assert u.post(f"/api/reading/{r['id']}/ask", json={"question": "What do atoms have?"}).json()["answer"] == "answer"
    v = u.post("/api/reading", data={"url": "https://www.youtube.com/watch?v=abc"}).json()["document"]
    assert v["kind"] == "video"

    d = u.post("/api/writing", json={"title": "Essay", "content": "first draft"}).json()["document"]
    s = u.post(f"/api/writing/{d['id']}/ai", json={"action": "improve", "text": "first draft"}).json()["suggestion"]
    assert s == "IMPROVED: first draft"
    u.put(f"/api/writing/{d['id']}", json={"title": "Essay", "content": s, "snapshot": "before_ai_edit"})
    got = u.get(f"/api/writing/{d['id']}").json()
    assert got["document"]["content"] == s and got["revisions"][0]["reason"] == "before_ai_edit"
    rev = u.get(f"/api/writing/{d['id']}/revisions/{got['revisions'][0]['id']}").json()["revision"]
    assert rev["content"] == "first draft"
    assert u.post(f"/api/writing/{d['id']}/ai", json={"action": "nope", "text": "x"}).status_code == 422
