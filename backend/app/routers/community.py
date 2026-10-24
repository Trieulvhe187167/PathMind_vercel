"""Community: Skill Hub (share and install skills), forum (posts, nested comments, votes), reports + moderation."""

from __future__ import annotations

from datetime import timedelta
import re
import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db, require_admin
from ..models import (
    CommunitySkill, CommunitySkillInstall, ContentReport, ForumComment, ForumPost, Skill, User, Vote, utcnow,
)
from ..services import audit, billing
from ..services.notify import notify

router = APIRouter()  # /api/community
CATEGORIES = ("academics", "languages", "skills-interests", "companions", "educators", "productivity", "other")
FORUM = ("general", "help", "skills", "study", "feedback", "announcements")
REASONS = ("spam", "harassment", "inappropriate", "misinformation", "copyright", "other")


def iso(d):
    return d.isoformat() + "Z" if d else None


def _feature(db: Session, user: User) -> None:
    billing.require_feature(db, user, "community")


def _rate(db: Session, model, author_col, user: User, limit: int, hours: int, what: str) -> None:
    since = utcnow() - timedelta(hours=hours)
    created = model.created_at
    n = db.scalar(select(func.count()).select_from(model).where(author_col == user.id, created >= since)) or 0
    if n >= limit:
        raise HTTPException(status_code=429, detail=f"Too many {what}. Try again later.")


def _author(db: Session, uid: str | None) -> dict | None:
    u = db.get(User, uid) if uid else None
    return {"id": u.id, "username": u.username, "avatar_url": u.avatar_url} if u and u.deleted_at is None else None


def _slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50] or "skill"


# ── Skill Hub ────────────────────────────────────────────────────────────────


def skill_public(s: CommunitySkill, db: Session, user: User, full: bool = False) -> dict:
    out = {"id": s.id, "slug": s.slug, "title": s.title, "description": s.description, "category": s.category,
           "tags": s.tags or [], "version": s.version, "install_count": s.install_count, "like_count": s.like_count,
           "author": _author(db, s.author_id), "mine": s.author_id == user.id, "hidden": s.is_hidden,
           "liked": db.get(Vote, ("skill", s.id, user.id)) is not None,
           "installed": db.get(CommunitySkillInstall, (s.id, user.id)) is not None,
           "created_at": iso(s.created_at), "updated_at": iso(s.updated_at)}
    if full:
        out["content"], out["files"] = s.content, s.files or {}
    return out


@router.get("/skills")
def hub(q: str = "", category: str = "", sort: Literal["popular", "liked", "new"] = "popular",
        db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    stmt = select(CommunitySkill).where(CommunitySkill.is_hidden.is_(False))
    if category:
        stmt = stmt.where(CommunitySkill.category == category)
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(CommunitySkill.title).like(term), func.lower(CommunitySkill.description).like(term)))
    order = {"popular": CommunitySkill.install_count.desc(), "liked": CommunitySkill.like_count.desc(),
             "new": CommunitySkill.created_at.desc()}[sort]
    rows = db.scalars(stmt.order_by(order).limit(200)).all()
    return {"skills": [skill_public(s, db, user) for s in rows], "categories": list(CATEGORIES)}


