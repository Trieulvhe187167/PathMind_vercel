"""Conversations with the AI tutor (replaces the per-user chat_history.db)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    title: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(30), default="chat")  # chat | deep_research | solve | quiz | tutor ...
    model: Mapped[str] = mapped_column(String(120), default="")
    persona_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("personas.id", ondelete="SET NULL"), nullable=True)
    partner_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("partners.id", ondelete="SET NULL"), nullable=True, index=True)
    course_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True)
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)  # tools on, temperature ...
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ChatSessionKnowledgeBase(Base):
    """Knowledge bases attached to a conversation."""

    __tablename__ = "chat_session_knowledge_bases"

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)


class ChatTurn(Base):
    """One question -> answer cycle; groups the messages and the streamed events."""

    __tablename__ = "chat_turns"

    id: Mapped[str] = uuid_pk()
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="running")  # running | done | failed | canceled
    mode: Mapped[str] = mapped_column(String(30), default="chat")
    model: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    id: Mapped[str] = uuid_pk()
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("chat_turns.id", ondelete="SET NULL"), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(Integer)  # order inside the session
    role: Mapped[str] = mapped_column(String(12))  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSONType, default=list)  # [{file_id, name, mime}]
    # How the answer was grounded: verified (company tree only) | mixed | personal_kb | general | none
    grounding: Mapped[str] = mapped_column(String(12), default="")
    tool_calls: Mapped[list] = mapped_column(JSONType, default=list)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()


class ChatTurnEvent(Base):
    """Streamed progress of a turn (thinking, tool calls, retrieval) for replay. Append-only."""

    __tablename__ = "chat_turn_events"
    __table_args__ = (UniqueConstraint("turn_id", "seq"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_turns.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30))  # status | tool_call | tool_result | token | error
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatMessageCitation(Base):
    """A source an AI answer cites: a verified chunk of the company tree or a personal KB document."""

    __tablename__ = "chat_message_citations"
    __table_args__ = (UniqueConstraint("message_id", "seq"),)

    id: Mapped[str] = uuid_pk()
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # the [1], [2] marker in the answer
    chunk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_chunks.id", ondelete="SET NULL"), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("verified_documents.id", ondelete="SET NULL"), nullable=True)
    kb_document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_base_documents.id", ondelete="SET NULL"), nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
