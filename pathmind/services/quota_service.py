"""Subscription lifecycle, quota, and model-access enforcement.

Conventions
-----------
* All datetimes stored in the DB are *naive UTC* (``_utcnow()``).
* Identity comes from the auth store / JWT. The relational ``users`` table is
  only a mirror for foreign keys and is filled lazily by ``ensure_db_user``.
* Authorisation decisions use the caller's **JWT role** (passed in as
  ``role``). The DB copy of the role is informational only, so promoting or
  demoting someone in the auth store takes effect immediately.
* A user with no DB row is treated as a *free-plan user*, never as unlimited.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from pathmind.services.i18n import t as _t
from pathmind.database.models import (
    Payment,
    Subscription,
    SubscriptionPlan,
    UsageLog,
    UsageModelLog,
    User,
)
from pathmind.services import model_pricing

logger = logging.getLogger(__name__)

FREE_PLAN_ID = "free"
ADMIN_IDS = {"admin", "local-admin"}
INTERVAL_DAYS = {"monthly": 30, "yearly": 365}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_str() -> str:
    return _utcnow().strftime("%Y-%m-%d")


def _month_prefix() -> str:
    return _utcnow().strftime("%Y-%m")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def usd_to_vnd_rate() -> int:
    try:
        return int(float(os.environ.get("BILLING_USD_VND_RATE", "25400")))
    except ValueError:
        return 25400


def _is_admin(user_id: str, role: str | None, db: Session) -> bool:
    if role is not None:
        return role in ("admin", "rnd")
    if user_id in ADMIN_IDS:
        return True
    user = db.get(User, user_id)
    return bool(user and user.role in ("admin", "rnd"))


def _auth_store_lookup(user_id: str) -> dict | None:
    try:
        from pathmind.services.auth import list_users

        for item in list_users():
            if str(item.get("id") or "") == user_id:
                return item
    except Exception as exc:  # pragma: no cover - auth store optional
        logger.debug("Auth store lookup failed for %s: %s", user_id, exc)
    return None


def ensure_db_user(
    db: Session,
    user_id: str,
    username: str | None = None,
    role: str | None = None,
) -> User | None:
    """Return the mirrored DB user, creating it from the auth store if needed."""
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user:
        if role and user.role != role:
            user.role = role
            db.commit()
        return user

    if username is None or role is None:
        info = _auth_store_lookup(user_id)
        if info:
            username = username or str(info.get("username") or "")
            role = role or str(info.get("role") or "user")
    username = username or user_id
    # ``users.username`` is unique; an old seeded row may already own the name.
    if db.query(User).filter_by(username=username).first():
        username = f"{username}#{user_id[:8]}"

    user = User(
        id=user_id,
        username=username,
        password_hash="managed-by-auth-store",
        role=role if role in {"admin", "rnd", "educator", "user"} else "user",
    )
    db.add(user)
    db.commit()
    return user


def get_free_plan(db: Session) -> SubscriptionPlan:
    free_plan = db.get(SubscriptionPlan, FREE_PLAN_ID)
    if not free_plan:
        free_plan = SubscriptionPlan(
            id=FREE_PLAN_ID,
            name="Free Starter",
            max_tokens_per_day=50000,
            max_tokens_per_month=1500000,
            max_storage_bytes=200 * 1024 * 1024,
            max_upload_file_size_bytes=10 * 1024 * 1024,
            allowed_models=["gemini-1.5-flash", "gpt-4o-mini", "deepseek-chat"],
            is_active=True,
        )
        db.add(free_plan)
        db.commit()
    return free_plan


# ---------------------------------------------------------------------------
# Subscription lifecycle
# ---------------------------------------------------------------------------


def get_active_subscription(user_id: str, db: Session) -> Subscription | None:
    """Return the user's current subscription, expiring stale ones on the way."""
    subs = (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.status == "active")
        .order_by(Subscription.current_period_start.desc())
        .all()
    )
    now = _utcnow()
    changed = False
    current: Subscription | None = None
    for sub in subs:
        if sub.current_period_end is not None and sub.current_period_end <= now:
            sub.status = "canceled" if sub.cancel_at_period_end else "expired"
            sub.canceled_at = sub.canceled_at or now
            changed = True
            continue
        if current is None:
            current = sub
        else:
            # Legacy data could hold several "active" rows; keep only the newest.
            sub.status = "replaced"
            sub.canceled_at = sub.canceled_at or now
            changed = True
    if changed:
        db.commit()
    return current


