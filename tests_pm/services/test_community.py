"""Community hub: publish / install / like skills, forum posts, votes, comments, moderation."""

from __future__ import annotations

import io
from types import SimpleNamespace
import zipfile

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base, get_db
from pathmind.multi_user.context import get_current_user, set_current_user, user_from_token_payload

USERS = {
    "u_alice": {"id": "u_alice", "username": "alice", "role": "user"},
    "u_bob": {"id": "u_bob", "username": "bob", "role": "user"},
    "u_admin": {"id": "u_admin", "username": "root", "role": "admin"},
}

SKILL_MD = """---
name: socratic
description: Ask guiding questions instead of giving answers.
always: true
---

# Socratic tutor

Never give the answer directly.
"""


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import pathmind.services.skill as skill_pkg
    from pathmind.api.routers import community
    from pathmind.services.skill.service import SkillService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()

    from pathmind.services import entitlements

    # Plan limits have their own tests (test_entitlements.py).
    monkeypatch.setattr(entitlements, "current_entitlements", lambda user=None: dict(entitlements.UNLIMITED))

    services = {uid: SkillService(root=tmp_path / uid / "skills", builtin_root=None) for uid in USERS}
    monkeypatch.setattr(skill_pkg, "get_skill_service", lambda: services[get_current_user().id])
    monkeypatch.setattr(community, "_usernames", lambda: {k: v["username"] for k, v in USERS.items()})

    docs = {"abcdef12": SimpleNamespace(content="# Essay coach\n\nGive feedback on structure first.", title="Essay coach")}
    import pathmind.co_writer.storage as storage

    monkeypatch.setattr(
        storage, "get_co_writer_storage", lambda: SimpleNamespace(load_document=lambda d: docs.get(d))
    )

    state = {"who": "u_alice"}

    async def as_user():
        u = USERS[state["who"]]
        set_current_user(
            user_from_token_payload(SimpleNamespace(user_id=u["id"], username=u["username"], role=u["role"]))
        )

    app = FastAPI()
    app.include_router(community.router, prefix="/api/community", dependencies=[Depends(as_user)])
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)

    def act(who):
        state["who"] = who
        return client

    yield act, services
    session.close()


