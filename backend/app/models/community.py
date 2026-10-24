"""Community: skill hub, forum, votes, reports. Plus app-wide notifications."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk


class CommunitySkill(Base):
    __tablename__ = "community_skills"

    id: Mapped[str] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    author_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(30), default="other", index=True)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    content: Mapped[str] = mapped_column(Text)  # SKILL.md
    files: Mapped[dict] = mapped_column(JSONType, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    install_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CommunitySkillInstall(Base):
    __tablename__ = "community_skill_installs"

    skill_id: Mapped[str] = mapped_column(String(36), ForeignKey("community_skills.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = created_at()


class ForumPost(Base):
    __tablename__ = "forum_posts"

    id: Mapped[str] = uuid_pk()
    author_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    category: Mapped[str] = mapped_column(String(30), default="general", index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    skill_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("community_skills.id", ondelete="SET NULL"), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ForumComment(Base):
    __tablename__ = "forum_comments"

    id: Mapped[str] = uuid_pk()
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("forum_posts.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("forum_comments.id", ondelete="CASCADE"), nullable=True)
    author_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    body: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Vote(Base):
    """+1/-1 on posts and comments, +1 (like) on skills."""

    __tablename__ = "votes"

    target_type: Mapped[str] = mapped_column(String(10), primary_key=True)  # post | comment | skill
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created_at()


class ContentReport(Base):
    __tablename__ = "content_reports"
    __table_args__ = (UniqueConstraint("target_type", "target_id", "reporter_id"),)

    id: Mapped[str] = uuid_pk()
    target_type: Mapped[str] = mapped_column(String(10))  # post | comment | skill
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    reporter_id: Mapped[str] = user_fk()
    reason: Mapped[str] = mapped_column(String(20), default="other")  # spam | harassment | inappropriate | misinformation | copyright | other
    details: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | dismissed | removed
    resolved_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()


class Notification(Base):
    """In-app notice for any feature (forum reply, skill installed, payment confirmed...)."""

    __tablename__ = "notifications"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()  # recipient
    kind: Mapped[str] = mapped_column(String(40))  # post_comment | comment_reply | skill_installed | content_removed | payment_completed ...
    actor_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    target_type: Mapped[str] = mapped_column(String(20), default="")
    target_id: Mapped[str] = mapped_column(String(36), default="")
    preview: Mapped[str] = mapped_column(String(300), default="")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