def get_user_plan(user_id: str, db: Session) -> SubscriptionPlan:
    """Resolve the plan that currently applies to the user (default: free)."""
    sub = get_active_subscription(user_id, db)
    if sub and sub.plan and sub.plan.is_active:
        return sub.plan
    return get_free_plan(db)


def activate_subscription(
    db: Session,
    user_id: str,
    plan: SubscriptionPlan,
    *,
    interval: str = "monthly",
    source: str = "system",
    reference: str | None = None,
    duration_days: int | None = None,
) -> Subscription:
    """Start, renew or switch a user's subscription. Caller commits.

    * Same paid plan still running → the period is *extended* (renewal).
    * Different plan → the old subscription is marked ``replaced`` and a new
      one starts now.
    * ``duration_days=0`` → open-ended (no expiry). ``None`` → from interval.
    * The free plan never expires.
    """
    interval = interval if interval in INTERVAL_DAYS else "monthly"
    now = _utcnow()
    days = INTERVAL_DAYS[interval] if duration_days is None else duration_days
    is_free = plan.id == FREE_PLAN_ID or (
        (plan.price_monthly or 0) <= 0 and (plan.price_yearly or 0) <= 0
    )

    current = get_active_subscription(user_id, db)
    if (
        current
        and current.plan_id == plan.id
        and not is_free
        and days > 0
        and current.current_period_end is not None
    ):
        base = max(current.current_period_end, now)
        current.current_period_end = base + timedelta(days=days)
        current.cancel_at_period_end = False
        current.billing_interval = interval
        current.external_subscription_id = reference or current.external_subscription_id
        return current

    if current:
        current.status = "replaced"
        current.canceled_at = now

    sub = Subscription(
        user_id=user_id,
        plan_id=plan.id,
        status="active",
        current_period_start=now,
        current_period_end=None if is_free or days <= 0 else now + timedelta(days=days),
        cancel_at_period_end=False,
        billing_interval=interval,
        source=source,
        external_subscription_id=reference,
        created_at=now,
    )
    db.add(sub)
    try:
        from pathmind.services.entitlements import invalidate

        invalidate(user_id)
    except Exception:  # pragma: no cover
        pass
    return sub


def complete_payment(db: Session, payment: Payment, *, confirmed_by: str) -> Subscription | None:
    """Mark a pending payment as paid and activate its plan. Idempotent. Commits."""
    if payment.status == "completed":
        return get_active_subscription(payment.user_id, db)
    if payment.status not in {"pending", "failed", "expired"}:
        raise ValueError(_t("billing.payment_bad_status", status=payment.status))
    plan = db.get(SubscriptionPlan, payment.plan_id)
    if plan is None:
        raise ValueError(_t("billing.payment_plan_missing"))

    ensure_db_user(db, payment.user_id)
    now = _utcnow()
    payment.status = "completed"
    payment.paid_at = now
    payment.confirmed_by = confirmed_by
    sub = activate_subscription(
        db,
        payment.user_id,
        plan,
        interval=payment.billing_interval or "monthly",
        source="payment",
        reference=payment.transaction_id,
    )
    db.commit()
    logger.info(
        "Payment %s completed (%s) → user %s on plan %s until %s",
        payment.transaction_id,
        confirmed_by,
        payment.user_id,
        plan.id,
        sub.current_period_end,
    )
    return sub


