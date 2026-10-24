"""Admin: subscription plans, user subscriptions, payments and system stats.

Mounted at ``/api/admin`` with ``require_admin`` on the whole router.
User identity (username / role / delete) is still managed by ``/api/auth/users``;
this router only manages the billing side of each account.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from pathmind.services.i18n import t as _t
from pathmind.api.routers.auth import require_admin
from pathmind.database.connection import get_db
from pathmind.database.models import (
    ModelPrice,
    Payment,
    Subscription,
    SubscriptionPlan,
    UsageLog,
    UsageModelLog,
)
from pathmind.services import model_pricing
from pathmind.services.quota_service import (
    FREE_PLAN_ID,
    _month_prefix,
    _today_str,
    _utcnow,
    activate_subscription,
    complete_payment,
    ensure_db_user,
    expire_stale_payments,
    get_active_subscription,
    get_free_plan,
    payment_to_dict,
    plan_to_dict,
    subscription_to_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PLAN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UpdateUserRoleBody(BaseModel):
    role: Literal["admin", "rnd", "educator", "user"]


class AssignPlanBody(BaseModel):
    plan_id: str
    # None → from interval (30/365 days); 0 → no expiry.
    duration_days: int | None = Field(default=None, ge=0, le=3650)
    interval: Literal["monthly", "yearly"] = "monthly"


class UpdatePlanModelsBody(BaseModel):
    allowed_models: list[str]


class PlanFields(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    features: list[str] | None = None
    sort_order: int | None = None
    price_monthly: float | None = Field(default=None, ge=0)
    price_yearly: float | None = Field(default=None, ge=0)
    max_tokens_per_day: int | None = Field(default=None, ge=0)
    max_tokens_per_month: int | None = Field(default=None, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)
    max_upload_file_size_bytes: int | None = Field(default=None, ge=0)
    allowed_models: list[str] | None = None
    allow_custom_api_key: bool | None = None
    default_model: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    # Feature switches and limits (see services.entitlements); null limit = unlimited.
    entitlements: dict[str, Any] | None = None


class CreatePlanBody(PlanFields):
    id: str
    name: str = Field(min_length=1, max_length=100)


class PaymentActionBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in items:
        s = (item or "").strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    return cleaned


def _auth_users() -> list[dict]:
    from pathmind.services.auth import list_users

    try:
        return list_users()
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not read auth store: %s", exc)
        return []


def _auth_user_by_id(user_id: str) -> dict | None:
    return next((u for u in _auth_users() if str(u.get("id") or "") == user_id), None)


def _apply_plan_fields(plan: SubscriptionPlan, body: PlanFields) -> None:
    data = body.model_dump(exclude_unset=True, exclude={"id"})
    if "allowed_models" in data and data["allowed_models"] is not None:
        data["allowed_models"] = _clean_list(data["allowed_models"])
    if "features" in data and data["features"] is not None:
        data["features"] = _clean_list(data["features"])
    raw_entitlements = data.pop("entitlements", None)
    for key, value in data.items():
        if value is None and key not in {"description", "features"}:
            continue
        setattr(plan, key, value)
    if raw_entitlements is not None:
        from pathmind.services import entitlements as ent_service

        plan.entitlements = ent_service.normalize(
            {**ent_service.plan_entitlements(plan), **raw_entitlements},
            str(plan.id or ""),
            plan.price_monthly,
        )
        ent_service.invalidate()
    if (plan.max_tokens_per_month or 0) and (plan.max_tokens_per_day or 0) > (
        plan.max_tokens_per_month or 0
    ):
        raise HTTPException(
            status_code=400, detail=_t("admin.day_gt_month")
        )
    default_model = (plan.default_model or "").strip()
    plan.default_model = default_model
    if default_model and plan.allowed_models:
        from pathmind.database.connection import SessionLocal
        from pathmind.services.quota_service import plan_allows_model

        check_db = SessionLocal()
        try:
            allowed = plan_allows_model(plan, default_model, check_db)
        finally:
            check_db.close()
        if not allowed:
            raise HTTPException(status_code=400, detail=_t("admin.default_model_not_allowed"))
    if (plan.max_upload_file_size_bytes or 0) > (plan.max_storage_bytes or 0):
        raise HTTPException(
            status_code=400, detail=_t("admin.file_gt_storage")
        )


# ---------------------------------------------------------------------------
# Users ↔ subscriptions
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users_with_subscriptions(
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Every account of the auth store with its current plan and usage."""
    today = _today_str()
    month = _month_prefix()
    credits_expr = func.coalesce(UsageLog.credits_used, UsageLog.total_tokens)
    today_usage = dict(
        db.query(UsageLog.user_id, credits_expr).filter(UsageLog.date == today).all()
    )
    month_rows = (
        db.query(UsageLog.user_id, func.sum(credits_expr), func.sum(UsageLog.cost_usd))
        .filter(UsageLog.date.like(f"{month}%"))
        .group_by(UsageLog.user_id)
        .all()
    )
    month_usage = {uid: credits for uid, credits, _ in month_rows}
    month_cost = {uid: float(cost or 0.0) for uid, _, cost in month_rows}
    free = get_free_plan(db)
    results = []
    for u in _auth_users():
        uid = str(u.get("id") or "")
        sub = get_active_subscription(uid, db) if uid else None
        plan = sub.plan if sub and sub.plan else free
        results.append(
            {
                "id": uid,
                "username": u.get("username"),
                "role": u.get("role") or "user",
                "is_active": not u.get("disabled", False),
                "created_at": u.get("created_at") or "",
                "plan_id": plan.id,
                "plan": plan.name,
                "subscription": subscription_to_dict(sub),
                "tokens_today": int(today_usage.get(uid) or 0),
                "tokens_month": int(month_usage.get(uid) or 0),
                "limit_day": plan.max_tokens_per_day or 0,
                "limit_month": plan.max_tokens_per_month or 0,
                "cost_month_usd": round(month_cost.get(uid, 0.0), 4),
            }
        )
    return results


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    body: UpdateUserRoleBody,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Change role in the auth store (source of truth) and mirror it in the DB."""
    from pathmind.services.auth import set_role

    target = _auth_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail=_t("admin.user_not_found"))
    if getattr(admin, "user_id", None) == user_id:
        raise HTTPException(status_code=400, detail=_t("admin.cannot_change_own_role"))
    if not set_role(str(target["username"]), body.role):
        raise HTTPException(status_code=404, detail=_t("admin.user_not_found"))
    ensure_db_user(db, user_id, str(target["username"]), body.role)
    return {"status": "ok", "user_id": user_id, "new_role": body.role}


@router.put("/users/{user_id}/plan")
async def assign_user_plan(
    user_id: str,
    body: AssignPlanBody,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Grant / switch a user's plan without payment (manual grant)."""
    plan = db.get(SubscriptionPlan, body.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=_t("admin.plan_not_found"))
    target = _auth_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail=_t("admin.user_not_found"))

    ensure_db_user(db, user_id, str(target.get("username") or ""), str(target.get("role") or "user"))
    sub = activate_subscription(
        db,
        user_id,
        plan,
        interval=body.interval,
        source="admin",
        reference=f"admin:{getattr(admin, 'username', 'admin')}",
        duration_days=body.duration_days,
    )
    db.commit()
    logger.info(
        "Admin %s assigned plan %s to %s (days=%s)",
        getattr(admin, "username", "?"),
        plan.id,
        user_id,
        body.duration_days,
    )
    return {"status": "ok", "user_id": user_id, "assigned_plan": plan.name, "subscription": subscription_to_dict(sub)}


