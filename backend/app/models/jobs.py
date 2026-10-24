"""Background work: long tasks (indexing, book generation) and scheduled jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import JSONType, created_at, user_fk, uuid_pk


class BackgroundTask(Base):
    __tablename__ = "background_tasks"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str | None] = user_fk(nullable=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)  # kb_index | kb_sync | book_generate | import ...
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)  # queued | running | done | failed | canceled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ScheduledJob(Base):
    """A recurring job (cron) - e.g. daily study reminder, nightly KB sync."""

    __tablename__ = "scheduled_jobs"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str | None] = user_fk(nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    cron: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Ho_Chi_Minh")
    action: Mapped[dict] = mapped_column(JSONType, default=dict)  # {type, params}
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(10), default="")
    created_at: Mapped[datetime] = created_at()