def expire_stale_payments(db: Session, max_age_hours: int = 24) -> int:
    """Mark pending payments older than ``max_age_hours`` as expired."""
    cutoff = _utcnow() - timedelta(hours=max_age_hours)
    rows = (
        db.query(Payment)
        .filter(Payment.status == "pending", Payment.created_at < cutoff)
        .all()
    )
    for row in rows:
        row.status = "expired"
    if rows:
        db.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

_VERSION_SUFFIX = r"(-\d[\d.\-]*|-latest|-preview(-[\w.\-]+)?|-exp(-[\w.\-]+)?|@[\w.\-]+)"


def _model_matches(requested: str, allowed: str) -> bool:
    """Exact match, or the allowed id followed by a version suffix.

    ``claude-3-5-sonnet`` allows ``claude-3-5-sonnet-20241022`` and
    ``gemini-3.1-flash-lite`` allows ``gemini-3.1-flash-lite-preview``, but
    ``gpt-4o`` does NOT silently allow ``gpt-4o-mini``.
    Provider prefixes such as ``openai/`` or ``models/`` are ignored.
    """
    req = model_pricing.normalize_model_name(requested)
    allowed = model_pricing.normalize_model_name(allowed)
    if req == allowed:
        return True
    return bool(re.fullmatch(re.escape(allowed) + _VERSION_SUFFIX, req))


def plan_allows_model(plan: SubscriptionPlan, model_name: str, db: Session) -> bool:
    """Whether ``plan`` lists ``model_name`` (directly, by version, or by price alias).

    An empty ``allowed_models`` list means "no restriction".
    """
    clean_model = model_pricing.normalize_model_name(model_name)
    if not clean_model:
        return False
    allowed = [m.strip() for m in (plan.allowed_models or []) if m and m.strip()]
    if not allowed or any(_model_matches(clean_model, m) for m in allowed):
        return True
    price_id = model_pricing.price_for(clean_model, db=db).id
    return price_id != model_pricing.FALLBACK_PRICE_ID and any(
        model_pricing.normalize_model_name(m) == price_id for m in allowed
    )


def check_model_access(
    user_id: str, model_name: str, db: Session, role: str | None = None
) -> tuple[bool, str]:
    """Admins may use every model; others are limited to their plan's list.

    An empty ``allowed_models`` list means "no restriction" for that plan. A
    model is also allowed when it resolves (via the price list aliases) to the
    same price entry as an allowed id, e.g. ``deepseek-chat`` → ``deepseek-v4-flash``.
    """
    if _is_admin(user_id, role, db):
        return True, ""

    clean_model = model_pricing.normalize_model_name(model_name)
    if not clean_model:
        return True, ""
    plan = get_user_plan(user_id, db)
    if plan_allows_model(plan, clean_model, db):
        return True, ""

    return False, _t("billing.model_not_in_plan", model=model_name, plan=plan.name)


_CREDITS_EXPR = func.coalesce(UsageLog.credits_used, UsageLog.total_tokens)


def _usage_today(user_id: str, db: Session) -> dict[str, float]:
    log = db.query(UsageLog).filter_by(user_id=user_id, date=_today_str()).first()
    if not log:
        return {"prompt": 0, "completion": 0, "tokens": 0, "credits": 0, "cost": 0.0}
    credits = log.credits_used if log.credits_used is not None else (log.total_tokens or 0)
    return {
        "prompt": log.prompt_tokens or 0,
        "completion": log.completion_tokens or 0,
        "tokens": log.total_tokens or 0,
        "credits": int(credits or 0),
        "cost": float(log.cost_usd or 0.0),
    }