def _publish_text(act, who="u_alice", **extra):
    body = {"source": "text", "content": SKILL_MD, "title": "Socratic Tutor", "category": "academics", "tags": ["Tutoring"]}
    body.update(extra)
    res = act(who).post("/api/community/skills", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_publish_browse_install_like(env):
    act, services = env
    skill = _publish_text(act)
    assert skill["slug"] == "socratic-tutor" and skill["version"] == 1
    assert "always" not in skill["content"] and "name: socratic-tutor" in skill["content"]
    assert skill["tags"] == ["tutoring"]

    listing = act("u_bob").get("/api/community/skills?category=academics").json()
    assert [s["slug"] for s in listing["skills"]] == ["socratic-tutor"]
    assert {c["id"]: c["count"] for c in listing["categories"]}["academics"] == 1
    assert listing["skills"][0]["installed_as"] is None and not listing["skills"][0]["can_edit"]

    res = act("u_bob").post("/api/community/skills/socratic-tutor/install", json={})
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "socratic-tutor"
    bob_skills = {s.name: s for s in services["u_bob"].list_skills()}
    assert "socratic-tutor" in bob_skills and "community" in bob_skills["socratic-tutor"].tags
    installed = services["u_bob"].get_detail("socratic-tutor").content
    assert "always" not in installed and "Never give the answer" in installed

    # Installing again: conflict unless forced; the count stays per person.
    assert act("u_bob").post("/api/community/skills/socratic-tutor/install", json={}).status_code == 409
    again = act("u_bob").post("/api/community/skills/socratic-tutor/install", json={"force": True}).json()
    assert again["install_count"] == 1
    detail = act("u_bob").get("/api/community/skills/socratic-tutor").json()
    assert detail["installed_as"] == "socratic-tutor"

    assert act("u_bob").post("/api/community/skills/socratic-tutor/like").json() == {"liked": True, "like_count": 1}
    assert act("u_bob").post("/api/community/skills/socratic-tutor/like").json()["like_count"] == 0

    # Same title → a unique slug.
    assert _publish_text(act, "u_bob")["slug"] == "socratic-tutor-2"


def test_publish_from_my_skill_cowriter_and_zip(env):
    act, services = env
    services["u_alice"].create("flashcards", "Make spaced-repetition cards.", "Turn notes into Q/A cards.")
    (services["u_alice"].root / "flashcards" / "references").mkdir()
    (services["u_alice"].root / "flashcards" / "references" / "format.md").write_text("Q: …\nA: …", encoding="utf-8")
    res = act("u_alice").post(
        "/api/community/skills", json={"source": "skill", "skill_name": "flashcards", "title": "Flashcards"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["files"] == ["references/format.md"]
    assert res.json()["description"] == "Make spaced-repetition cards."

    # Someone else's / missing skill.
    missing = act("u_bob").post(
        "/api/community/skills", json={"source": "skill", "skill_name": "flashcards", "title": "X"}
    )
    assert missing.status_code == 404

    doc = act("u_alice").post(
        "/api/community/skills",
        json={"source": "cowriter", "doc_id": "abcdef12", "title": "Essay coach", "description": "Feedback on essays."},
    )
    assert doc.status_code == 200, doc.text
    assert "Give feedback on structure" in doc.json()["content"]
    no_desc = act("u_alice").post(
        "/api/community/skills", json={"source": "cowriter", "doc_id": "abcdef12", "title": "Essay 2"}
    )
    assert no_desc.status_code == 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/SKILL.md", SKILL_MD)
        zf.writestr("pkg/examples/one.md", "example")
        zf.writestr("pkg/tool.exe", b"MZ\x00\x01")
    res = act("u_alice").post(
        "/api/community/skills/upload",
        data={"title": "Zipped tutor", "category": "educators", "tags": "a, b"},
        files={"file": ("pkg.zip", buf.getvalue(), "application/zip")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["files"] == ["examples/one.md"] and "tool.exe" in body["skipped_files"]
    assert body["tags"] == ["a", "b"] and body["category"] == "educators"

    bad = act("u_alice").post(
        "/api/community/skills/upload",
        data={"title": "Bad"},
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
    )
    assert bad.status_code == 400


def test_only_author_edits_and_admin_hides(env):
    act, _ = env
    _publish_text(act)
    assert act("u_bob").put("/api/community/skills/socratic-tutor", json={"title": "Mine"}).status_code == 403
    res = act("u_alice").put(
        "/api/community/skills/socratic-tutor",
        json={"source": "text", "content": SKILL_MD.replace("directly", "directly, ever"), "description": "v2"},
    )
    assert res.status_code == 200 and res.json()["version"] == 2 and res.json()["description"] == "v2"
    assert act("u_bob").delete("/api/community/skills/socratic-tutor").status_code == 403
    assert act("u_admin").delete("/api/community/skills/socratic-tutor").status_code == 200
    assert act("u_bob").get("/api/community/skills/socratic-tutor").status_code == 404
    assert act("u_admin").get("/api/community/skills/socratic-tutor").json()["hidden"] is True
    assert act("u_bob").get("/api/community/skills").json()["total"] == 0


def test_forum_posts_votes_comments(env):
    act, _ = env
    _publish_text(act)
    post = act("u_alice").post(
        "/api/community/posts",
        json={"title": "How do I use Socratic Tutor?", "body": "Details **here**", "category": "help", "skill_slug": "socratic-tutor"},
    ).json()
    assert post["score"] == 1 and post["my_vote"] == 1 and post["skill"]["slug"] == "socratic-tutor"
    pid = post["id"]

    assert act("u_bob").post(f"/api/community/posts/{pid}/vote", json={"value": 1}).json()["score"] == 2
    assert act("u_bob").post(f"/api/community/posts/{pid}/vote", json={"value": -1}).json()["score"] == 0
    assert act("u_bob").post(f"/api/community/posts/{pid}/vote", json={"value": 0}).json()["score"] == 1

    c1 = act("u_bob").post(f"/api/community/posts/{pid}/comments", json={"body": "Try asking it why."}).json()
    reply = act("u_alice").post(
        f"/api/community/posts/{pid}/comments", json={"body": "Thanks!", "parent_id": c1["id"]}
    ).json()
    assert reply["parent_id"] == c1["id"]
    assert act("u_alice").post(f"/api/community/comments/{c1['id']}/vote", json={"value": 1}).json()["score"] == 2

    assert act("u_alice").delete(f"/api/community/comments/{c1['id']}").status_code == 403
    assert act("u_bob").delete(f"/api/community/comments/{c1['id']}").status_code == 200
    thread = act("u_alice").get(f"/api/community/posts/{pid}").json()
    by_id = {c["id"]: c for c in thread["comments"]}
    assert by_id[c1["id"]]["deleted"] and by_id[c1["id"]]["body"] == ""
    assert by_id[reply["id"]]["body"] == "Thanks!"
    assert thread["post"]["comment_count"] == 1

    listing = act("u_bob").get("/api/community/posts?category=help").json()
    assert [p["id"] for p in listing["posts"]] == [pid]
    assert act("u_bob").get("/api/community/posts?q=socratic").json()["total"] == 1


def test_forum_moderation_and_sorting(env):
    act, _ = env
    old = act("u_alice").post("/api/community/posts", json={"title": "Old but gold", "category": "general"}).json()
    new = act("u_bob").post("/api/community/posts", json={"title": "Brand new", "category": "study"}).json()
    act("u_bob").post(f"/api/community/posts/{old['id']}/vote", json={"value": 1})

    assert [p["id"] for p in act("u_bob").get("/api/community/posts?sort=new").json()["posts"]] == [new["id"], old["id"]]
    assert act("u_bob").get("/api/community/posts?sort=top").json()["posts"][0]["id"] == old["id"]

    assert act("u_bob").post("/api/community/posts", json={"title": "News!", "category": "announcements"}).status_code == 403
    assert act("u_bob").post(f"/api/community/posts/{new['id']}/moderate", json={"pinned": True}).status_code == 403
    act("u_admin").post(f"/api/community/posts/{new['id']}/moderate", json={"pinned": True, "locked": True})
    hot = act("u_alice").get("/api/community/posts").json()["posts"]
    assert hot[0]["id"] == new["id"] and hot[0]["pinned"]
    assert act("u_alice").post(f"/api/community/posts/{new['id']}/comments", json={"body": "hi"}).status_code == 403

    assert act("u_bob").delete(f"/api/community/posts/{old['id']}").status_code == 403
    assert act("u_admin").delete(f"/api/community/posts/{old['id']}").status_code == 200
    assert act("u_alice").get(f"/api/community/posts/{old['id']}").status_code == 404


def test_messages_follow_language(env, monkeypatch):
    import pathmind.services.i18n as i18n

    act, _ = env
    monkeypatch.setattr(i18n, "current_language", lambda default="en": "zh")
    assert act("u_bob").get("/api/community/posts/nope").json()["detail"] == "帖子不存在或已被删除。"


def test_reports_queue_and_resolution(env):
    act, _ = env
    post = act("u_alice").post("/api/community/posts", json={"title": "Buy cheap followers!!", "category": "general"}).json()
    ok_post = act("u_alice").post("/api/community/posts", json={"title": "Legit question", "category": "help"}).json()

    assert act("u_alice").post(
        "/api/community/reports", json={"target_type": "post", "target_id": post["id"], "reason": "spam"}
    ).status_code == 400  # own content
    for who in ("u_bob", "u_admin"):
        res = act(who).post(
            "/api/community/reports",
            json={"target_type": "post", "target_id": post["id"], "reason": "spam", "details": "ads"},
        )
        assert res.status_code == 200, res.text
    # Reporting twice doesn't duplicate.
    act("u_bob").post("/api/community/reports", json={"target_type": "post", "target_id": post["id"], "reason": "other"})
    act("u_bob").post("/api/community/reports", json={"target_type": "post", "target_id": ok_post["id"], "reason": "other"})

    assert act("u_bob").get("/api/community/reports").status_code == 403
    queue = act("u_admin").get("/api/community/reports").json()
    assert queue["open_targets"] == 2
    top = queue["items"][0]
    assert top["target_id"] == post["id"] and top["count"] == 2 and top["title"] == "Buy cheap followers!!"
    assert {r["reporter"] for r in top["reports"]} == {"bob", "root"}

    act("u_admin").post("/api/community/reports/resolve", json={"target_type": "post", "target_id": post["id"], "action": "remove"})
    act("u_admin").post("/api/community/reports/resolve", json={"target_type": "post", "target_id": ok_post["id"], "action": "dismiss"})
    assert act("u_bob").get(f"/api/community/posts/{post['id']}").status_code == 404
    assert act("u_bob").get(f"/api/community/posts/{ok_post['id']}").status_code == 200
    assert act("u_admin").get("/api/community/reports").json()["open_targets"] == 0
    resolved = act("u_admin").get("/api/community/reports?status=resolved").json()["items"]
    assert {i["status"] for i in resolved} == {"removed", "dismissed"}

    # The author hears that a moderator removed their post.
    notes = act("u_alice").get("/api/community/notifications").json()
    assert notes["items"][0]["kind"] == "content_removed"


def test_reply_and_install_notifications(env):
    act, _ = env
    _publish_text(act)
    post = act("u_alice").post("/api/community/posts", json={"title": "Stuck on integrals", "category": "help"}).json()
    c1 = act("u_bob").post(f"/api/community/posts/{post['id']}/comments", json={"body": "Try substitution."}).json()
    act("u_alice").post(f"/api/community/posts/{post['id']}/comments", json={"body": "Thanks!", "parent_id": c1["id"]})
    act("u_alice").post(f"/api/community/posts/{post['id']}/comments", json={"body": "self note"})
    act("u_bob").post("/api/community/skills/socratic-tutor/install", json={})

    alice = act("u_alice").get("/api/community/notifications").json()
    assert alice["unread"] == 2
    kinds = [(n["kind"], n["actor"]) for n in alice["items"]]
    assert ("post_comment", "bob") in kinds and ("skill_installed", "bob") in kinds
    comment_note = next(n for n in alice["items"] if n["kind"] == "post_comment")
    assert comment_note["post_id"] == post["id"] and comment_note["post_title"] == "Stuck on integrals"
    assert comment_note["preview"] == "Try substitution."

    bob = act("u_bob").get("/api/community/notifications").json()
    assert [n["kind"] for n in bob["items"]] == ["comment_reply"]
    assert act("u_bob").get("/api/community/notifications/unread-count").json() == {"unread": 1}

    after = act("u_alice").post("/api/community/notifications/read", json={"ids": [comment_note["id"]]}).json()
    assert after["unread"] == 1
    assert act("u_alice").post("/api/community/notifications/read", json={"all": True}).json()["unread"] == 0
    # Someone else can't mark mine.
    act("u_alice").post(f"/api/community/posts/{post['id']}/comments", json={"body": "ping", "parent_id": c1["id"]})
    act("u_alice").post("/api/community/notifications/read", json={"all": True})
    assert act("u_bob").get("/api/community/notifications/unread-count").json()["unread"] == 2