@router.post("/users/{user_id}/subscription/cancel")
async def cancel_user_subscription(
    user_id: str,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """End the user's paid subscription immediately (they fall back to free)."""
    sub = get_active_subscription(user_id, db)
    if not sub or sub.plan_id == FREE_PLAN_ID:
        raise HTTPException(status_code=400, detail=_t("admin.user_on_free"))
    sub.status = "canceled"
    sub.canceled_at = _utcnow()
    db.commit()
    return {"status": "ok", "user_id": user_id}


@router.get("/users/{user_id}/subscriptions")
async def user_subscription_history(
    user_id: str,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        db.query(Subscription)
        .filter_by(user_id=user_id)
        .order_by(Subscription.current_period_start.desc())
        .limit(50)
        .all()
    )
    return [subscription_to_dict(s) for s in rows]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_system_stats(
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    users = _auth_users()
    plan_breakdown: dict[str, int] = {}
    paid = 0
    for u in users:
        uid = str(u.get("id") or "")
        if (u.get("role") or "user") == "admin":
            plan_breakdown["admin"] = plan_breakdown.get("admin", 0) + 1
            continue
        sub = get_active_subscription(uid, db) if uid else None
        plan_id = sub.plan_id if sub else FREE_PLAN_ID
        plan_breakdown[plan_id] = plan_breakdown.get(plan_id, 0) + 1
        if plan_id != FREE_PLAN_ID:
            paid += 1

    total_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0)).scalar() or 0
    tokens_today = (
        db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))
        .filter(UsageLog.date == _today_str())
        .scalar()
        or 0
    )
    # Storage: latest log per user.
    latest = (
        db.query(UsageLog.user_id, func.max(UsageLog.date).label("d"))
        .group_by(UsageLog.user_id)
        .subquery()
    )
    total_storage = (
        db.query(func.coalesce(func.sum(UsageLog.current_storage_bytes), 0))
        .join(latest, (UsageLog.user_id == latest.c.user_id) & (UsageLog.date == latest.c.d))
        .scalar()
        or 0
    )
    completed = db.query(Payment).filter(Payment.status == "completed")
    total_revenue = completed.with_entities(func.coalesce(func.sum(Payment.amount), 0.0)).scalar()
    month_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_revenue = (
        completed.filter(Payment.paid_at >= month_start)
        .with_entities(func.coalesce(func.sum(Payment.amount), 0.0))
        .scalar()
    )
    pending = db.query(func.count(Payment.id)).filter(Payment.status == "pending").scalar() or 0
    month = _month_prefix()
    cost_month = (
        db.query(func.coalesce(func.sum(UsageLog.cost_usd), 0.0))
        .filter(UsageLog.date.like(f"{month}%"))
        .scalar()
        or 0.0
    )
    cost_today = (
        db.query(func.coalesce(func.sum(UsageLog.cost_usd), 0.0))
        .filter(UsageLog.date == _today_str())
        .scalar()
        or 0.0
    )
    by_model = (
        db.query(
            UsageModelLog.model,
            func.sum(UsageModelLog.calls),
            func.sum(UsageModelLog.prompt_tokens + UsageModelLog.completion_tokens),
            func.sum(UsageModelLog.cost_usd),
        )
        .filter(UsageModelLog.date.like(f"{month}%"))
        .group_by(UsageModelLog.model)
        .all()
    )
    cost_by_model = sorted(
        (
            {
                "model": m,
                "calls": int(calls or 0),
                "tokens": int(tokens or 0),
                "cost_usd": round(float(cost or 0.0), 4),
            }
            for m, calls, tokens, cost in by_model
        ),
        key=lambda r: -r["cost_usd"],
    )

    return {
        "total_users": len(users),
        "active_subscriptions": paid,
        "plan_breakdown": plan_breakdown,
        "total_tokens_consumed": int(total_tokens),
        "tokens_today": int(tokens_today),
        "total_storage_bytes": int(total_storage),
        "total_revenue_usd": round(float(total_revenue or 0), 2),
        "revenue_this_month_usd": round(float(month_revenue or 0), 2),
        "pending_payments": int(pending),
        "api_cost_today_usd": round(float(cost_today), 4),
        "api_cost_month_usd": round(float(cost_month), 4),
        "gross_margin_month_usd": round(float(month_revenue or 0) - float(cost_month), 2),
        "cost_by_model_month": cost_by_model[:20],
    }


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def _plan_with_count(db: Session, plan: SubscriptionPlan) -> dict[str, Any]:
    count = db.query(Subscription).filter_by(plan_id=plan.id, status="active").count()
    max_cost = model_pricing.credits_to_usd(plan.max_tokens_per_month or 0)
    price = plan.price_monthly or 0.0
    return {
        **plan_to_dict(plan),
        "active_subscribers": count,
        # Worst case: a subscriber burns the whole monthly credit quota.
        "max_api_cost_month_usd": round(max_cost, 2),
        "max_cost_ratio": round(max_cost / price, 3) if price > 0 else None,
    }