def _usage_month(user_id: str, db: Session) -> dict[str, float]:
    row = (
        db.query(
            func.coalesce(func.sum(UsageLog.total_tokens), 0),
            func.coalesce(func.sum(_CREDITS_EXPR), 0),
            func.coalesce(func.sum(UsageLog.cost_usd), 0.0),
        )
        .filter(UsageLog.user_id == user_id, UsageLog.date.like(f"{_month_prefix()}%"))
        .one()
    )
    return {"tokens": int(row[0] or 0), "credits": int(row[1] or 0), "cost": float(row[2] or 0.0)}


def _storage_bytes(user_id: str, db: Session) -> int:
    latest = (
        db.query(UsageLog)
        .filter_by(user_id=user_id)
        .order_by(UsageLog.date.desc(), UsageLog.updated_at.desc())
        .first()
    )
    return int(latest.current_storage_bytes or 0) if latest else 0


def check_token_quota(
    user_id: str, estimated_credits: int, db: Session, role: str | None = None
) -> tuple[bool, str]:
    """Check the daily and monthly *credit* quota (name kept for compatibility)."""
    if _is_admin(user_id, role, db):
        return True, ""

    plan = get_user_plan(user_id, db)
    day_limit = int(plan.max_tokens_per_day or 0)
    month_limit = int(plan.max_tokens_per_month or 0)
    try:  # credits bought with reward points raise both limits until they expire
        from pathmind.services.rewards import bonus_credits

        bonus = bonus_credits(db, user_id)
        day_limit += bonus
        month_limit += bonus
    except Exception:  # pragma: no cover
        pass
    used_today = int(_usage_today(user_id, db)["credits"])
    used_month = int(_usage_month(user_id, db)["credits"])

    if used_today >= day_limit or used_month >= month_limit:
        key = (
            "billing.quota_exhausted_day"
            if used_today >= day_limit
            else "billing.quota_exhausted_month"
        )
        return False, _t(
            key,
            plan=plan.name,
            used_today=f"{used_today:,}",
            day_limit=f"{day_limit:,}",
            used_month=f"{used_month:,}",
            month_limit=f"{month_limit:,}",
        )
    if used_today + estimated_credits > day_limit or used_month + estimated_credits > month_limit:
        remaining = min(day_limit - used_today, month_limit - used_month)
        return False, _t(
            "billing.quota_estimate",
            estimate=f"{estimated_credits:,}",
            remaining=f"{remaining:,}",
        )
    return True, ""


def default_llm_model_name() -> str:
    """Model id of the deployment's active LLM profile (best effort)."""
    try:
        from pathmind.multi_user.model_access import admin_catalog

        llm = (admin_catalog().get("services") or {}).get("llm") or {}
        active_profile, active_model = llm.get("active_profile_id"), llm.get("active_model_id")
        for profile in llm.get("profiles") or []:
            if profile.get("id") != active_profile:
                continue
            for model in profile.get("models") or []:
                if model.get("id") == active_model:
                    return str(model.get("model") or model.get("name") or "")
    except Exception as exc:
        logger.debug("Cannot resolve default LLM model: %s", exc)
    return ""


def plan_default_model(user_id: str, db: Session) -> str:
    """The plan's preferred model for users who did not pick one ("" = none)."""
    try:
        plan = get_user_plan(user_id, db)
    except Exception:
        return ""
    return str(getattr(plan, "default_model", "") or "").strip()


def model_matches_name(candidate: str, wanted: str, db: Session | None = None) -> bool:
    """Whether a catalog model id (``models/gemini-…``, ``deepseek-flash``…) is ``wanted``.

    Exact / version-suffix match, or both resolve to the same price entry
    (``deepseek-flash`` and ``deepseek-v4-flash`` are one model).
    """
    if not candidate or not wanted:
        return False
    if _model_matches(candidate, wanted):
        return True
    a = model_pricing.price_for(model_pricing.normalize_model_name(candidate), db=db).id
    b = model_pricing.price_for(model_pricing.normalize_model_name(wanted), db=db).id
    return a == b and a != model_pricing.FALLBACK_PRICE_ID