class ShareSkillBody(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    category: str = "other"
    tags: list[str] = []
    content: str | None = Field(default=None, max_length=100000)  # paste a SKILL.md...
    from_skill_id: str | None = None  # ...or share one of your skills


@router.post("/skills", status_code=201)
def share_skill(body: ShareSkillBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    _rate(db, CommunitySkill, CommunitySkill.author_id, user, 20, 24, "shared skills")
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown category.")
    content, files = body.content or "", {}
    if body.from_skill_id:
        sk = db.get(Skill, body.from_skill_id)
        if sk is None or sk.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Skill not found.")
        content, files = sk.content, sk.files or {}
    if not content.strip():
        raise HTTPException(status_code=422, detail="The skill needs content (SKILL.md).")
    slug, n = _slug(body.title), 1
    base = slug
    while db.scalar(select(CommunitySkill.id).where(CommunitySkill.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    s = CommunitySkill(slug=slug, author_id=user.id, title=body.title.strip(), description=body.description,
                       category=body.category, tags=[t.strip()[:30] for t in body.tags if t.strip()][:10], content=content, files=files)
    db.add(s)
    db.commit()
    return {"skill": skill_public(s, db, user, True)}


def _skill(db: Session, slug: str, user: User) -> CommunitySkill:
    s = db.scalar(select(CommunitySkill).where(CommunitySkill.slug == slug))
    if s is None or (s.is_hidden and s.author_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Skill not found.")
    return s


@router.get("/skills/{slug}")
def get_hub_skill(slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    return {"skill": skill_public(_skill(db, slug, user), db, user, True)}


@router.put("/skills/{slug}")
def update_hub_skill(slug: str, body: ShareSkillBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _skill(db, slug, user)
    if s.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can change it.")
    s.title, s.description, s.category = body.title.strip(), body.description, body.category if body.category in CATEGORIES else s.category
    s.tags = [t.strip()[:30] for t in body.tags if t.strip()][:10]
    if body.content and body.content != s.content:
        s.content, s.version = body.content, s.version + 1
    db.commit()
    return {"skill": skill_public(s, db, user, True)}


@router.delete("/skills/{slug}")
def remove_hub_skill(slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _skill(db, slug, user)
    if s.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only the author can remove it.")
    s.is_hidden = True  # installed copies stay with the people who added them
    db.commit()
    return {"ok": True}


class InstallBody(BaseModel):
    replace: bool = False


@router.post("/skills/{slug}/install")
def install(slug: str, body: InstallBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    s = _skill(db, slug, user)
    name = s.slug[:64]
    mine = db.scalar(select(Skill).where(Skill.owner_id == user.id, Skill.name == name))
    if mine is not None and not body.replace:
        raise HTTPException(status_code=409, detail="You already have a skill with this name. Replace it?")
    if mine is None:
        n = db.scalar(select(func.count()).select_from(Skill).where(Skill.owner_id == user.id)) or 0
        billing.check_max(db, user, "max_skills", n)
        mine = Skill(owner_id=user.id, name=name)
        db.add(mine)
    mine.description, mine.content, mine.files = s.description, s.content, s.files or {}
    mine.origin, mine.community_skill_id, mine.is_enabled = "community", s.id, True
    if db.get(CommunitySkillInstall, (s.id, user.id)) is None:
        db.add(CommunitySkillInstall(skill_id=s.id, user_id=user.id))
        s.install_count += 1
        if s.author_id and s.author_id != user.id:
            notify(db, s.author_id, "skill_installed", f"{user.username} added your skill '{s.title}'.", actor_id=user.id,
                   target_type="community_skill", target_id=s.id)
    db.commit()
    return {"skill_id": mine.id, "install_count": s.install_count}


@router.post("/skills/{slug}/like")
def like(slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    s = _skill(db, slug, user)
    v = db.get(Vote, ("skill", s.id, user.id))
    if v is None:
        db.add(Vote(target_type="skill", target_id=s.id, user_id=user.id, value=1))
        s.like_count += 1
        liked = True
    else:
        db.delete(v)
        s.like_count = max(0, s.like_count - 1)
        liked = False
    db.commit()
    return {"liked": liked, "like_count": s.like_count}


# ── Forum ────────────────────────────────────────────────────────────────────


def post_public(p: ForumPost, db: Session, user: User) -> dict:
    v = db.get(Vote, ("post", p.id, user.id))
    deleted = p.deleted_at is not None
    return {"id": p.id, "category": p.category, "title": "[deleted]" if deleted else p.title,
            "body": "" if deleted else p.body, "author": None if deleted else _author(db, p.author_id),
            "score": p.score, "comment_count": p.comment_count, "is_pinned": p.is_pinned, "is_locked": p.is_locked,
            "skill_id": p.skill_id, "my_vote": v.value if v else 0, "mine": p.author_id == user.id, "deleted": deleted,
            "created_at": iso(p.created_at), "edited_at": iso(p.edited_at)}


def comment_public(c: ForumComment, db: Session, user: User) -> dict:
    v = db.get(Vote, ("comment", c.id, user.id))
    deleted = c.deleted_at is not None
    return {"id": c.id, "post_id": c.post_id, "parent_id": c.parent_id, "body": "[deleted]" if deleted else c.body,
            "author": None if deleted else _author(db, c.author_id), "score": c.score, "my_vote": v.value if v else 0,
            "mine": c.author_id == user.id, "deleted": deleted, "created_at": iso(c.created_at), "edited_at": iso(c.edited_at)}


@router.get("/posts")
def posts(category: str = "", q: str = "", sort: Literal["hot", "new", "top"] = "hot",
          db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    stmt = select(ForumPost).where(ForumPost.deleted_at.is_(None))
    if category:
        stmt = stmt.where(ForumPost.category == category)
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(ForumPost.title).like(term), func.lower(ForumPost.body).like(term)))
    rows = db.scalars(stmt.order_by(ForumPost.is_pinned.desc(), ForumPost.created_at.desc()).limit(300)).all()
    if sort == "top":
        rows = sorted(rows, key=lambda p: (not p.is_pinned, -p.score))
    elif sort == "hot":
        now = utcnow()
        rows = sorted(rows, key=lambda p: (not p.is_pinned, -(p.score + p.comment_count + 1) / ((now - p.created_at).total_seconds() / 3600 + 2) ** 1.5))
    return {"posts": [post_public(p, db, user) for p in rows[:100]], "categories": list(FORUM)}


class PostBody(BaseModel):
    category: str = "general"
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(default="", max_length=40000)
    skill_id: str | None = None


@router.post("/posts", status_code=201)
def create_post(body: PostBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    if body.category not in FORUM:
        raise HTTPException(status_code=422, detail="Unknown category.")
    if body.category == "announcements" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators post announcements.")
    _rate(db, ForumPost, ForumPost.author_id, user, 10, 1, "posts")
    p = ForumPost(author_id=user.id, category=body.category, title=body.title.strip(), body=body.body, skill_id=body.skill_id)
    db.add(p)
    db.commit()
    return {"post": post_public(p, db, user)}


def _post(db: Session, pid: str) -> ForumPost:
    p = db.get(ForumPost, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="Post not found.")
    return p


@router.get("/posts/{pid}")
def get_post(pid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    p = _post(db, pid)
    cs = db.scalars(select(ForumComment).where(ForumComment.post_id == p.id).order_by(ForumComment.created_at)).all()
    return {"post": post_public(p, db, user), "comments": [comment_public(c, db, user) for c in cs]}


@router.put("/posts/{pid}")
def edit_post(pid: str, body: PostBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _post(db, pid)
    if p.author_id != user.id or p.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Only the author can edit it.")
    p.title, p.body, p.edited_at = body.title.strip(), body.body, utcnow()
    db.commit()
    return {"post": post_public(p, db, user)}


@router.delete("/posts/{pid}")
def delete_post(pid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _post(db, pid)
    if p.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only the author can delete it.")
    p.deleted_at = utcnow()  # replies stay readable
    db.commit()
    return {"ok": True}


class ModerateBody(BaseModel):
    is_pinned: bool | None = None
    is_locked: bool | None = None


@router.patch("/posts/{pid}/moderate")
def moderate_post(pid: str, body: ModerateBody, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    p = _post(db, pid)
    if body.is_pinned is not None:
        p.is_pinned = body.is_pinned
    if body.is_locked is not None:
        p.is_locked = body.is_locked
    db.commit()
    return {"post": post_public(p, db, user)}


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    parent_id: str | None = None


@router.post("/posts/{pid}/comments", status_code=201)
def comment(pid: str, body: CommentBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    p = _post(db, pid)
    if p.deleted_at is not None or p.is_locked:
        raise HTTPException(status_code=409, detail="This post is closed for replies.")
    _rate(db, ForumComment, ForumComment.author_id, user, 60, 1, "comments")
    parent = None
    if body.parent_id:
        parent = db.get(ForumComment, body.parent_id)
        if parent is None or parent.post_id != p.id:
            raise HTTPException(status_code=422, detail="Reply target not found.")
    c = ForumComment(post_id=p.id, parent_id=body.parent_id, author_id=user.id, body=body.body)
    db.add(c)
    p.comment_count += 1
    if parent is not None and parent.author_id and parent.author_id != user.id:
        notify(db, parent.author_id, "comment_reply", f"{user.username} replied: {body.body[:120]}", actor_id=user.id,
               target_type="post", target_id=p.id)
    elif p.author_id and p.author_id != user.id:
        notify(db, p.author_id, "post_comment", f"{user.username} commented on '{p.title[:80]}'", actor_id=user.id,
               target_type="post", target_id=p.id)
    db.commit()
    return {"comment": comment_public(c, db, user)}


@router.put("/comments/{cid}")
def edit_comment(cid: str, body: CommentBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(ForumComment, cid)
    if c is None or c.author_id != user.id or c.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Only the author can edit it.")
    c.body, c.edited_at = body.body, utcnow()
    db.commit()
    return {"comment": comment_public(c, db, user)}


@router.delete("/comments/{cid}")
def delete_comment(cid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(ForumComment, cid)
    if c is None or (c.author_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=403, detail="Only the author can delete it.")
    c.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


class VoteBody(BaseModel):
    target_type: Literal["post", "comment"]
    target_id: str
    value: int = Field(ge=-1, le=1)


@router.post("/vote")
def vote(body: VoteBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    target = db.get(ForumPost if body.target_type == "post" else ForumComment, body.target_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Not found.")
    v = db.get(Vote, (body.target_type, body.target_id, user.id))
    old = v.value if v else 0
    if body.value == 0:
        if v is not None:
            db.delete(v)
    elif v is None:
        db.add(Vote(target_type=body.target_type, target_id=body.target_id, user_id=user.id, value=body.value))
    else:
        v.value = body.value
    target.score += body.value - old
    db.commit()
    return {"score": target.score, "my_vote": body.value}


# ── Reports and moderation ────────────────────────────────────────────────────


class ReportBody(BaseModel):
    target_type: Literal["post", "comment", "skill"]
    target_id: str
    reason: str = "other"
    details: str = Field(default="", max_length=500)


def _target(db: Session, kind: str, tid: str):
    model = {"post": ForumPost, "comment": ForumComment, "skill": CommunitySkill}[kind]
    return db.get(model, tid)


@router.post("/reports", status_code=201)
def report(body: ReportBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _feature(db, user)
    if body.reason not in REASONS:
        raise HTTPException(status_code=422, detail="Unknown reason.")
    t = _target(db, body.target_type, body.target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if getattr(t, "author_id", None) == user.id:
        raise HTTPException(status_code=409, detail="You cannot report your own content.")
    _rate(db, ContentReport, ContentReport.reporter_id, user, 30, 1, "reports")
    r = db.scalar(select(ContentReport).where(ContentReport.target_type == body.target_type, ContentReport.target_id == body.target_id,
                                              ContentReport.reporter_id == user.id))
    if r is None:
        r = ContentReport(target_type=body.target_type, target_id=body.target_id, reporter_id=user.id)
        db.add(r)
    r.reason, r.details, r.status = body.reason, body.details, "open"
    db.commit()
    return {"ok": True}


def _preview(db: Session, kind: str, tid: str) -> dict:
    t = _target(db, kind, tid)
    if t is None:
        return {"exists": False}
    if kind == "post":
        return {"exists": True, "title": t.title, "body": t.body[:500], "author": _author(db, t.author_id), "removed": t.deleted_at is not None}
    if kind == "comment":
        return {"exists": True, "body": t.body[:500], "post_id": t.post_id, "author": _author(db, t.author_id), "removed": t.deleted_at is not None}
    return {"exists": True, "title": t.title, "slug": t.slug, "body": t.description, "author": _author(db, t.author_id), "removed": t.is_hidden}


@router.get("/reports")
def list_reports(status: Literal["open", "resolved"] = "open", db: Session = Depends(get_db), _: User = Depends(require_admin)):
    cond = ContentReport.status == "open" if status == "open" else ContentReport.status != "open"
    rows = db.execute(select(ContentReport.target_type, ContentReport.target_id, func.count(), func.max(ContentReport.created_at))
                      .where(cond).group_by(ContentReport.target_type, ContentReport.target_id)
                      .order_by(func.count().desc(), func.max(ContentReport.created_at).desc()).limit(200)).all()
    out = []
    for kind, tid, n, last in rows:
        reasons = dict(db.execute(select(ContentReport.reason, func.count()).where(
            ContentReport.target_type == kind, ContentReport.target_id == tid, cond).group_by(ContentReport.reason)).all())
        details = [d for d, in db.execute(select(ContentReport.details).where(
            ContentReport.target_type == kind, ContentReport.target_id == tid, cond, ContentReport.details != "")).all()][:10]
        out.append({"target_type": kind, "target_id": tid, "reports": n, "last_at": iso(last), "reasons": reasons,
                    "details": details, "content": _preview(db, kind, tid)})
    return {"reports": out}


class ResolveBody(BaseModel):
    target_type: Literal["post", "comment", "skill"]
    target_id: str
    action: Literal["keep", "remove"]


@router.post("/reports/resolve")
def resolve(body: ResolveBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    t = _target(db, body.target_type, body.target_id)
    if body.action == "remove" and t is not None:
        if body.target_type == "skill":
            t.is_hidden = True
        else:
            t.deleted_at = utcnow()
        author = getattr(t, "author_id", None)
        if author:
            notify(db, author, "content_removed", "An administrator removed your content after reports.", actor_id=admin.id,
                   target_type=body.target_type, target_id=body.target_id)
    for r in db.scalars(select(ContentReport).where(ContentReport.target_type == body.target_type,
                                                    ContentReport.target_id == body.target_id, ContentReport.status == "open")):
        r.status = "removed" if body.action == "remove" else "dismissed"
        r.resolved_by, r.resolved_at = admin.id, utcnow()
    audit.record(db, admin.id, "moderation.remove" if body.action == "remove" else "moderation.dismiss", target_type=body.target_type, target_id=body.target_id)
    db.commit()
    return {"ok": True}