@router.get("/plans")
async def list_admin_plans(
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    plans = db.query(SubscriptionPlan).all()
    plans.sort(key=lambda p: (p.sort_order or 0, p.price_monthly or 0))
    return [_plan_with_count(db, p) for p in plans]


@router.post("/plans", status_code=201)
async def create_plan(
    body: CreatePlanBody,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan_id = body.id.strip().lower()
    if not _PLAN_ID_RE.match(plan_id):
        raise HTTPException(
            status_code=400,
            detail=_t("admin.plan_id_invalid"),
        )
    if db.get(SubscriptionPlan, plan_id):
        raise HTTPException(status_code=409, detail=_t("admin.plan_id_exists"))
    plan = SubscriptionPlan(
        id=plan_id,
        name=body.name,
        price_monthly=0.0,
        price_yearly=0.0,
        max_tokens_per_day=50_000,
        max_tokens_per_month=1_500_000,
        max_storage_bytes=200 * 1024 * 1024,
        max_upload_file_size_bytes=10 * 1024 * 1024,
        allowed_models=[],
        allow_custom_api_key=False,
        is_active=True,
        sort_order=100,
    )
    _apply_plan_fields(plan, body)
    db.add(plan)
    db.commit()
    logger.info("Admin %s created plan %s", getattr(admin, "username", "?"), plan_id)
    return _plan_with_count(db, plan)


@router.put("/plans/{plan_id}/models")
async def update_plan_models(
    plan_id: str,
    body: UpdatePlanModelsBody,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=_t("admin.plan_not_found"))
    plan.allowed_models = _clean_list(body.allowed_models)
    db.commit()
    return {"status": "ok", "plan_id": plan_id, "allowed_models": plan.allowed_models}


@router.put("/plans/{plan_id}")
async def update_plan_details(
    plan_id: str,
    body: PlanFields,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=_t("admin.plan_not_found"))
    if plan_id == FREE_PLAN_ID:
        if body.is_active is False:
            raise HTTPException(status_code=400, detail=_t("admin.cannot_disable_free"))
        if (body.price_monthly or 0) > 0 or (body.price_yearly or 0) > 0:
            raise HTTPException(status_code=400, detail=_t("admin.free_must_be_zero"))
    _apply_plan_fields(plan, body)
    db.commit()
    logger.info("Admin %s updated plan %s", getattr(admin, "username", "?"), plan_id)
    return _plan_with_count(db, plan)


@router.delete("/plans/{plan_id}")
async def delete_plan(
    plan_id: str,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Delete a plan that was never used; otherwise ask to deactivate it."""
    if plan_id == FREE_PLAN_ID:
        raise HTTPException(status_code=400, detail=_t("admin.cannot_delete_free"))
    plan = db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=_t("admin.plan_not_found"))
    used = (
        db.query(Subscription).filter_by(plan_id=plan_id).count()
        + db.query(Payment).filter_by(plan_id=plan_id).count()
    )
    if used:
        raise HTTPException(
            status_code=409,
            detail=_t("admin.plan_has_history"),
        )
    db.delete(plan)
    db.commit()
    return {"status": "ok", "plan_id": plan_id}


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@router.get("/payments")
async def list_payments(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    expire_stale_payments(db)
    q = db.query(Payment)
    if status:
        q = q.filter(Payment.status == status)
    rows = q.order_by(Payment.created_at.desc()).limit(limit).all()
    names = {str(u.get("id") or ""): u.get("username") for u in _auth_users()}
    return [payment_to_dict(p, names.get(p.user_id)) for p in rows]


def _payment_or_404(db: Session, tx_id: str) -> Payment:
    payment = db.query(Payment).filter_by(transaction_id=tx_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail=_t("billing.payment_not_found"))
    return payment


@router.post("/payments/{tx_id}/confirm")
async def confirm_payment(
    tx_id: str,
    body: PaymentActionBody | None = None,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Admin confirms money was received (e.g. checked the bank statement)."""
    payment = _payment_or_404(db, tx_id)
    if body and body.note:
        payment.note = body.note
    try:
        sub = complete_payment(db, payment, confirmed_by=f"admin:{getattr(admin, 'username', 'admin')}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"payment": payment_to_dict(payment), "subscription": subscription_to_dict(sub)}


@router.post("/payments/{tx_id}/reject")
async def reject_payment(
    tx_id: str,
    body: PaymentActionBody | None = None,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payment = _payment_or_404(db, tx_id)
    if payment.status != "pending":
        raise HTTPException(status_code=409, detail=_t("admin.only_pending_reject"))
    payment.status = "failed"
    payment.note = (body.note if body and body.note else None) or "Rejected by administrator"
    payment.confirmed_by = f"admin:{getattr(admin, 'username', 'admin')}"
    db.commit()
    return {"payment": payment_to_dict(payment)}


@router.post("/payments/{tx_id}/refund")
async def refund_payment(
    tx_id: str,
    body: PaymentActionBody | None = None,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Mark a completed payment refunded and end the subscription it paid for."""
    payment = _payment_or_404(db, tx_id)
    if payment.status != "completed":
        raise HTTPException(status_code=409, detail=_t("admin.only_paid_refund"))
    payment.status = "refunded"
    payment.note = (body.note if body and body.note else None) or "Refunded"
    sub = get_active_subscription(payment.user_id, db)
    if sub and sub.external_subscription_id == payment.transaction_id:
        sub.status = "canceled"
        sub.canceled_at = _utcnow()
    db.commit()
    return {"payment": payment_to_dict(payment)}


# ---------------------------------------------------------------------------
# VNPAY (payment gateway)
# ---------------------------------------------------------------------------


@router.get("/vnpay/status")
async def vnpay_status(admin: Any = Depends(require_admin)) -> dict[str, Any]:
    """Secret-free VNPAY configuration: environment, merchant code, IPN / return URLs."""
    from pathmind.services import vnpay

    return vnpay.status_summary()


# ---------------------------------------------------------------------------
# Model price list (drives credits and cost reporting)
# ---------------------------------------------------------------------------


class ModelPriceBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=40)
    input_per_m: float | None = Field(default=None, ge=0)
    cached_input_per_m: float | None = Field(default=None, ge=0)
    output_per_m: float | None = Field(default=None, ge=0)
    aliases: list[str] | None = None
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=300)


class CreateModelPriceBody(ModelPriceBody):
    id: str = Field(min_length=1, max_length=80)
    input_per_m: float = Field(ge=0)
    output_per_m: float = Field(ge=0)


def _model_price_dict(row: ModelPrice) -> dict[str, Any]:
    price = model_pricing.Price(
        id=row.id,
        display_name=row.display_name or row.id,
        input_per_m=row.input_per_m or 0.0,
        cached_input_per_m=(
            row.cached_input_per_m
            if row.cached_input_per_m is not None
            else (row.input_per_m or 0.0) * 0.25
        ),
        output_per_m=row.output_per_m or 0.0,
    )
    return {
        "id": row.id,
        "display_name": row.display_name or "",
        "provider": row.provider or "",
        "input_per_m": row.input_per_m or 0.0,
        "cached_input_per_m": row.cached_input_per_m,
        "output_per_m": row.output_per_m or 0.0,
        "aliases": row.aliases or [],
        "is_active": row.is_active is not False,
        "notes": row.notes or "",
        "blended_per_m": round(price.blended_per_m, 4),
        "multiplier": round(price.multiplier, 2),
    }


@router.get("/model-prices")
async def list_model_prices(
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.query(ModelPrice).all()
    rows.sort(key=lambda r: (r.id != model_pricing.FALLBACK_PRICE_ID, r.input_per_m or 0))
    default_model = ""
    try:
        from pathmind.services.quota_service import default_llm_model_name

        default_model = default_llm_model_name()
    except Exception:
        pass
    resolved = model_pricing.price_for(default_model, db=db) if default_model else None
    return {
        "credit_base_usd_per_mtok": model_pricing.credit_base_usd_per_m(),
        "default_model": default_model,
        "default_model_price_id": resolved.id if resolved else None,
        "prices": [_model_price_dict(r) for r in rows],
    }


@router.post("/model-prices", status_code=201)
async def create_model_price(
    body: CreateModelPriceBody,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    price_id = body.id.strip() if body.id.strip() == "*" else model_pricing.normalize_model_name(body.id)
    if not price_id:
        raise HTTPException(status_code=400, detail=_t("admin.model_id_invalid"))
    if db.get(ModelPrice, price_id):
        raise HTTPException(status_code=409, detail=_t("admin.model_exists"))
    row = ModelPrice(
        id=price_id,
        display_name=body.display_name or price_id,
        provider=body.provider or "",
        input_per_m=body.input_per_m,
        cached_input_per_m=body.cached_input_per_m,
        output_per_m=body.output_per_m,
        aliases=_clean_list([model_pricing.normalize_model_name(a) for a in body.aliases or []]),
        is_active=True if body.is_active is None else body.is_active,
        notes=body.notes or "",
    )
    db.add(row)
    db.commit()
    model_pricing.invalidate_price_cache()
    return _model_price_dict(row)


@router.put("/model-prices/{price_id}")
async def update_model_price(
    price_id: str,
    body: ModelPriceBody,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(ModelPrice, price_id)
    if not row:
        raise HTTPException(status_code=404, detail=_t("admin.model_not_found"))
    data = body.model_dump(exclude_unset=True)
    if "aliases" in data and data["aliases"] is not None:
        data["aliases"] = _clean_list([model_pricing.normalize_model_name(a) for a in data["aliases"]])
    for key, value in data.items():
        if value is None and key not in {"cached_input_per_m"}:
            continue
        setattr(row, key, value)
    if price_id == model_pricing.FALLBACK_PRICE_ID:
        row.is_active = True
    db.commit()
    model_pricing.invalidate_price_cache()
    return _model_price_dict(row)


@router.delete("/model-prices/{price_id}")
async def delete_model_price(
    price_id: str,
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if price_id == model_pricing.FALLBACK_PRICE_ID:
        raise HTTPException(status_code=400, detail=_t("admin.cannot_delete_default_price"))
    row = db.get(ModelPrice, price_id)
    if not row:
        raise HTTPException(status_code=404, detail=_t("admin.model_not_found"))
    db.delete(row)
    db.commit()
    model_pricing.invalidate_price_cache()
    return {"status": "ok", "id": price_id}


@router.get("/available-models")
async def get_available_models(
    admin: Any = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[str]:
    """Common model ids for quick assignment, plus everything already used by a plan."""
    presets = [
        r.id
        for r in db.query(ModelPrice).order_by(ModelPrice.input_per_m).all()
        if not r.id.startswith("*")
        and "embedding" not in r.id  # embedding prices are not chat models
        and r.is_active is not False
    ]
    for p in db.query(SubscriptionPlan).all():
        for m in p.allowed_models or []:
            if m not in presets:
                presets.append(m)
    return presets