def model_name_for_selection(selection: Any) -> str:
    """Resolve an ``llm_selection`` ({profile_id, model_id} or {model}) to a model id."""
    if not isinstance(selection, dict):
        return ""
    if selection.get("model"):
        return str(selection["model"])
    profile_id, model_id = selection.get("profile_id"), selection.get("model_id")
    if not profile_id or not model_id:
        return ""
    try:
        from pathmind.multi_user.model_access import admin_catalog

        llm = (admin_catalog().get("services") or {}).get("llm") or {}
        for profile in llm.get("profiles") or []:
            if profile.get("id") != profile_id:
                continue
            for model in profile.get("models") or []:
                if model.get("id") == model_id:
                    return str(model.get("model") or model.get("name") or "")
    except Exception as exc:
        logger.debug("Cannot resolve selected model: %s", exc)
    return ""


def check_turn_allowed(
    user_id: str,
    db: Session,
    *,
    role: str | None = None,
    model: str | None = None,
    capability: str | None = None,
) -> tuple[bool, str]:
    """Pre-turn gate: model allowed for the plan + enough credits for the job."""
    if _is_admin(user_id, role, db):
        return True, ""
    effective_model = model or default_llm_model_name()
    if effective_model:
        allowed, reason = check_model_access(user_id, effective_model, db, role=role)
        if not allowed:
            return False, reason
    estimate = model_pricing.estimate_turn_credits(capability, effective_model, db=db)
    return check_token_quota(user_id, estimate, db, role=role)


def record_llm_call(
    user_id: str,
    record: dict[str, Any],
    db: Session,
    *,
    price: "model_pricing.Price | None" = None,
) -> tuple[int, float]:
    """Charge one LLM call (a ``CallMeasurement`` record) to the user.

    Returns ``(credits, cost_usd)``. Writes the daily total and the per-model
    breakdown used by the admin margin report. ``price`` overrides the lookup
    by model name (embedding models missing from the price list).
    """
    if not user_id:
        return 0, 0.0
    prompt = int(record.get("prompt_tokens") or 0)
    completion = int(record.get("completion_tokens") or 0)
    cached = int(record.get("cache_read_input_tokens") or 0)
    if prompt <= 0 and completion <= 0:
        return 0, 0.0
    model = str(record.get("model") or "")
    if price is None:
        price = model_pricing.price_for(model, record.get("provider"), db=db)
    cost = model_pricing.call_cost_usd(price, prompt, completion, cached)
    credits = model_pricing.usd_to_credits(cost)

    ensure_db_user(db, user_id)
    today = _today_str()
    log = db.query(UsageLog).filter_by(user_id=user_id, date=today).first()
    if not log:
        log = UsageLog(
            user_id=user_id,
            date=today,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            credits_used=0,
            cost_usd=0.0,
            current_storage_bytes=_storage_bytes(user_id, db),
        )
        db.add(log)
    elif log.credits_used is None:
        log.credits_used = log.total_tokens or 0
    log.prompt_tokens = (log.prompt_tokens or 0) + prompt
    log.completion_tokens = (log.completion_tokens or 0) + completion
    log.total_tokens = (log.total_tokens or 0) + prompt + completion
    log.credits_used = (log.credits_used or 0) + credits
    log.cost_usd = float(log.cost_usd or 0.0) + cost

    model_key = model_pricing.normalize_model_name(model)[:120] or "unknown"
    row = db.query(UsageModelLog).filter_by(user_id=user_id, date=today, model=model_key).first()
    if not row:
        row = UsageModelLog(
            user_id=user_id,
            date=today,
            model=model_key,
            price_id=price.id,
            calls=0,
            prompt_tokens=0,
            cached_tokens=0,
            completion_tokens=0,
            credits_used=0,
            cost_usd=0.0,
        )
        db.add(row)
    row.calls = (row.calls or 0) + 1
    row.prompt_tokens = (row.prompt_tokens or 0) + prompt
    row.cached_tokens = (row.cached_tokens or 0) + cached
    row.completion_tokens = (row.completion_tokens or 0) + completion
    row.credits_used = (row.credits_used or 0) + credits
    row.cost_usd = float(row.cost_usd or 0.0) + cost
    db.commit()
    return credits, cost


