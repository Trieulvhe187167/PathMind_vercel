"""Shared column helpers for every table.

Conventions (see README "Database"):
- Primary keys are UUID strings (36 chars) unless a table is an append-only log
  (BigInteger identity) or keyed naturally (composite / slug).
- Times are naive UTC (``utcnow()``), stored as ``timestamp without time zone``.
- Money: USD as Numeric(12, 2), VND as BigInteger.
- Free-form structured data is JSON (JSONB on PostgreSQL).
- Rows people may want back are soft-deleted with ``deleted_at``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")
# SQLite only auto-increments INTEGER PRIMARY KEY, PostgreSQL gets a 64-bit identity.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


def uuid_pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime, default=utcnow, nullable=False)


def updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


def user_fk(ondelete: str = "CASCADE", nullable: bool = False, index: bool = True):
    from sqlalchemy import ForeignKey

    return mapped_column(
        String(36), ForeignKey("users.id", ondelete=ondelete), nullable=nullable, index=index
    )
