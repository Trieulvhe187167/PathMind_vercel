"""Community: a shared skill hub and a Reddit-style forum.

Mounted at ``/api/community`` (auth required on the whole router).

Skills (``/skills``)
    Anyone can browse, like and **install** (copies the package into their own
    skill layer, see ``services.community.install_package``). Publishing takes
    one of the user's own skills, a Co-Writer document, pasted markdown, or an
    uploaded ``.md`` / ``.zip``. Only the author (or an admin) can update or
    remove a listing; an admin removal hides it.

Forum (``/posts``, ``/comments``)
    Posts with categories, up/down votes, nested comments; sort ``hot`` /
    ``new`` / ``top``. Authors edit/delete their own content (soft delete keeps
    threads readable); admins pin, lock and remove anything. The
    ``announcements`` category is admin-only for new posts.

Reports (``/reports``)
    Anyone can flag a post, comment or skill once; admins see open reports
    grouped by target and either dismiss them or remove the content.

Notifications (``/notifications``)
    A comment on my post, a reply to my comment, my skill being added by
    someone, or my content removed by a moderator. Polled by the web app
    (``/notifications/unread-count`` feeds the sidebar badge).

User-facing errors are ``services.i18n`` keys ``community.*`` (en/zh).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from pathmind.database.connection import get_db
from pathmind.database.models import (
    CommunityNotification,
    CommunityReport,
    CommunitySkill,
    CommunitySkillInstall,
    CommunityVote,
    ForumComment,
    ForumPost,
)
from pathmind.multi_user.context import get_current_user
from pathmind.services import community as svc
from pathmind.services.i18n import t as _t

logger = logging.getLogger(__name__)

router = APIRouter()

PAGE_SIZE = 20
POSTS_PER_HOUR = 10
COMMENTS_PER_HOUR = 60
SKILLS_PER_DAY = 20
HOT_WINDOW_DAYS = 90
HOT_POOL = 500


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str:
    return value.isoformat() + "Z" if value else ""


def _usernames() -> dict[str, str]:
    from pathmind.services.auth import list_users

    try:
        return {str(u.get("id") or ""): str(u.get("username") or "") for u in list_users()}
    except Exception:
        return {}


def _author(names: dict[str, str], user_id: str) -> str:
    return names.get(user_id) or _t("social.deleted_user")


def _package_error(exc: svc.PackageError) -> HTTPException:
    code = str(exc) or "invalid"
    status = 404 if code in {"skill_not_found", "doc_not_found"} else 400
    if code == "skill_not_yours":
        status = 403
    return HTTPException(status_code=status, detail=_t(f"community.package_{code}"))


def _rate_limit(db: Session, model, user_id: str, *, window: timedelta, limit: int) -> None:
    since = _now() - window
    count = (
        db.query(func.count(model.id))
        .filter(model.author_id == user_id, model.created_at >= since)
        .scalar()
        or 0
    )
    if count >= limit:
        raise HTTPException(status_code=429, detail=_t("community.rate_limited"))


def _my_votes(db: Session, target_type: str, ids: list[str], user_id: str) -> dict[str, int]:
    if not ids:
        return {}
    rows = (
        db.query(CommunityVote.target_id, CommunityVote.value)
        .filter(
            CommunityVote.target_type == target_type,
            CommunityVote.user_id == user_id,
            CommunityVote.target_id.in_(ids),
        )
        .all()
    )
    return {target_id: value for target_id, value in rows}


def _apply_vote(db: Session, target_type: str, target_id: str, user_id: str, value: int) -> int:
    """Set the user's vote (-1/0/+1); returns the score delta."""
    row = (
        db.query(CommunityVote)
        .filter_by(target_type=target_type, target_id=target_id, user_id=user_id)
        .first()
    )
    old = row.value if row else 0
    if value == 0:
        if row:
            db.delete(row)
    elif row:
        row.value = value
    else:
        db.add(CommunityVote(target_type=target_type, target_id=target_id, user_id=user_id, value=value))
    return value - old


def _notify(
    db: Session,
    user_id: str | None,
    kind: str,
    *,
    actor_id: str | None,
    post_id: str | None = None,
    comment_id: str | None = None,
    skill_id: str | None = None,
    preview: str = "",
) -> None:
    """Queue a notification (never to the actor themself)."""
    if not user_id or user_id == actor_id:
        return
    db.add(
        CommunityNotification(
            user_id=user_id,
            kind=kind,
            actor_id=actor_id,
            post_id=post_id,
            comment_id=comment_id,
            skill_id=skill_id,
            preview=" ".join((preview or "").split())[:300],
            created_at=_now(),
        )
    )


# ===========================================================================
# Skills
# ===========================================================================


