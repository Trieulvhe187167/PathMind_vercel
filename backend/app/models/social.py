"""Friends and shared study rooms."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, created_at, updated_at, user_fk, utcnow, uuid_pk


class Friendship(Base):
    """One row per pair, stored with ``user_a_id < user_b_id`` so a pair can't exist twice."""

    __tablename__ = "friendships"
    __table_args__ = (CheckConstraint("user_a_id < user_b_id", name="ordered_pair"),)

    user_a_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    user_b_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | accepted | blocked
    requested_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    blocked_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class StudyRoom(Base):
    __tablename__ = "study_rooms"

    id: Mapped[str] = uuid_pk()
    code: Mapped[str] = mapped_column(String(12), unique=True)  # 6-char join code
    title: Mapped[str] = mapped_column(String(200))
    host_id: Mapped[str] = user_fk()
    partner_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("partners.id", ondelete="SET NULL"), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RoomMember(Base):
    __tablename__ = "room_members"

    room_id: Mapped[str] = mapped_column(String(36), ForeignKey("study_rooms.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(12), default="participant")  # host | participant
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoomMessage(Base):
    __tablename__ = "room_messages"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)  # monotonic for ?after= polling
    room_id: Mapped[str] = mapped_column(String(36), ForeignKey("study_rooms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)  # NULL for partner/system
    role: Mapped[str] = mapped_column(String(10), default="user")  # user | partner | system
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
