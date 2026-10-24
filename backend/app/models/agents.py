"""Personalisation of the AI: personas, skills, partners (companions), memory, MCP tools."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk


class Persona(Base):
    """A teaching style / system prompt. ``owner_id`` NULL = built-in, visible to all."""

    __tablename__ = "personas"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str | None] = user_fk(nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Skill(Base):
    """A SKILL.md package the AI can load. ``owner_id`` NULL = built-in."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str | None] = user_fk(nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, default="")  # SKILL.md
    files: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"references/x.md": "text"}
    origin: Mapped[str] = mapped_column(String(12), default="own")  # own | community | builtin
    community_skill_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("community_skills.id", ondelete="SET NULL"), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Partner(Base):
    """A study companion: persona + model + skills, usable in chat and study rooms."""

    __tablename__ = "partners"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str | None] = user_fk(nullable=True)  # NULL = admin-managed, assignable
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    persona_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("personas.id", ondelete="SET NULL"), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)  # skills, tools, KBs, language
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class PartnerShare(Base):
    """A partner shared with a friend (use only) or assigned by an admin."""

    __tablename__ = "partner_shares"

    partner_id: Mapped[str] = mapped_column(String(36), ForeignKey("partners.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    shared_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    created_at: Mapped[datetime] = created_at()


class Memory(Base):
    """Something the tutor remembers about a learner (fact, preference, goal, difficulty)."""

    __tablename__ = "memories"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    kind: Mapped[str] = mapped_column(String(16), default="fact")  # fact | preference | goal | difficulty
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=1)  # 1..5
    source_session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemorySharingConsent(Base):
    """Opt-in to share anonymised memory statistics. ``pseudo_id`` is the only link to insights."""

    __tablename__ = "memory_sharing_consents"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    pseudo_id: Mapped[str] = mapped_column(String(32), unique=True)
    consented_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_shared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemoryInsight(Base):
    """One anonymised count (no free text, no user id)."""

    __tablename__ = "memory_insights"
    __table_args__ = (UniqueConstraint("pseudo_id", "kind", "label", "period"),)

    id: Mapped[str] = uuid_pk()
    pseudo_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # topic | preference | activity
    label: Mapped[str] = mapped_column(String(48))
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    count: Mapped[int] = mapped_column(Integer, default=1)


class MCPServer(Base):
    """An external tool server the AI may call (Model Context Protocol)."""

    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str | None] = user_fk(nullable=True)  # NULL = deployment-wide (admin)
    name: Mapped[str] = mapped_column(String(64))
    transport: Mapped[str] = mapped_column(String(10), default="http")  # http | sse | stdio
    url: Mapped[str] = mapped_column(String(500), default="")
    command: Mapped[str] = mapped_column(String(500), default="")
    secrets_encrypted: Mapped[str] = mapped_column(Text, default="")  # env / headers, Fernet
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