def record_token_usage(
    user_id: str, prompt_tokens: int, completion_tokens: int, db: Session, model: str = ""
) -> None:
    """Legacy entry point: charge tokens without a per-call breakdown."""
    record_llm_call(
        user_id,
        {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "model": model},
        db,
    )


def check_storage_quota(
    user_id: str, file_size_bytes: int, db: Session, role: str | None = None
) -> tuple[bool, str]:
    """Check single file size and overall storage quota."""
    if _is_admin(user_id, role, db):
        return True, ""

    plan = get_user_plan(user_id, db)
    if file_size_bytes > (plan.max_upload_file_size_bytes or 0):
        max_mb = (plan.max_upload_file_size_bytes or 0) / (1024 * 1024)
        return False, _t("billing.file_too_large", max_mb=f"{max_mb:.0f}")

    current_storage = _storage_bytes(user_id, db)
    if current_storage + file_size_bytes > (plan.max_storage_bytes or 0):
        max_storage_mb = (plan.max_storage_bytes or 0) / (1024 * 1024)
        return False, _t("billing.storage_full", max_mb=f"{max_storage_mb:.0f}")
    return True, ""


def check_storage_batch(
    user_id: str, sizes: list[int], db: Session, role: str | None = None
) -> tuple[bool, str]:
    """Check a whole upload batch: each file's size and the batch total."""
    for size in sizes:
        ok, reason = check_storage_quota(user_id, int(size or 0), db, role=role)
        if not ok:
            return ok, reason
    if len(sizes) > 1:
        # Per-file limit already checked; now only the cumulative total matters.
        if _is_admin(user_id, role, db):
            return True, ""
        plan = get_user_plan(user_id, db)
        total = sum(int(x or 0) for x in sizes)
        if _storage_bytes(user_id, db) + total > (plan.max_storage_bytes or 0):
            max_storage_mb = (plan.max_storage_bytes or 0) / (1024 * 1024)
            return False, _t("billing.batch_too_large", max_mb=f"{max_storage_mb:.0f}")
    return True, ""


def update_user_storage(user_id: str, delta_bytes: int, db: Session) -> None:
    """Update user's current total storage bytes (carried forward day to day)."""
    if not user_id:
        return
    ensure_db_user(db, user_id)
    today = _today_str()
    log = db.query(UsageLog).filter_by(user_id=user_id, date=today).first()
    if not log:
        new_storage = max(0, _storage_bytes(user_id, db) + delta_bytes)
        db.add(
            UsageLog(
                user_id=user_id,
                date=today,
                credits_used=0,
                cost_usd=0.0,
                current_storage_bytes=new_storage,
            )
        )
    else:
        log.current_storage_bytes = max(0, (log.current_storage_bytes or 0) + delta_bytes)
    db.commit()


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _fmt_bytes(n: int) -> str:
    if n >= 1024**3:
        value = n / 1024**3
        return f"{value:.0f} GB" if value.is_integer() else f"{value:.1f} GB"
    return f"{n / 1024**2:.0f} MB"


def _fmt_int(n: int) -> str:
    return f"{int(n):,}"


