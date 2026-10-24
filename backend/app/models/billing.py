"""Plans, subscriptions, payments and usage metering."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # free | plus | pro | enterprise
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    price_monthly_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    price_yearly_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    credits_per_day: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # NULL = unlimited
    credits_per_month: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_upload_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    allowed_models: Mapped[list] = mapped_column(JSONType, default=list)  # [] = every model
    default_model: Mapped[str] = mapped_column(String(120), default="")
    # Feature switches and counted limits, e.g. {"community": true, "max_partners": 3}.
    entitlements: Mapped[dict] = mapped_column(JSONType, default=dict)
    features: Mapped[list] = mapped_column(JSONType, default=list)  # marketing bullet points
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    plan_id: Mapped[str] = mapped_column(String(40), ForeignKey("plans.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | canceled | expired | replaced
    billing_interval: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly | yearly | none
    source: Mapped[str] = mapped_column(String(20), default="system")  # register | payment | admin | import | system
    bulk_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bulk_imports.id", ondelete="SET NULL"), nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL = never expires
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk(ondelete="RESTRICT")  # accounting records outlive accounts
    plan_id: Mapped[str] = mapped_column(String(40), ForeignKey("plans.id"))
    subscription_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True)  # our reference, sent to the gateway
    gateway: Mapped[str] = mapped_column(String(20), default="vnpay")  # vnpay | admin | demo
    gateway_reference: Mapped[str] = mapped_column(String(255), default="")
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount_vnd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    billing_interval: Mapped[str] = mapped_column(String(10), default="monthly")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | completed | failed | canceled | expired | refunded
    note: Mapped[str] = mapped_column(String(500), default="")
    confirmed_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class UsageDaily(Base):
    """Per user per UTC day totals; quotas are checked against this."""

    __tablename__ = "usage_daily"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    credits_used: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=0)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class UsageByModel(Base):
    """Per user / day / model breakdown for cost and margin reports."""

    __tablename__ = "usage_by_model"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    model: Mapped[str] = mapped_column(String(120), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    credits_used: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=0)


class FeatureUsage(Base):
    """Counters for plan-limited creations (mastery paths, books...) per period."""

    __tablename__ = "feature_usage"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    feature: Mapped[str] = mapped_column(String(40), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM, or "lifetime"
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = updated_at()


class LLMCall(Base):
    """Every model call (append-only ledger) - source of the daily rollups."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    model: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), default="")
    purpose: Mapped[str] = mapped_column(String(40), default="chat")  # chat | embedding | indexing | quiz ...
    chat_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok | error
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
