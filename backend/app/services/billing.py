"""Plans, entitlements, quotas and usage metering.

* R&D and admin accounts are never limited (they study with a separate learner account).
* Learners and educators get what their plan allows; no active subscription = the free plan.
* Every AI call is metered: tokens -> cost from ``model_prices`` -> credits, charged per user per day.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    FeatureUsage, LLMCall, ModelPrice, Plan, Subscription, UsageByModel, UsageDaily, User, utcnow,
)
from .app_settings import get_setting

UNLIMITED_ROLES = ("rnd", "admin")
MB = 1024 * 1024
GB = 1024 * MB

# Feature switches (bool) and counted limits (int, None = unlimited) a plan can carry.
FEATURE_KEYS = ("kb", "courses", "notebooks", "question_bank", "reading", "community", "cowriter", "rooms")
COUNT_KEYS = ("mastery_paths", "books")  # counted per allowance period
MAX_KEYS = ("max_partners", "max_skills", "max_kbs")  # counted over what exists now

DEFAULT_ENTITLEMENTS: dict[str, Any] = {
    **{k: True for k in FEATURE_KEYS},
    "mastery_paths": None, "books": None, "max_partners": None, "max_skills": None, "max_kbs": None,
    "allowance_period": "month",
}

SEED_PLANS = [
    {
        "id": "free", "name": "Free", "description": "Try PathMind: AI tutor, practice and your own knowledge base.",
        "price_monthly_usd": Decimal("0"), "price_yearly_usd": Decimal("0"),
        "credits_per_day": 150_000, "credits_per_month": 1_500_000, "storage_bytes": 200 * MB,
        "max_upload_bytes": 10 * MB, "sort_order": 0,
        "entitlements": {"kb": True, "courses": False, "notebooks": False, "question_bank": False,
                         "reading": False, "community": False, "cowriter": False, "rooms": True,
                         "mastery_paths": 1, "books": 1, "max_partners": 1, "max_skills": 0, "max_kbs": 2,
                         "allowance_period": "lifetime"},
        "features": ["AI tutor chat", "1 mastery path and 1 book to try", "2 knowledge bases"],
    },
    {
        "id": "plus", "name": "Plus", "description": "Everything for steady self-study.",
        "price_monthly_usd": Decimal("4.99"), "price_yearly_usd": Decimal("49"),
        "credits_per_day": 1_000_000, "credits_per_month": 11_000_000, "storage_bytes": 2 * GB,
        "max_upload_bytes": 25 * MB, "sort_order": 1,
        "entitlements": {**{k: True for k in FEATURE_KEYS}, "mastery_paths": 10, "books": 10, "max_partners": 3,
                         "max_skills": 5, "max_kbs": 10, "allowance_period": "month"},
        "features": ["Courses, notebooks, question bank", "Reading & co-writer", "10 mastery paths and books a month"],
    },
    {
        "id": "pro", "name": "Pro", "description": "For heavy learners.",
        "price_monthly_usd": Decimal("9.99"), "price_yearly_usd": Decimal("99"),
        "credits_per_day": 2_000_000, "credits_per_month": 22_000_000, "storage_bytes": 10 * GB,
        "max_upload_bytes": 100 * MB, "sort_order": 2,
        "entitlements": {**{k: True for k in FEATURE_KEYS}, "mastery_paths": None, "books": None, "max_partners": 10,
                         "max_skills": 20, "max_kbs": None, "allowance_period": "month"},
        "features": ["Unlimited mastery paths and books", "10 partners, 20 skills", "10 GB storage"],
    },
    {
        "id": "enterprise", "name": "Unlimited Campus", "description": "For companies and schools.",
        "price_monthly_usd": Decimal("29.99"), "price_yearly_usd": Decimal("299"),
        "credits_per_day": 6_000_000, "credits_per_month": 66_000_000, "storage_bytes": 50 * GB,
        "max_upload_bytes": 200 * MB, "sort_order": 3,
        "entitlements": {**{k: True for k in FEATURE_KEYS}, "mastery_paths": None, "books": None, "max_partners": None,
                         "max_skills": None, "max_kbs": None, "allowance_period": "month"},
        "features": ["Everything unlimited", "50 GB storage", "Priority models"],
    },
]

# USD per 1M tokens (input, cached input, output). "*" is the safe (expensive) fallback.
SEED_PRICES = [
    ("*", "Unknown model", "", "2", None, "8", []),
    ("*embedding", "Unknown embedding model", "", "0.15", None, "0", []),
    ("mock", "Mock (testing)", "mock", "0", None, "0", ["mock-chat", "mock-embed"]),
    ("gpt-4o", "GPT-4o", "openai", "2.5", "1.25", "10", []),
    ("gpt-4o-mini", "GPT-4o mini", "openai", "0.15", "0.075", "0.6", []),
    ("gpt-4.1", "GPT-4.1", "openai", "2", "0.5", "8", []),
    ("gpt-4.1-mini", "GPT-4.1 mini", "openai", "0.4", "0.1", "1.6", []),
    ("text-embedding-3-small", "text-embedding-3-small", "openai", "0.02", None, "0", []),
    ("text-embedding-3-large", "text-embedding-3-large", "openai", "0.13", None, "0", []),
    ("claude-sonnet-4", "Claude Sonnet 4", "anthropic", "3", "0.3", "15", ["claude-sonnet-4-5"]),
    ("claude-haiku-4", "Claude Haiku 4", "anthropic", "1", "0.1", "5", ["claude-haiku-4-5"]),
    ("gemini-2.5-flash", "Gemini 2.5 Flash", "gemini", "0.3", "0.075", "2.5", []),
    ("gemini-2.5-pro", "Gemini 2.5 Pro", "gemini", "1.25", "0.31", "10", []),
    ("gemini-embedding-001", "Gemini embedding", "gemini", "0.15", None, "0", []),
    ("deepseek-chat", "DeepSeek V3 (chat)", "deepseek", "0.27", "0.07", "1.1", ["deepseek-v3"]),
    ("deepseek-reasoner", "DeepSeek R1", "deepseek", "0.55", "0.14", "2.19", []),
]


def seed(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(Plan)) == 0:
        for p in SEED_PLANS:
            db.add(Plan(**p))
    have = set(db.scalars(select(ModelPrice.model_key)))
    for key, name, provider, inp, cached, out, aliases in SEED_PRICES:
        if key not in have:
            db.add(ModelPrice(model_key=key, display_name=name, provider=provider, input_per_m=Decimal(inp),
                              cached_input_per_m=Decimal(cached) if cached else None, output_per_m=Decimal(out),
                              aliases=aliases))
    db.commit()


# ── Plans and subscriptions ──────────────────────────────────────────────────


def active_subscription(db: Session, user_id: str) -> Subscription | None:
    now = utcnow()
    subs = db.scalars(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status.in_(("active", "canceled")))
        .order_by(Subscription.created_at.desc())
    ).all()
    for s in subs:
        if s.period_end is not None and s.period_end <= now:
            s.status = "expired"  # lazily expire; committed with the caller's transaction
            continue
        if s.status == "canceled" and not s.cancel_at_period_end:
            continue
        return s
    return None


def current_plan(db: Session, user: User) -> tuple[Plan | None, Subscription | None]:
    sub = active_subscription(db, user.id)
    if sub is not None:
        plan = db.get(Plan, sub.plan_id)
        if plan is not None:
            return plan, sub
    return db.get(Plan, "free"), None


def is_unlimited(user: User) -> bool:
    return user.role in UNLIMITED_ROLES


def entitlements(db: Session, user: User) -> dict[str, Any]:
    if is_unlimited(user):
        return dict(DEFAULT_ENTITLEMENTS, unlimited=True)
    plan, _ = current_plan(db, user)
    ent = dict(DEFAULT_ENTITLEMENTS)
    if plan is not None:
        for k, v in (plan.entitlements or {}).items():
            if k in FEATURE_KEYS:
                ent[k] = bool(v)
            elif k in COUNT_KEYS or k in MAX_KEYS:
                ent[k] = None if v is None else (int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else ent[k])
            elif k == "allowance_period" and v in ("month", "lifetime"):
                ent[k] = v
    ent["unlimited"] = False
    return ent


def cheapest_plan_with(db: Session, feature: str) -> dict | None:
    for plan in db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)):
        e = plan.entitlements or {}
        v = e.get(feature, DEFAULT_ENTITLEMENTS.get(feature))
        if v is True or v is None or (isinstance(v, int) and not isinstance(v, bool) and v > 0):
            if plan.id != "free" or v is True:
                return {"id": plan.id, "name": plan.name}
    return None


def plan_required(db: Session, feature: str, message: str) -> HTTPException:
    return HTTPException(status_code=402, detail={
        "code": "plan_required", "feature": feature, "message": message,
        "upgrade_to": cheapest_plan_with(db, feature),
    })


def require_feature(db: Session, user: User, feature: str) -> None:
    if not entitlements(db, user).get(feature, True):
        raise plan_required(db, feature, "Your plan does not include this feature.")


def check_max(db: Session, user: User, key: str, current: int) -> None:
    limit = entitlements(db, user).get(key)
    if limit is not None and current >= limit:
        raise plan_required(db, key, f"Your plan allows {limit}. Upgrade to add more.")


def _period(ent: dict) -> str:
    return "lifetime" if ent.get("allowance_period") == "lifetime" else utcnow().strftime("%Y-%m")


def usage_count(db: Session, user: User, feature: str) -> int:
    ent = entitlements(db, user)
    row = db.get(FeatureUsage, (user.id, feature, _period(ent)))
    return row.count if row else 0


def reserve(db: Session, user: User, feature: str) -> None:
    """Check and count one creation of ``feature`` (e.g. a mastery path). Caller commits."""
    ent = entitlements(db, user)
    limit = ent.get(feature)
    period = _period(ent)
    row = db.get(FeatureUsage, (user.id, feature, period))
    used = row.count if row else 0
    if limit is not None and used >= limit:
        msg = ("Your free trial of this feature is used up." if period == "lifetime"
               else f"You have used all {limit} for this month.")
        raise plan_required(db, feature, msg)
    if row is None:
        row = FeatureUsage(user_id=user.id, feature=feature, period=period, count=0)
        db.add(row)
    row.count = used + 1


def release(db: Session, user: User, feature: str) -> None:
    ent = entitlements(db, user)
    row = db.get(FeatureUsage, (user.id, feature, _period(ent)))
    if row is not None and row.count > 0:
        row.count -= 1


def check_upload(db: Session, user: User, size: int) -> None:
    if is_unlimited(user):
        return
    plan, _ = current_plan(db, user)
    if plan is None:
        return
    if plan.max_upload_bytes is not None and size > plan.max_upload_bytes:
        raise plan_required(db, "max_upload_bytes",
                            f"Your plan allows files up to {plan.max_upload_bytes // MB} MB.")
    if plan.storage_bytes is not None:
        from .storage import used_bytes

        if used_bytes(db, user.id) + size > plan.storage_bytes:
            raise plan_required(db, "storage_bytes", "Your storage is full. Delete files or upgrade your plan.")


def activate(db: Session, user_id: str, plan_id: str, *, interval: str = "monthly", days: int | None = None,
             source: str = "admin", bulk_import_id: str | None = None) -> Subscription:
    """Give ``user_id`` a plan. Same plan still running = extend it; otherwise replace. Caller commits.

    ``days`` None = by interval (30 / 365), 0 = never expires.
    """
    now = utcnow()
    length = None if days == 0 else timedelta(days=days if days else (365 if interval == "yearly" else 30))
    current = active_subscription(db, user_id)
    if current is not None and current.plan_id == plan_id and current.period_end is not None and length is not None:
        current.period_end = max(current.period_end, now) + length
        current.status, current.cancel_at_period_end = "active", False
        return current
    if current is not None:
        current.status = "replaced"
    sub = Subscription(user_id=user_id, plan_id=plan_id, billing_interval=interval if days is None else "none",
                       source=source, period_start=now, period_end=(now + length) if length else None,
                       bulk_import_id=bulk_import_id)
    db.add(sub)
    db.flush()
    return sub


# ── Prices, credits and metering ─────────────────────────────────────────────


def price_for(db: Session, model: str, purpose: str = "chat") -> ModelPrice | None:
    name = (model or "").split("/")[-1].strip().lower()
    rows = db.scalars(select(ModelPrice).where(ModelPrice.is_active.is_(True))).all()
    by_key = {r.model_key.lower(): r for r in rows}
    if name in by_key:
        return by_key[name]
    for r in rows:
        if name in [a.lower() for a in (r.aliases or [])]:
            return r
    best = None
    for key, r in by_key.items():
        if key not in ("*", "*embedding") and name.startswith(key) and (best is None or len(key) > len(best.model_key)):
            best = r
    if best is not None:
        return best
    return by_key.get("*embedding" if purpose == "embedding" else "*")


def cost_usd(price: ModelPrice | None, prompt: int, cached: int, completion: int) -> Decimal:
    if price is None:
        return Decimal(0)
    cached_rate = price.cached_input_per_m if price.cached_input_per_m is not None else price.input_per_m
    total = (Decimal(max(prompt - cached, 0)) * Decimal(price.input_per_m)
             + Decimal(cached) * Decimal(cached_rate) + Decimal(completion) * Decimal(price.output_per_m))
    return (total / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def credits_for(db: Session, cost: Decimal) -> int:
    per_mtok = Decimal(str(get_setting(db, "billing").get("credit_usd_per_mtok", 0.25) or 0.25))
    return int((cost / per_mtok * Decimal(1_000_000)).to_integral_value()) if cost > 0 else 0


def credits_used(db: Session, user_id: str) -> tuple[int, int]:
    today = utcnow().date()
    month_start = today.replace(day=1)
    day = db.scalar(select(UsageDaily.credits_used).where(UsageDaily.user_id == user_id, UsageDaily.day == today)) or 0
    month = db.scalar(select(func.coalesce(func.sum(UsageDaily.credits_used), 0)).where(
        UsageDaily.user_id == user_id, UsageDaily.day >= month_start)) or 0
    return int(day), int(month)


def check_credits(db: Session, user: User, estimate: int = 0) -> None:
    if is_unlimited(user):
        return
    plan, _ = current_plan(db, user)
    if plan is None:
        return
    day, month = credits_used(db, user.id)
    if plan.credits_per_day is not None and day + estimate > plan.credits_per_day:
        raise plan_required(db, "credits", "You have used today's AI credits. They refill tomorrow (UTC), or upgrade.")
    if plan.credits_per_month is not None and month + estimate > plan.credits_per_month:
        raise plan_required(db, "credits", "You have used this month's AI credits. Upgrade for more.")


def record_call(db: Session, *, user_id: str | None, model: str, provider: str, purpose: str,
                prompt: int, cached: int, completion: int, latency_ms: int = 0, status: str = "ok",
                error: str = "", chat_session_id: str | None = None) -> LLMCall:
    """Write the ledger row and the daily roll-ups. Caller commits."""
    price = price_for(db, model, purpose)
    cost = cost_usd(price, prompt, cached, completion) if status == "ok" else Decimal(0)
    credits = credits_for(db, cost)
    call = LLMCall(user_id=user_id, model=model[:120], provider=provider[:40], purpose=purpose[:40],
                   chat_session_id=chat_session_id, prompt_tokens=prompt, cached_tokens=cached,
                   completion_tokens=completion, credits=credits, cost_usd=cost, latency_ms=latency_ms,
                   status=status, error=error[:2000])
    db.add(call)
    if user_id and status == "ok":
        today: date = utcnow().date()
        daily = db.get(UsageDaily, (user_id, today))
        if daily is None:
            daily = UsageDaily(user_id=user_id, day=today, prompt_tokens=0, cached_tokens=0, completion_tokens=0,
                               credits_used=0, cost_usd=Decimal(0), storage_bytes=0)
            db.add(daily)
        daily.prompt_tokens += prompt
        daily.cached_tokens += cached
        daily.completion_tokens += completion
        daily.credits_used += credits
        daily.cost_usd = Decimal(daily.cost_usd or 0) + cost
        bym = db.get(UsageByModel, (user_id, today, model[:120]))
        if bym is None:
            bym = UsageByModel(user_id=user_id, day=today, model=model[:120], calls=0, prompt_tokens=0,
                               cached_tokens=0, completion_tokens=0, credits_used=0, cost_usd=Decimal(0))
            db.add(bym)
        bym.calls += 1
        bym.prompt_tokens += prompt
        bym.cached_tokens += cached
        bym.completion_tokens += completion
        bym.credits_used += credits
        bym.cost_usd = Decimal(bym.cost_usd or 0) + cost
    return call


def plan_public(p: Plan) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "price_monthly_usd": float(p.price_monthly_usd or 0), "price_yearly_usd": float(p.price_yearly_usd or 0),
        "credits_per_day": p.credits_per_day, "credits_per_month": p.credits_per_month,
        "storage_bytes": p.storage_bytes, "max_upload_bytes": p.max_upload_bytes,
        "allowed_models": p.allowed_models or [], "default_model": p.default_model,
        "entitlements": {**{k: v for k, v in DEFAULT_ENTITLEMENTS.items()}, **(p.entitlements or {})},
        "features": p.features or [], "sort_order": p.sort_order, "is_active": p.is_active,
    }


def sub_public(s: Subscription | None) -> dict | None:
    if s is None:
        return None
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {"id": s.id, "plan_id": s.plan_id, "status": s.status, "billing_interval": s.billing_interval,
            "source": s.source, "period_start": iso(s.period_start), "period_end": iso(s.period_end),
            "cancel_at_period_end": s.cancel_at_period_end}


def summary(db: Session, user: User) -> dict:
    plan, sub = current_plan(db, user)
    day, month = credits_used(db, user.id)
    from .storage import used_bytes

    ent = entitlements(db, user)
    counts = {k: usage_count(db, user, k) for k in COUNT_KEYS}
    return {
        "unlimited": is_unlimited(user),
        "plan": plan_public(plan) if plan else None,
        "subscription": sub_public(sub),
        "entitlements": ent,
        "usage": {"credits_today": day, "credits_month": month, "storage_bytes": used_bytes(db, user.id), **counts},
    }


def model_allowed(db: Session, user: User, model: str) -> bool:
    if is_unlimited(user):
        return True
    plan, _ = current_plan(db, user)
    allowed = [m.lower() for m in (plan.allowed_models or [])] if plan else []
    if not allowed:
        return True
    name = (model or "").lower()
    return any(name == a or name.startswith(a + "-") for a in allowed)