def plan_features(plan: SubscriptionPlan, language: str | None = None) -> list[str]:
    """Admin-provided features, or a list generated from the real limits.

    Generating from limits guarantees the pricing page never advertises
    numbers that differ from what is actually enforced.
    """
    custom = [f for f in (plan.features or []) if isinstance(f, str) and f.strip()]
    if custom:
        return custom
    models = plan.allowed_models or []
    features = [
        _t(
            "billing.feature_credits",
            language=language,
            month=_fmt_int(plan.max_tokens_per_month or 0),
            day=_fmt_int(plan.max_tokens_per_day or 0),
        ),
        _t(
            "billing.feature_storage",
            language=language,
            size=_fmt_bytes(plan.max_storage_bytes or 0),
        ),
        _t(
            "billing.feature_upload",
            language=language,
            size=_fmt_bytes(plan.max_upload_file_size_bytes or 0),
        ),
        _t("billing.feature_all_models", language=language)
        if not models
        else _t("billing.feature_models", language=language, count=len(models)),
    ]
    if plan.allow_custom_api_key:
        features.append(_t("billing.feature_own_key", language=language))
    features.extend(entitlement_features(plan, language))
    return features


def entitlement_features(plan: SubscriptionPlan, language: str | None = None) -> list[str]:
    """Pricing-page lines for what the plan unlocks (see services.entitlements)."""
    from pathmind.services.entitlements import plan_entitlements

    ent = plan_entitlements(plan)
    lines = [_t("billing.feature_ent_practice", language=language)]
    mastery, books = ent["mastery_per_month"], ent["books_per_month"]
    if mastery is None and books is None:
        lines.append(_t("billing.feature_ent_mastery_unlimited", language=language))
    elif (mastery or books) and ent.get("allowance_period") == "lifetime":
        lines.append(
            _t("billing.feature_ent_trial", language=language, count=mastery or 0, books=books or 0)
        )
    elif mastery or books:
        lines.append(
            _t(
                "billing.feature_ent_mastery",
                language=language,
                count=mastery if mastery is not None else "∞",
                books=books if books is not None else "∞",
            )
        )
    if ent["reading"] or ent["watching"]:
        lines.append(_t("billing.feature_ent_reading", language=language))
    if ent["space_extras"]:
        lines.append(_t("billing.feature_ent_space", language=language))
    if ent["courses"]:
        lines.append(_t("billing.feature_ent_courses", language=language))
    if ent["memory"]:
        lines.append(_t("billing.feature_ent_memory", language=language))
    if ent["community"]:
        lines.append(_t("billing.feature_ent_community", language=language))
    if ent["max_skills"] is None:
        lines.append(_t("billing.feature_ent_skills_unlimited", language=language))
    elif ent["max_skills"]:
        key = "billing.feature_ent_skills_one" if ent["max_skills"] == 1 else "billing.feature_ent_skills"
        lines.append(_t(key, language=language, count=ent["max_skills"]))
    if ent["max_partners"] is None:
        lines.append(_t("billing.feature_ent_partners_unlimited", language=language))
    elif ent["max_partners"]:
        key = "billing.feature_ent_partners_one" if ent["max_partners"] == 1 else "billing.feature_ent_partners"
        lines.append(_t(key, language=language, count=ent["max_partners"]))
    return lines


def plan_to_dict(plan: SubscriptionPlan, language: str | None = None) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "description": plan.description or "",
        "sort_order": plan.sort_order or 0,
        "price_monthly": plan.price_monthly or 0.0,
        "price_yearly": plan.price_yearly or 0.0,
        "price_usd": plan.price_monthly or 0.0,
        "max_tokens_per_day": plan.max_tokens_per_day or 0,
        "max_tokens_per_month": plan.max_tokens_per_month or 0,
        "max_storage_bytes": plan.max_storage_bytes or 0,
        "max_upload_file_size_bytes": plan.max_upload_file_size_bytes or 0,
        "allowed_models": plan.allowed_models or [],
        "allow_custom_api_key": bool(plan.allow_custom_api_key),
        "default_model": plan.default_model or "",
        "is_active": bool(plan.is_active),
        "custom_features": plan.features or [],
        "features": plan_features(plan, language),
        "entitlements": _entitlements_dict(plan),
    }