class PublishSkillBody(BaseModel):
    source: Literal["skill", "cowriter", "text"]
    skill_name: str | None = Field(default=None, max_length=64)
    doc_id: str | None = Field(default=None, max_length=40)
    content: str | None = Field(default=None, max_length=svc.MAX_SKILL_MD_CHARS)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    category: str = "other"
    tags: list[str] = Field(default_factory=list)


class UpdateSkillBody(BaseModel):
    """Metadata edits; include a ``source`` to replace the package as well."""

    source: Literal["skill", "cowriter", "text"] | None = None
    skill_name: str | None = Field(default=None, max_length=64)
    doc_id: str | None = Field(default=None, max_length=40)
    content: str | None = Field(default=None, max_length=svc.MAX_SKILL_MD_CHARS)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    category: str | None = None
    tags: list[str] | None = None


class InstallBody(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    force: bool = False


def _category(value: str | None) -> str:
    value = (value or "other").strip().lower()
    return value if value in svc.SKILL_CATEGORIES else "other"


def _material(
    source: str, *, skill_name: str | None, doc_id: str | None, content: str | None
) -> tuple[str, list[tuple[str, str]], str]:
    """(SKILL.md text, support files, suggested title)."""
    try:
        if source == "skill":
            text, support = svc.material_from_user_skill((skill_name or "").strip())
            return text, support, skill_name or ""
        if source == "cowriter":
            text, title = svc.material_from_cowriter((doc_id or "").strip())
            return text, [], title
        return content or "", [], ""
    except svc.PackageError as exc:
        raise _package_error(exc) from exc


def _unique_slug(db: Session, title: str) -> str:
    base = svc.slugify(title)
    slug, n = base, 2
    while db.query(CommunitySkill.id).filter(CommunitySkill.slug == slug).first():
        slug = f"{base[:56]}-{n}"
        n += 1
    return slug


def _skill_or_404(db: Session, slug: str, *, include_hidden: bool = False) -> CommunitySkill:
    row = db.query(CommunitySkill).filter(CommunitySkill.slug == slug).first()
    if not row or (row.is_hidden and not include_hidden):
        raise HTTPException(status_code=404, detail=_t("community.skill_not_found"))
    return row


def _can_edit(author_id: str) -> bool:
    user = get_current_user()
    return user.id == author_id or user.is_admin


def _skill_view(
    row: CommunitySkill,
    names: dict[str, str],
    *,
    liked: bool,
    installed: dict[str, str],
    detail: bool = False,
) -> dict[str, Any]:
    view = {
        "id": row.id,
        "slug": row.slug,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "tags": list(row.tags or []),
        "author_id": row.author_id,
        "author": _author(names, row.author_id),
        "version": row.version,
        "install_count": row.install_count,
        "like_count": row.like_count,
        "liked": liked,
        "installed_as": installed.get(row.slug),
        "can_edit": _can_edit(row.author_id),
        "file_count": len(row.files or {}),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if detail:
        view["content"] = row.content
        view["files"] = sorted((row.files or {}).keys())
        view["hidden"] = bool(row.is_hidden)
    return view


@router.get("/skills")
async def list_community_skills(
    q: str = "",
    category: str = "",
    sort: Literal["popular", "new", "likes"] = "popular",
    mine: bool = False,
    page: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    me = get_current_user()
    query = db.query(CommunitySkill).filter(CommunitySkill.is_hidden.is_(False))
    if category and category in svc.SKILL_CATEGORIES:
        query = query.filter(CommunitySkill.category == category)
    if mine:
        query = query.filter(CommunitySkill.author_id == me.id)
    needle = q.strip()
    if needle:
        like = f"%{needle[:100]}%"
        query = query.filter(
            or_(
                CommunitySkill.title.ilike(like),
                CommunitySkill.description.ilike(like),
                CommunitySkill.slug.ilike(like),
            )
        )
    order = {
        "new": [CommunitySkill.created_at.desc()],
        "likes": [CommunitySkill.like_count.desc(), CommunitySkill.created_at.desc()],
    }.get(sort, [CommunitySkill.install_count.desc(), CommunitySkill.like_count.desc(), CommunitySkill.created_at.desc()])
    total = query.count()
    page = max(1, page)
    rows = query.order_by(*order).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    names = _usernames()
    liked = _my_votes(db, "skill", [r.id for r in rows], me.id)
    installed = svc.installed_community_slugs()
    counts = dict(
        db.query(CommunitySkill.category, func.count(CommunitySkill.id))
        .filter(CommunitySkill.is_hidden.is_(False))
        .group_by(CommunitySkill.category)
        .all()
    )
    return {
        "skills": [_skill_view(r, names, liked=r.id in liked, installed=installed) for r in rows],
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "categories": [{"id": c, "count": int(counts.get(c, 0))} for c in svc.SKILL_CATEGORIES],
    }


@router.get("/skills/{slug}")
async def get_community_skill(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    row = _skill_or_404(db, slug, include_hidden=me.is_admin)
    liked = bool(_my_votes(db, "skill", [row.id], me.id))
    return _skill_view(
        row, _usernames(), liked=liked, installed=svc.installed_community_slugs(), detail=True
    )


def _publish(
    db: Session,
    *,
    text: str,
    support: list[tuple[str, str]],
    skipped: list[str] | None,
    title: str,
    description: str,
    category: str,
    tags: list[str],
) -> dict[str, Any]:
    me = get_current_user()
    _rate_limit(db, CommunitySkill, me.id, window=timedelta(days=1), limit=SKILLS_PER_DAY)
    slug = _unique_slug(db, title)
    try:
        package = svc.build_package(
            text, slug=slug, description=description, tags=tags, support=support, skipped=skipped
        )
    except svc.PackageError as exc:
        raise _package_error(exc) from exc
    row = CommunitySkill(
        slug=slug,
        title=title.strip(),
        description=package.description,
        category=_category(category),
        tags=package.tags,
        content=package.content,
        files=package.files,
        author_id=me.id,
        version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    view = _skill_view(row, _usernames(), liked=False, installed={}, detail=True)
    view["skipped_files"] = package.skipped
    return view


@router.post("/skills")
async def publish_skill(body: PublishSkillBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    text, support, _ = _material(
        body.source, skill_name=body.skill_name, doc_id=body.doc_id, content=body.content
    )
    return _publish(
        db,
        text=text,
        support=support,
        skipped=None,
        title=body.title,
        description=body.description,
        category=body.category,
        tags=body.tags,
    )


async def _read_upload(file: UploadFile) -> tuple[str, list[tuple[str, str]], list[str]]:
    data = await file.read(svc.MAX_UPLOAD_BYTES + 1)
    try:
        return await asyncio.to_thread(svc.material_from_upload, file.filename or "", data)
    except svc.PackageError as exc:
        raise _package_error(exc) from exc


@router.post("/skills/upload")
async def publish_skill_upload(
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=120),
    description: str = Form("", max_length=500),
    category: str = Form("other"),
    tags: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    text, support, skipped = await _read_upload(file)
    return _publish(
        db,
        text=text,
        support=support,
        skipped=skipped,
        title=title,
        description=description,
        category=category,
        tags=svc.clean_tags(tags),
    )


def _replace_package(
    row: CommunitySkill,
    text: str,
    support: list[tuple[str, str]],
    skipped: list[str] | None,
    *,
    description: str | None,
    tags: list[str] | None,
) -> list[str]:
    try:
        package = svc.build_package(
            text,
            slug=row.slug,
            description=description if description is not None else row.description,
            tags=tags if tags is not None else list(row.tags or []),
            support=support,
            skipped=skipped,
        )
    except svc.PackageError as exc:
        raise _package_error(exc) from exc
    row.content = package.content
    row.files = package.files
    row.description = package.description
    row.tags = package.tags
    row.version = (row.version or 1) + 1
    return package.skipped


@router.put("/skills/{slug}")
async def update_community_skill(
    slug: str, body: UpdateSkillBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = _skill_or_404(db, slug)
    if not _can_edit(row.author_id):
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    skipped: list[str] = []
    if body.title is not None:
        row.title = body.title.strip()
    if body.category is not None:
        row.category = _category(body.category)
    if body.source is not None:
        text, support, _ = _material(
            body.source, skill_name=body.skill_name, doc_id=body.doc_id, content=body.content
        )
        skipped = _replace_package(
            row, text, support, None, description=body.description, tags=body.tags
        )
    elif body.description is not None or body.tags is not None:
        description = (body.description if body.description is not None else row.description).strip()
        if not description:
            raise HTTPException(status_code=400, detail=_t("community.package_no_description"))
        tags = svc.clean_tags(body.tags) if body.tags is not None else list(row.tags or [])
        row.description = description[:500]
        row.tags = tags
        row.content = svc.normalize_skill_md(row.content, slug=row.slug, description=row.description, tags=tags)
    row.updated_at = _now()
    db.commit()
    liked = bool(_my_votes(db, "skill", [row.id], get_current_user().id))
    view = _skill_view(row, _usernames(), liked=liked, installed=svc.installed_community_slugs(), detail=True)
    view["skipped_files"] = skipped
    return view


@router.put("/skills/{slug}/upload")
async def update_community_skill_upload(
    slug: str, file: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = _skill_or_404(db, slug)
    if not _can_edit(row.author_id):
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    text, support, skipped = await _read_upload(file)
    dropped = _replace_package(row, text, support, skipped, description=None, tags=None)
    row.updated_at = _now()
    db.commit()
    view = _skill_view(row, _usernames(), liked=False, installed=svc.installed_community_slugs(), detail=True)
    view["skipped_files"] = dropped
    return view


@router.delete("/skills/{slug}")
async def delete_community_skill(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The author removes the listing; an admin hides it (moderation)."""
    me = get_current_user()
    row = _skill_or_404(db, slug, include_hidden=me.is_admin)
    if row.author_id == me.id:
        db.query(CommunityVote).filter_by(target_type="skill", target_id=row.id).delete()
        db.delete(row)
    elif me.is_admin:
        _hide_skill(db, row, actor_id=me.id)
    else:
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    db.commit()
    return {"status": "ok"}


@router.post("/skills/{slug}/like")
async def like_community_skill(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Toggle my like."""
    me = get_current_user()
    row = _skill_or_404(db, slug)
    liked = bool(_my_votes(db, "skill", [row.id], me.id))
    delta = _apply_vote(db, "skill", row.id, me.id, 0 if liked else 1)
    row.like_count = max(0, (row.like_count or 0) + delta)
    db.commit()
    return {"liked": not liked, "like_count": row.like_count}


@router.post("/skills/{slug}/install")
async def install_community_skill(
    slug: str, body: InstallBody | None = None, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Copy the package into my own Skills (Learning Space → Skills)."""
    from pathmind.services.skill.service import (
        InvalidSkillNameError,
        InvalidTagError,
        SkillExistsError,
        SkillImportError,
    )

    from pathmind.services.entitlements import check_skill_limit

    me = get_current_user()
    body = body or InstallBody()
    row = _skill_or_404(db, slug)
    check_skill_limit(((body.name or "").strip() or row.slug) if body.force else None)
    try:
        result = await asyncio.to_thread(
            svc.install_package,
            slug=row.slug,
            version=row.version,
            content=row.content,
            files=row.files,
            rename_to=(body.name or "").strip() or None,
            force=body.force,
        )
    except SkillExistsError as exc:
        raise HTTPException(
            status_code=409, detail=_t("community.install_exists", name=str(exc))
        ) from exc
    except (InvalidSkillNameError, InvalidTagError, SkillImportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not db.query(CommunitySkillInstall).filter_by(skill_id=row.id, user_id=me.id).first():
        db.add(CommunitySkillInstall(skill_id=row.id, user_id=me.id))
        row.install_count = (row.install_count or 0) + 1
        _notify(db, row.author_id, "skill_installed", actor_id=me.id, skill_id=row.id, preview=row.title)
        db.commit()
    return {
        "name": result.info.name,
        "install_count": row.install_count,
        "skipped": [path for path, _reason in result.skipped],
    }


# ===========================================================================
# Forum
# ===========================================================================


class PostBody(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(default="", max_length=40_000)
    category: str = "general"
    skill_slug: str | None = Field(default=None, max_length=64)


class PostUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    body: str | None = Field(default=None, max_length=40_000)
    category: str | None = None


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    parent_id: str | None = None


class CommentUpdateBody(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class VoteBody(BaseModel):
    value: Literal[-1, 0, 1]


class ModerateBody(BaseModel):
    pinned: bool | None = None
    locked: bool | None = None


def _forum_category(value: str | None, *, for_new_post: bool) -> str:
    value = (value or "general").strip().lower()
    if value not in svc.FORUM_CATEGORIES:
        value = "general"
    if for_new_post and value in svc.ADMIN_ONLY_FORUM_CATEGORIES and not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail=_t("community.admin_only_category"))
    return value


def _post_or_404(db: Session, post_id: str) -> ForumPost:
    row = db.get(ForumPost, post_id)
    if not row or row.is_deleted:
        raise HTTPException(status_code=404, detail=_t("community.post_not_found"))
    return row


def _skill_cards(db: Session, ids: list[str]) -> dict[str, dict[str, str]]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (
        db.query(CommunitySkill)
        .filter(CommunitySkill.id.in_(ids), CommunitySkill.is_hidden.is_(False))
        .all()
    )
    return {r.id: {"slug": r.slug, "title": r.title} for r in rows}


def _post_view(
    row: ForumPost,
    names: dict[str, str],
    *,
    my_vote: int,
    skills: dict[str, dict[str, str]],
    full: bool,
) -> dict[str, Any]:
    body = row.body or ""
    return {
        "id": row.id,
        "title": row.title,
        "body": body if full else body[:400],
        "truncated": (not full) and len(body) > 400,
        "category": row.category,
        "author_id": row.author_id,
        "author": _author(names, row.author_id),
        "score": row.score,
        "my_vote": my_vote,
        "comment_count": row.comment_count,
        "pinned": bool(row.is_pinned),
        "locked": bool(row.is_locked),
        "skill": skills.get(row.skill_id or ""),
        "can_edit": _can_edit(row.author_id),
        "created_at": _iso(row.created_at),
        "edited_at": _iso(row.edited_at),
    }


@router.get("/posts")
async def list_posts(
    category: str = "",
    sort: Literal["hot", "new", "top"] = "hot",
    q: str = "",
    mine: bool = False,
    page: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    me = get_current_user()
    query = db.query(ForumPost).filter(ForumPost.is_deleted.is_(False))
    if category and category in svc.FORUM_CATEGORIES:
        query = query.filter(ForumPost.category == category)
    if mine:
        query = query.filter(ForumPost.author_id == me.id)
    needle = q.strip()
    if needle:
        like = f"%{needle[:100]}%"
        query = query.filter(or_(ForumPost.title.ilike(like), ForumPost.body.ilike(like)))
    page = max(1, page)
    total = query.count()
    pinned: list[ForumPost] = []
    if page == 1 and not needle and not mine:
        pinned = query.filter(ForumPost.is_pinned.is_(True)).order_by(ForumPost.created_at.desc()).all()
    rest = query.filter(ForumPost.is_pinned.is_(False)) if pinned else query
    offset = (page - 1) * PAGE_SIZE
    if sort == "new":
        rows = rest.order_by(ForumPost.created_at.desc()).offset(offset).limit(PAGE_SIZE).all()
    elif sort == "top":
        rows = (
            rest.order_by(ForumPost.score.desc(), ForumPost.created_at.desc())
            .offset(offset)
            .limit(PAGE_SIZE)
            .all()
        )
    else:
        since = _now() - timedelta(days=HOT_WINDOW_DAYS)
        pool = (
            rest.filter(ForumPost.created_at >= since)
            .order_by(ForumPost.created_at.desc())
            .limit(HOT_POOL)
            .all()
        )
        now = _now()
        pool.sort(key=lambda r: svc.hot_rank(r.score, r.created_at, r.comment_count, now), reverse=True)
        rows = pool[offset : offset + PAGE_SIZE]
        if len(rows) < PAGE_SIZE:  # older than the hot window: newest first
            older = (
                rest.filter(ForumPost.created_at < since)
                .order_by(ForumPost.created_at.desc())
                .offset(max(0, offset - len(pool)))
                .limit(PAGE_SIZE - len(rows))
                .all()
            )
            rows += older
    items = pinned + rows
    names = _usernames()
    votes = _my_votes(db, "post", [r.id for r in items], me.id)
    skills = _skill_cards(db, [r.skill_id for r in items])
    return {
        "posts": [
            _post_view(r, names, my_vote=votes.get(r.id, 0), skills=skills, full=False) for r in items
        ],
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "categories": list(svc.FORUM_CATEGORIES),
        "is_admin": me.is_admin,
    }


@router.post("/posts")
async def create_post(body: PostBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    category = _forum_category(body.category, for_new_post=True)
    _rate_limit(db, ForumPost, me.id, window=timedelta(hours=1), limit=POSTS_PER_HOUR)
    skill_id = None
    if body.skill_slug:
        skill_id = _skill_or_404(db, body.skill_slug.strip()).id
    row = ForumPost(
        author_id=me.id,
        category=category,
        title=body.title.strip(),
        body=body.body.strip(),
        skill_id=skill_id,
        score=1,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.flush()
    db.add(CommunityVote(target_type="post", target_id=row.id, user_id=me.id, value=1))
    db.commit()
    return _post_view(
        row, _usernames(), my_vote=1, skills=_skill_cards(db, [skill_id or ""]), full=True
    )


@router.get("/posts/{post_id}")
async def get_post(post_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    row = _post_or_404(db, post_id)
    comments = (
        db.query(ForumComment)
        .filter(ForumComment.post_id == row.id)
        .order_by(ForumComment.created_at.asc())
        .all()
    )
    names = _usernames()
    post_vote = _my_votes(db, "post", [row.id], me.id).get(row.id, 0)
    comment_votes = _my_votes(db, "comment", [c.id for c in comments], me.id)
    return {
        "post": _post_view(
            row, names, my_vote=post_vote, skills=_skill_cards(db, [row.skill_id or ""]), full=True
        ),
        "comments": [
            {
                "id": c.id,
                "parent_id": c.parent_id,
                "author_id": None if c.is_deleted else c.author_id,
                "author": "" if c.is_deleted else _author(names, c.author_id),
                "body": "" if c.is_deleted else c.body,
                "deleted": bool(c.is_deleted),
                "score": c.score,
                "my_vote": comment_votes.get(c.id, 0),
                "can_edit": (not c.is_deleted) and _can_edit(c.author_id),
                "created_at": _iso(c.created_at),
                "edited_at": _iso(c.edited_at),
            }
            for c in comments
        ],
        "is_admin": me.is_admin,
    }


@router.put("/posts/{post_id}")
async def update_post(post_id: str, body: PostUpdateBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _post_or_404(db, post_id)
    if not _can_edit(row.author_id):
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    if body.title is not None:
        row.title = body.title.strip()
    if body.body is not None:
        row.body = body.body.strip()
    if body.category is not None:
        new_category = _forum_category(body.category, for_new_post=False)
        if new_category != row.category:
            new_category = _forum_category(body.category, for_new_post=True)
        row.category = new_category
    row.edited_at = row.updated_at = _now()
    db.commit()
    me = get_current_user()
    return _post_view(
        row,
        _usernames(),
        my_vote=_my_votes(db, "post", [row.id], me.id).get(row.id, 0),
        skills=_skill_cards(db, [row.skill_id or ""]),
        full=True,
    )


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _post_or_404(db, post_id)
    if not _can_edit(row.author_id):
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    _remove_post(db, row, actor_id=get_current_user().id)
    db.commit()
    return {"status": "ok"}


@router.post("/posts/{post_id}/vote")
async def vote_post(post_id: str, body: VoteBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    row = _post_or_404(db, post_id)
    row.score = (row.score or 0) + _apply_vote(db, "post", row.id, me.id, body.value)
    db.commit()
    return {"score": row.score, "my_vote": body.value}


@router.post("/posts/{post_id}/moderate")
async def moderate_post(post_id: str, body: ModerateBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail=_t("community.admin_only"))
    row = _post_or_404(db, post_id)
    if body.pinned is not None:
        row.is_pinned = body.pinned
    if body.locked is not None:
        row.is_locked = body.locked
    db.commit()
    return {"pinned": bool(row.is_pinned), "locked": bool(row.is_locked)}


@router.post("/posts/{post_id}/comments")
async def create_comment(post_id: str, body: CommentBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    row = _post_or_404(db, post_id)
    if row.is_locked and not me.is_admin:
        raise HTTPException(status_code=403, detail=_t("community.post_locked"))
    _rate_limit(db, ForumComment, me.id, window=timedelta(hours=1), limit=COMMENTS_PER_HOUR)
    parent_id = (body.parent_id or "").strip() or None
    parent: ForumComment | None = None
    if parent_id:
        parent = db.get(ForumComment, parent_id)
        if not parent or parent.post_id != row.id:
            raise HTTPException(status_code=404, detail=_t("community.comment_not_found"))
    comment = ForumComment(
        post_id=row.id,
        parent_id=parent_id,
        author_id=me.id,
        body=body.body.strip(),
        score=1,
        created_at=_now(),
    )
    db.add(comment)
    db.flush()
    db.add(CommunityVote(target_type="comment", target_id=comment.id, user_id=me.id, value=1))
    row.comment_count = (row.comment_count or 0) + 1
    parent_author = parent.author_id if parent_id and not parent.is_deleted else None
    if parent_author:
        _notify(db, parent_author, "comment_reply", actor_id=me.id, post_id=row.id,
                comment_id=comment.id, preview=comment.body)
    if row.author_id != parent_author:
        _notify(db, row.author_id, "post_comment", actor_id=me.id, post_id=row.id,
                comment_id=comment.id, preview=comment.body)
    db.commit()
    return {
        "id": comment.id,
        "parent_id": comment.parent_id,
        "author_id": me.id,
        "author": me.username,
        "body": comment.body,
        "deleted": False,
        "score": 1,
        "my_vote": 1,
        "can_edit": True,
        "created_at": _iso(comment.created_at),
        "edited_at": "",
    }


def _comment_or_404(db: Session, comment_id: str) -> ForumComment:
    comment = db.get(ForumComment, comment_id)
    if not comment or comment.is_deleted:
        raise HTTPException(status_code=404, detail=_t("community.comment_not_found"))
    return comment


@router.put("/comments/{comment_id}")
async def update_comment(
    comment_id: str, body: CommentUpdateBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    comment = _comment_or_404(db, comment_id)
    if not _can_edit(comment.author_id):
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    comment.body = body.body.strip()
    comment.edited_at = _now()
    db.commit()
    return {"id": comment.id, "body": comment.body, "edited_at": _iso(comment.edited_at)}


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Soft delete: replies stay readable under a "[deleted]" placeholder."""
    comment = _comment_or_404(db, comment_id)
    if not _can_edit(comment.author_id):
        raise HTTPException(status_code=403, detail=_t("community.not_author"))
    _remove_comment(db, comment, actor_id=get_current_user().id)
    db.commit()
    return {"status": "ok"}


@router.post("/comments/{comment_id}/vote")
async def vote_comment(comment_id: str, body: VoteBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    comment = _comment_or_404(db, comment_id)
    comment.score = (comment.score or 0) + _apply_vote(db, "comment", comment.id, me.id, body.value)
    db.commit()
    return {"score": comment.score, "my_vote": body.value}


# ===========================================================================
# Removal helpers (author delete, admin delete, report resolution)
# ===========================================================================


def _close_reports(db: Session, target_type: str, target_id: str, *, status: str, actor_id: str) -> None:
    db.query(CommunityReport).filter(
        CommunityReport.target_type == target_type,
        CommunityReport.target_id == target_id,
        CommunityReport.status == "open",
    ).update(
        {"status": status, "resolved_by": actor_id, "resolved_at": _now()},
        synchronize_session=False,
    )


def _remove_post(db: Session, row: ForumPost, *, actor_id: str) -> None:
    row.is_deleted = True
    row.updated_at = _now()
    _close_reports(db, "post", row.id, status="removed", actor_id=actor_id)
    _notify(db, row.author_id, "content_removed", actor_id=actor_id, post_id=row.id, preview=row.title)


def _remove_comment(db: Session, comment: ForumComment, *, actor_id: str) -> None:
    comment.is_deleted = True
    post = db.get(ForumPost, comment.post_id)
    if post:
        post.comment_count = max(0, (post.comment_count or 0) - 1)
    _close_reports(db, "comment", comment.id, status="removed", actor_id=actor_id)
    _notify(
        db, comment.author_id, "content_removed", actor_id=actor_id,
        post_id=comment.post_id, comment_id=comment.id, preview=comment.body,
    )


def _hide_skill(db: Session, row: CommunitySkill, *, actor_id: str) -> None:
    row.is_hidden = True
    _close_reports(db, "skill", row.id, status="removed", actor_id=actor_id)
    _notify(db, row.author_id, "content_removed", actor_id=actor_id, skill_id=row.id, preview=row.title)


# ===========================================================================
# Reports
# ===========================================================================

REPORT_REASONS = ("spam", "harassment", "inappropriate", "misinformation", "copyright", "other")


class ReportBody(BaseModel):
    target_type: Literal["post", "comment", "skill"]
    target_id: str = Field(min_length=1, max_length=64)
    reason: str = "other"
    details: str = Field(default="", max_length=500)


class ResolveBody(BaseModel):
    target_type: Literal["post", "comment", "skill"]
    target_id: str = Field(min_length=1, max_length=64)
    action: Literal["dismiss", "remove"]


def _require_admin() -> None:
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail=_t("community.admin_only"))


def _report_target(db: Session, target_type: str, target_id: str):
    """(row, author_id) of a live target, or (None, None)."""
    if target_type == "post":
        row = db.get(ForumPost, target_id)
        return (row, row.author_id) if row and not row.is_deleted else (None, None)
    if target_type == "comment":
        row = db.get(ForumComment, target_id)
        return (row, row.author_id) if row and not row.is_deleted else (None, None)
    row = db.get(CommunitySkill, target_id)
    return (row, row.author_id) if row and not row.is_hidden else (None, None)


@router.post("/reports")
async def create_report(body: ReportBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    row, author_id = _report_target(db, body.target_type, body.target_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_t("community.report_target_missing"))
    if author_id == me.id:
        raise HTTPException(status_code=400, detail=_t("community.report_own"))
    reason = body.reason if body.reason in REPORT_REASONS else "other"
    existing = (
        db.query(CommunityReport)
        .filter_by(target_type=body.target_type, target_id=body.target_id, reporter_id=me.id)
        .first()
    )
    if existing:
        if existing.status == "open":
            existing.reason, existing.details = reason, body.details.strip()
            db.commit()
        return {"status": "ok", "message": _t("community.report_thanks")}
    _rate_limit_reports(db, me.id)
    db.add(
        CommunityReport(
            target_type=body.target_type,
            target_id=body.target_id,
            reporter_id=me.id,
            reason=reason,
            details=body.details.strip(),
            created_at=_now(),
        )
    )
    db.commit()
    return {"status": "ok", "message": _t("community.report_thanks")}


def _rate_limit_reports(db: Session, user_id: str) -> None:
    since = _now() - timedelta(hours=1)
    count = (
        db.query(func.count(CommunityReport.id))
        .filter(CommunityReport.reporter_id == user_id, CommunityReport.created_at >= since)
        .scalar()
        or 0
    )
    if count >= 30:
        raise HTTPException(status_code=429, detail=_t("community.rate_limited"))


def _target_summary(db: Session, target_type: str, target_id: str, names: dict[str, str]) -> dict[str, Any]:
    if target_type == "post":
        row = db.get(ForumPost, target_id)
        if not row:
            return {"missing": True}
        return {
            "title": row.title,
            "excerpt": (row.body or "")[:300],
            "author": _author(names, row.author_id),
            "post_id": row.id,
            "removed": bool(row.is_deleted),
        }
    if target_type == "comment":
        row = db.get(ForumComment, target_id)
        if not row:
            return {"missing": True}
        post = db.get(ForumPost, row.post_id)
        return {
            "title": post.title if post else "",
            "excerpt": (row.body or "")[:300],
            "author": _author(names, row.author_id),
            "post_id": row.post_id,
            "removed": bool(row.is_deleted),
        }
    row = db.get(CommunitySkill, target_id)
    if not row:
        return {"missing": True}
    return {
        "title": row.title,
        "excerpt": row.description[:300],
        "author": _author(names, row.author_id),
        "skill_slug": row.slug,
        "removed": bool(row.is_hidden),
    }


@router.get("/reports")
async def list_reports(
    status: Literal["open", "resolved"] = "open", db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Admin: reports grouped by target, most-reported first."""
    _require_admin()
    query = db.query(CommunityReport)
    query = (
        query.filter(CommunityReport.status == "open")
        if status == "open"
        else query.filter(CommunityReport.status != "open")
    )
    rows = query.order_by(CommunityReport.created_at.desc()).limit(1000).all()
    names = _usernames()
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r.target_type, r.target_id)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "target_type": r.target_type,
                "target_id": r.target_id,
                "status": r.status,
                "resolved_by": _author(names, r.resolved_by) if r.resolved_by else "",
                "resolved_at": _iso(r.resolved_at),
                "reports": [],
                **_target_summary(db, r.target_type, r.target_id, names),
            }
        group["reports"].append(
            {
                "reason": r.reason,
                "details": r.details,
                "reporter": _author(names, r.reporter_id),
                "created_at": _iso(r.created_at),
            }
        )
    items = sorted(
        groups.values(),
        key=lambda g: (len(g["reports"]), g["reports"][0]["created_at"]),
        reverse=True,
    )
    for item in items:
        item["count"] = len(item["reports"])
    open_count = (
        db.query(func.count(func.distinct(CommunityReport.target_id)))
        .filter(CommunityReport.status == "open")
        .scalar()
        or 0
    )
    return {"items": items[:200], "open_targets": int(open_count)}


@router.post("/reports/resolve")
async def resolve_reports(body: ResolveBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Admin: dismiss every open report on a target, or remove the content."""
    _require_admin()
    me = get_current_user()
    if body.action == "dismiss":
        _close_reports(db, body.target_type, body.target_id, status="dismissed", actor_id=me.id)
    else:
        row, _author_id = _report_target(db, body.target_type, body.target_id)
        if row is None:
            _close_reports(db, body.target_type, body.target_id, status="removed", actor_id=me.id)
        elif body.target_type == "post":
            _remove_post(db, row, actor_id=me.id)
        elif body.target_type == "comment":
            _remove_comment(db, row, actor_id=me.id)
        else:
            _hide_skill(db, row, actor_id=me.id)
    db.commit()
    return {"status": "ok"}


# ===========================================================================
# Notifications
# ===========================================================================


class ReadBody(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=200)
    all: bool = False


def _unread(db: Session, user_id: str) -> int:
    return int(
        db.query(func.count(CommunityNotification.id))
        .filter(CommunityNotification.user_id == user_id, CommunityNotification.read_at.is_(None))
        .scalar()
        or 0
    )


@router.get("/notifications/unread-count")
async def notifications_unread_count(db: Session = Depends(get_db)) -> dict[str, int]:
    return {"unread": _unread(db, get_current_user().id)}


@router.get("/notifications")
async def list_notifications(limit: int = 30, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = get_current_user()
    limit = max(1, min(int(limit or 30), 100))
    rows = (
        db.query(CommunityNotification)
        .filter(CommunityNotification.user_id == me.id)
        .order_by(CommunityNotification.created_at.desc())
        .limit(limit)
        .all()
    )
    names = _usernames()
    post_ids = {r.post_id for r in rows if r.post_id}
    skill_ids = {r.skill_id for r in rows if r.skill_id}
    posts = (
        {p.id: p for p in db.query(ForumPost).filter(ForumPost.id.in_(post_ids)).all()} if post_ids else {}
    )
    skills = (
        {s.id: s for s in db.query(CommunitySkill).filter(CommunitySkill.id.in_(skill_ids)).all()}
        if skill_ids
        else {}
    )
    items = []
    for r in rows:
        post = posts.get(r.post_id or "")
        skill = skills.get(r.skill_id or "")
        items.append(
            {
                "id": r.id,
                "kind": r.kind,
                "actor": _author(names, r.actor_id) if r.actor_id else "",
                "post_id": r.post_id if post and not post.is_deleted else None,
                "post_title": post.title if post else "",
                "comment_id": r.comment_id,
                "skill_slug": skill.slug if skill and not skill.is_hidden else None,
                "skill_title": skill.title if skill else "",
                "preview": r.preview,
                "read": r.read_at is not None,
                "created_at": _iso(r.created_at),
            }
        )
    return {"items": items, "unread": _unread(db, me.id)}


@router.post("/notifications/read")
async def mark_notifications_read(body: ReadBody, db: Session = Depends(get_db)) -> dict[str, int]:
    me = get_current_user()
    query = db.query(CommunityNotification).filter(
        CommunityNotification.user_id == me.id, CommunityNotification.read_at.is_(None)
    )
    if not body.all:
        if not body.ids:
            return {"unread": _unread(db, me.id)}
        query = query.filter(CommunityNotification.id.in_(body.ids))
    query.update({"read_at": _now()}, synchronize_session=False)
    db.commit()
    return {"unread": _unread(db, me.id)}
