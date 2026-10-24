"""Deployment-wide configuration: settings, AI providers/models, model prices, file storage."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import JSONType, created_at, updated_at, user_fk, uuid_pk


class AppSetting(Base):
    """Key/value settings for the whole deployment (replaces the old settings JSON files)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    updated_at: Mapped[datetime] = updated_at()


class AIProvider(Base):
    """An LLM / embedding endpoint. ``api_key_encrypted`` is Fernet-encrypted, never plain."""

    __tablename__ = "ai_providers"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(30))  # openai | anthropic | gemini | ollama | openai_compatible ...
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSONType, default=dict)  # headers, proxy, api version
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AIModel(Base):
    __tablename__ = "ai_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_name"),)

    id: Mapped[str] = uuid_pk()
    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_providers.id", ondelete="CASCADE"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(120))  # as the provider calls it
    display_name: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(String(20), default="chat")  # chat | embedding | image | speech
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class ModelPrice(Base):
    """List price per 1M tokens; turns usage into cost and credits. ``*`` is the fallback row."""

    __tablename__ = "model_prices"

    model_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    provider: Mapped[str] = mapped_column(String(40), default="")
    input_per_m: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    cached_input_per_m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    output_per_m: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    aliases: Mapped[list] = mapped_column(JSONType, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = updated_at()


class StorageBackend(Base):
    """Where uploaded files are kept: an S3-compatible bucket (AWS S3, MinIO, Cloudflare R2, Viettel/FPT Cloud...).

    No row (or none active) = files go to the server's own disk. Exactly one row may be
    ``is_default`` for new uploads; older rows stay so files already stored there remain readable
    after a switch (``files.storage_backend_id`` points at the row that holds each file).
    """

    __tablename__ = "storage_backends"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)  # e.g. "R2 production"
    kind: Mapped[str] = mapped_column(String(10), default="s3")  # s3 (any S3-compatible service)
    endpoint_url: Mapped[str] = mapped_column(String(500), default="")  # empty = AWS default endpoint
    region: Mapped[str] = mapped_column(String(64), default="")
    bucket: Mapped[str] = mapped_column(String(255))
    key_prefix: Mapped[str] = mapped_column(String(200), default="")  # e.g. "pathmind/"
    access_key_id: Mapped[str] = mapped_column(String(255), default="")
    secret_key_encrypted: Mapped[str] = mapped_column(Text, default="")  # Fernet, never plain text
    path_style: Mapped[bool] = mapped_column(Boolean, default=False)  # MinIO and some local providers need it
    signed_url_seconds: Mapped[int] = mapped_column(Integer, default=300)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # receives new uploads
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # false = read-only, keeps old files reachable
    status: Mapped[str] = mapped_column(String(10), default="untested")  # untested | ok | error
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


# At most one storage receives new uploads.
Index(
    "uq_storage_backends_one_default",
    StorageBackend.is_default,
    unique=True,
    postgresql_where=StorageBackend.is_default.is_(True),
    sqlite_where=StorageBackend.is_default.is_(True),
)