def _entitlements_dict(plan: SubscriptionPlan) -> dict[str, Any]:
    from pathmind.services.entitlements import plan_entitlements

    return plan_entitlements(plan)


def subscription_to_dict(sub: Subscription | None) -> dict[str, Any] | None:
    if sub is None:
        return None
    days_left = None
    if sub.current_period_end is not None:
        delta = sub.current_period_end - _utcnow()
        days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))
    return {
        "id": sub.id,
        "plan_id": sub.plan_id,
        "plan_name": sub.plan.name if sub.plan else sub.plan_id,
        "status": sub.status,
        "billing_interval": sub.billing_interval or "monthly",
        "source": sub.source or "system",
        "current_period_start": _iso(sub.current_period_start),
        "current_period_end": _iso(sub.current_period_end),
        "cancel_at_period_end": bool(sub.cancel_at_period_end),
        "days_left": days_left,
    }


def payment_to_dict(payment: Payment, username: str | None = None) -> dict[str, Any]:
    return {
        "id": payment.id,
        "transaction_id": payment.transaction_id,
        "user_id": payment.user_id,
        "username": username,
        "plan_id": payment.plan_id,
        "plan_name": payment.plan.name if payment.plan else payment.plan_id,
        "amount": payment.amount,
        "currency": payment.currency or "USD",
        "amount_vnd": payment.amount_vnd,
        "billing_interval": payment.billing_interval or "monthly",
        "payment_method": payment.payment_method,
        "status": payment.status,
        "created_at": _iso(payment.created_at),
        "paid_at": _iso(payment.paid_at),
        "confirmed_by": payment.confirmed_by,
        "note": payment.note,
        "gateway_reference": getattr(payment, "gateway_reference", None),
    }


def _pct(used: int, limit: int) -> float:
    return min(100.0, round(used / (limit or 1) * 100, 1))


def get_user_usage_summary(user_id: str, db: Session, role: str | None = None) -> dict:
    """Active plan, subscription state, credits used today/this month, storage."""
    is_admin = _is_admin(user_id, role, db)
    sub = get_active_subscription(user_id, db)
    plan = sub.plan if sub and sub.plan and sub.plan.is_active else get_free_plan(db)
    today = _usage_today(user_id, db)
    month = _usage_month(user_id, db)
    storage_bytes = _storage_bytes(user_id, db)
    day_limit = int(plan.max_tokens_per_day or 0)
    month_limit = int(plan.max_tokens_per_month or 0)
    default_model = default_llm_model_name()
    default_price = model_pricing.price_for(default_model, db=db) if default_model else None

    return {
        "user_id": user_id,
        "role": "admin" if is_admin else "user",
        "is_unlimited": is_admin,
        "plan": plan_to_dict(plan),
        "subscription": subscription_to_dict(sub),
        "default_model": (
            {
                "id": model_pricing.normalize_model_name(default_model),
                "name": default_price.display_name,
                "multiplier": round(default_price.multiplier, 2),
            }
            if default_price
            else None
        ),
        "usage_today": {
            "prompt_tokens": today["prompt"],
            "completion_tokens": today["completion"],
            "total_tokens": today["tokens"],
            "credits": today["credits"],
            "limit": day_limit,
            "remaining": max(0, day_limit - int(today["credits"])),
            "percent_used": _pct(int(today["credits"]), day_limit),
        },
        "usage_month": {
            "total_tokens": month["tokens"],
            "credits": month["credits"],
            "limit": month_limit,
            "remaining": max(0, month_limit - int(month["credits"])),
            "percent_used": _pct(int(month["credits"]), month_limit),
        },
        "storage": {
            "used_bytes": storage_bytes,
            "limit_bytes": plan.max_storage_bytes or 0,
            "max_file_size_bytes": plan.max_upload_file_size_bytes or 0,
            "percent_used": _pct(storage_bytes, plan.max_storage_bytes or 0),
        },
    }
