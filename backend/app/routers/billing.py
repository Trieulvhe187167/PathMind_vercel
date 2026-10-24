"""Plans and payments: what learners see (/api/billing) and what admins manage (/api/admin/billing)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db, require_admin
from ..models import LLMCall, Payment, Plan, Subscription, UsageByModel, User, utcnow
from ..services import audit, billing, vnpay
from ..services.app_settings import get_setting, secret
from ..services.notify import notify
from .auth import _client_ip

router = APIRouter()  # /api/billing
admin_router = APIRouter()  # /api/admin/billing


def payment_public(p: Payment, db: Session | None = None) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    out = {"id": p.id, "transaction_id": p.transaction_id, "plan_id": p.plan_id, "gateway": p.gateway,
           "amount_usd": float(p.amount_usd), "amount_vnd": p.amount_vnd, "billing_interval": p.billing_interval,
           "status": p.status, "note": p.note, "created_at": iso(p.created_at), "paid_at": iso(p.paid_at)}
    if db is not None:
        u = db.get(User, p.user_id)
        out["user"] = {"id": u.id, "username": u.username, "email": u.email} if u else None
    return out


def _gateways(db: Session) -> dict:
    cfg = get_setting(db, "vnpay")
    return {"vnpay": bool(cfg.get("tmn_code") and cfg.get("hash_secret_enc")),
            "demo": bool(get_setting(db, "billing").get("demo_mode"))}


@router.get("/config")
def config(db: Session = Depends(get_db)):
    b = get_setting(db, "billing")
    return {"gateways": _gateways(db), "usd_vnd_rate": b.get("usd_vnd_rate"),
            "vnpay_env": get_setting(db, "vnpay").get("env")}


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    rows = db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)).all()
    return {"plans": [billing.plan_public(p) for p in rows]}


@router.get("/summary")
def my_summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    out = billing.summary(db, user)
    db.commit()  # lazily expired subscriptions
    return out


@router.get("/entitlements")
def my_entitlements(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"entitlements": billing.entitlements(db, user), "unlimited": billing.is_unlimited(user)}


class CheckoutBody(BaseModel):
    plan_id: str
    interval: Literal["monthly", "yearly"] = "monthly"
    gateway: Literal["vnpay", "demo"] = "vnpay"


def _new_txn() -> str:
    return "PM" + utcnow().strftime("%y%m%d") + secrets.token_hex(4).upper()


@router.post("/checkout", status_code=201)
def checkout(body: CheckoutBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if billing.is_unlimited(user):
        raise HTTPException(status_code=409, detail="R&D and admin accounts are not limited and need no plan.")
    plan = db.get(Plan, body.plan_id)
    if plan is None or not plan.is_active:
        raise HTTPException(status_code=404, detail="Plan not found.")
    price = plan.price_yearly_usd if body.interval == "yearly" else plan.price_monthly_usd
    if not price or Decimal(price) <= 0:
        raise HTTPException(status_code=422, detail="This plan is free; nothing to pay.")
    gw = _gateways(db)
    if body.gateway == "vnpay" and not gw["vnpay"]:
        raise HTTPException(status_code=409, detail="Online payment is not set up yet. Contact an administrator.")
    if body.gateway == "demo" and not gw["demo"]:
        raise HTTPException(status_code=409, detail="Demo payments are turned off.")
    rate = int(get_setting(db, "billing").get("usd_vnd_rate") or 25400)
    p = Payment(user_id=user.id, plan_id=plan.id, transaction_id=_new_txn(), gateway=body.gateway,
                amount_usd=Decimal(price), amount_vnd=int((Decimal(price) * rate).to_integral_value()),
                billing_interval=body.interval, status="pending")
    db.add(p)
    db.flush()
    pay_url = None
    if body.gateway == "vnpay":
        cfg = get_setting(db, "vnpay")
        base = get_setting(db, "general").get("public_url") or str(request.base_url).rstrip("/")
        pay_url = vnpay.payment_url(
            tmn_code=cfg["tmn_code"], secret=secret(db, "vnpay", "hash_secret"), env=cfg.get("env", "sandbox"),
            txn_ref=p.transaction_id, amount_vnd=p.amount_vnd or 0,
            order_info=f"PathMind {plan.name} {body.interval} {p.transaction_id}",
            return_url=f"{base}/billing?return=vnpay", ip=_client_ip(request),
            locale="vn" if user.locale == "vi" else "en",
        )
    db.commit()
    return {"payment": payment_public(p), "pay_url": pay_url}


@router.get("/payments")
def my_payments(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(100)).all()
    return {"payments": [payment_public(p) for p in rows]}


def _own_payment(db: Session, user: User, txn: str) -> Payment:
    p = db.scalar(select(Payment).where(Payment.transaction_id == txn))
    if p is None or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found.")
    return p


def complete_payment(db: Session, p: Payment, *, reference: str = "", confirmed_by: str | None = None) -> bool:
    """Mark paid and activate the plan. Idempotent: returns False when it was already done."""
    if p.status == "completed":
        return False
    if p.status not in ("pending", "failed", "expired", "canceled"):
        raise HTTPException(status_code=409, detail=f"A {p.status} payment cannot be completed.")
    sub = billing.activate(db, p.user_id, p.plan_id, interval=p.billing_interval, source="payment")
    p.status, p.paid_at, p.subscription_id = "completed", utcnow(), sub.id
    p.gateway_reference = reference or p.gateway_reference
    p.confirmed_by = confirmed_by
    plan = db.get(Plan, p.plan_id)
    notify(db, p.user_id, "payment_completed", f"Payment received: {plan.name if plan else p.plan_id} is active.",
           target_type="payment", target_id=p.id)
    return True


@router.post("/payments/{txn}/cancel")
def cancel_payment(txn: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _own_payment(db, user, txn)
    if p.status != "pending":
        raise HTTPException(status_code=409, detail="Only a pending payment can be canceled.")
    p.status = "canceled"
    db.commit()
    return {"payment": payment_public(p)}


@router.post("/payments/{txn}/simulate")
def simulate(txn: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not get_setting(db, "billing").get("demo_mode"):
        raise HTTPException(status_code=404, detail="Not available.")
    p = _own_payment(db, user, txn)
    complete_payment(db, p, reference="demo")
    db.commit()
    return {"payment": payment_public(p), "summary": billing.summary(db, user)}


def _vnpay_apply(db: Session, params: dict[str, str]) -> tuple[str, Payment | None]:
    """Shared by the return page and the IPN. Returns a VNPAY response code."""
    if not vnpay.verify(params, secret(db, "vnpay", "hash_secret")):
        return "97", None
    p = db.scalar(select(Payment).where(Payment.transaction_id == params.get("vnp_TxnRef", "")))
    if p is None:
        return "01", None
    try:
        amount = int(params.get("vnp_Amount", "0")) // 100
    except ValueError:
        amount = -1
    if amount != (p.amount_vnd or 0):
        return "04", p
    if p.status == "completed":
        return "02", p
    if vnpay.succeeded(params):
        complete_payment(db, p, reference="vnpay:" + params.get("vnp_TransactionNo", ""))
    else:
        p.status = "failed"
        p.note = f"VNPAY code {params.get('vnp_ResponseCode', '')}"
    return "00", p


class ReturnBody(BaseModel):
    params: dict[str, str]


@router.post("/vnpay-return")
def vnpay_return(body: ReturnBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.scalar(select(Payment).where(Payment.transaction_id == body.params.get("vnp_TxnRef", "")))
    if p is None or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found.")
    code, p = _vnpay_apply(db, body.params)
    db.commit()
    if code == "97":
        raise HTTPException(status_code=400, detail="The payment result has an invalid signature.")
    if code == "04":
        raise HTTPException(status_code=400, detail="The paid amount does not match.")
    return {"payment": payment_public(p) if p else None}


@router.get("/vnpay/ipn")
def vnpay_ipn(request: Request, db: Session = Depends(get_db)):
    params = {k: v for k, v in request.query_params.items()}
    code, _ = _vnpay_apply(db, params)
    db.commit()
    messages = {"00": "Confirm Success", "01": "Order not found", "02": "Order already confirmed",
                "04": "Invalid amount", "97": "Invalid signature"}
    return {"RspCode": code, "Message": messages.get(code, "Unknown error")}


@router.post("/cancel")
def cancel_renewal(db: Session = Depends(get_db), user: User = Depends(current_user)):
    sub = billing.active_subscription(db, user.id)
    if sub is None or sub.period_end is None:
        raise HTTPException(status_code=409, detail="There is no paid plan to cancel.")
    sub.cancel_at_period_end, sub.status, sub.canceled_at = True, "canceled", utcnow()
    db.commit()
    return billing.summary(db, user)


@router.post("/resume")
def resume_renewal(db: Session = Depends(get_db), user: User = Depends(current_user)):
    sub = billing.active_subscription(db, user.id)
    if sub is None or not sub.cancel_at_period_end:
        raise HTTPException(status_code=409, detail="Nothing to resume.")
    sub.cancel_at_period_end, sub.status, sub.canceled_at = False, "active", None
    db.commit()
    return billing.summary(db, user)


# ── Admin ─────────────────────────────────────────────────────────────────────


class PlanBody(BaseModel):
    id: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    price_monthly_usd: float = Field(ge=0)
    price_yearly_usd: float = Field(ge=0)
    credits_per_day: int | None = Field(default=None, ge=0)
    credits_per_month: int | None = Field(default=None, ge=0)
    storage_bytes: int | None = Field(default=None, ge=0)
    max_upload_bytes: int | None = Field(default=None, ge=0)
    allowed_models: list[str] = []
    default_model: str = Field(default="", max_length=120)
    entitlements: dict = {}
    features: list[str] = []
    sort_order: int = 0
    is_active: bool = True


@admin_router.get("/plans")
def admin_plans(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(Plan).order_by(Plan.sort_order)).all()
    counts = dict(db.execute(select(Subscription.plan_id, func.count()).where(
        Subscription.status.in_(("active", "canceled"))).group_by(Subscription.plan_id)).all())
    return {"plans": [{**billing.plan_public(p), "subscribers": counts.get(p.id, 0)} for p in rows]}


@admin_router.put("/plans")
def save_plan(body: PlanBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    p = db.get(Plan, body.id)
    if p is None:
        p = Plan(id=body.id)
        db.add(p)
    if body.default_model and body.allowed_models and body.default_model not in body.allowed_models:
        raise HTTPException(status_code=422, detail="The default model must be one of the allowed models.")
    ent = {}
    for k, v in (body.entitlements or {}).items():
        if k in billing.FEATURE_KEYS:
            ent[k] = bool(v)
        elif k in billing.COUNT_KEYS or k in billing.MAX_KEYS:
            ent[k] = None if v is None or v == "" else max(0, int(v))
        elif k == "allowance_period" and v in ("month", "lifetime"):
            ent[k] = v
    p.name, p.description = body.name.strip(), body.description.strip()
    p.price_monthly_usd, p.price_yearly_usd = Decimal(str(body.price_monthly_usd)), Decimal(str(body.price_yearly_usd))
    p.credits_per_day, p.credits_per_month = body.credits_per_day, body.credits_per_month
    p.storage_bytes, p.max_upload_bytes = body.storage_bytes, body.max_upload_bytes
    p.allowed_models, p.default_model = [m.strip() for m in body.allowed_models if m.strip()], body.default_model.strip()
    p.entitlements, p.features = ent, [f.strip() for f in body.features if f.strip()][:20]
    p.sort_order, p.is_active = body.sort_order, body.is_active
    audit.record(db, admin.id, "plan.save", target_type="plan", target_id=p.id, ip=_client_ip(request))
    db.commit()
    return {"plan": billing.plan_public(p)}


@admin_router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    p = db.get(Plan, plan_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if plan_id == "free":
        raise HTTPException(status_code=409, detail="The free plan cannot be deleted.")
    used = db.scalar(select(Subscription.id).where(Subscription.plan_id == plan_id).limit(1)) or \
        db.scalar(select(Payment.id).where(Payment.plan_id == plan_id).limit(1))
    if used:
        raise HTTPException(status_code=409, detail="This plan has been used; stop selling it (inactive) instead.")
    db.delete(p)
    db.commit()
    return {"ok": True}


class GrantBody(BaseModel):
    plan_id: str
    days: int = Field(default=30, ge=0, le=3650)  # 0 = never expires
    note: str = Field(default="", max_length=300)


@admin_router.post("/users/{user_id}/plan")
def grant_plan(user_id: str, body: GrantBody, request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.role in billing.UNLIMITED_ROLES:
        raise HTTPException(status_code=409, detail="R&D and admin accounts are not limited and need no plan.")
    if db.get(Plan, body.plan_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    sub = billing.activate(db, user.id, body.plan_id, days=body.days, source="admin")
    audit.record(db, admin.id, "plan.grant", target_type="user", target_id=user.id,
                 data={"plan": body.plan_id, "days": body.days, "note": body.note}, ip=_client_ip(request))
    notify(db, user.id, "plan_granted", f"An administrator gave you the {body.plan_id} plan.", actor_id=admin.id,
           target_type="subscription", target_id=sub.id)
    db.commit()
    return {"subscription": billing.sub_public(sub), "summary": billing.summary(db, user)}


@admin_router.post("/users/{user_id}/plan/cancel")
def revoke_plan(user_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    sub = billing.active_subscription(db, user_id)
    if sub is None:
        raise HTTPException(status_code=409, detail="This user has no paid plan.")
    sub.status, sub.canceled_at, sub.period_end = "expired", utcnow(), utcnow()
    audit.record(db, admin.id, "plan.revoke", target_type="user", target_id=user_id, data={"plan": sub.plan_id},
                 ip=_client_ip(request))
    db.commit()
    return {"ok": True}


@admin_router.get("/users/{user_id}")
def user_billing(user_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    subs = db.scalars(select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.created_at.desc()).limit(50)).all()
    out = billing.summary(db, user)
    db.commit()
    return {**out, "history": [billing.sub_public(s) for s in subs]}


@admin_router.get("/payments")
def admin_payments(status: str = "", db: Session = Depends(get_db), _: User = Depends(require_admin)):
    stmt = select(Payment).order_by(Payment.created_at.desc()).limit(300)
    if status:
        stmt = stmt.where(Payment.status == status)
    return {"payments": [payment_public(p, db) for p in db.scalars(stmt)]}


class PaymentAction(BaseModel):
    note: str = Field(default="", max_length=500)


def _payment(db: Session, txn: str) -> Payment:
    p = db.scalar(select(Payment).where(Payment.transaction_id == txn))
    if p is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    return p


@admin_router.post("/payments/{txn}/confirm")
def admin_confirm(txn: str, body: PaymentAction, request: Request, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)):
    p = _payment(db, txn)
    complete_payment(db, p, reference="admin", confirmed_by=admin.id)
    p.note = body.note or p.note
    audit.record(db, admin.id, "payment.confirm", target_type="payment", target_id=p.id, ip=_client_ip(request))
    db.commit()
    return {"payment": payment_public(p, db)}


@admin_router.post("/payments/{txn}/reject")
def admin_reject(txn: str, body: PaymentAction, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    p = _payment(db, txn)
    if p.status != "pending":
        raise HTTPException(status_code=409, detail="Only a pending payment can be rejected.")
    p.status, p.note = "failed", body.note or "Rejected by an administrator"
    audit.record(db, admin.id, "payment.reject", target_type="payment", target_id=p.id, ip=_client_ip(request))
    db.commit()
    return {"payment": payment_public(p, db)}


@admin_router.post("/payments/{txn}/refund")
def admin_refund(txn: str, body: PaymentAction, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    p = _payment(db, txn)
    if p.status != "completed":
        raise HTTPException(status_code=409, detail="Only a completed payment can be refunded.")
    p.status, p.note = "refunded", body.note or "Refunded"
    if p.subscription_id:
        sub = db.get(Subscription, p.subscription_id)
        if sub is not None and sub.status in ("active", "canceled"):
            sub.status, sub.period_end = "expired", utcnow()
    notify(db, p.user_id, "payment_refunded", "Your payment was refunded and the plan ended.", actor_id=admin.id,
           target_type="payment", target_id=p.id)
    audit.record(db, admin.id, "payment.refund", target_type="payment", target_id=p.id, ip=_client_ip(request))
    db.commit()
    return {"payment": payment_public(p, db)}


@admin_router.get("/report")
def report(days: int = 30, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    days = max(1, min(days, 366))
    since = utcnow() - timedelta(days=days)
    revenue = db.scalar(select(func.coalesce(func.sum(Payment.amount_usd), 0)).where(
        Payment.status == "completed", Payment.paid_at >= since)) or 0
    refunds = db.scalar(select(func.coalesce(func.sum(Payment.amount_usd), 0)).where(
        Payment.status == "refunded", Payment.paid_at >= since)) or 0
    cost = db.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(LLMCall.created_at >= since)) or 0
    by_model = db.execute(
        select(UsageByModel.model, func.sum(UsageByModel.calls), func.sum(UsageByModel.prompt_tokens),
               func.sum(UsageByModel.completion_tokens), func.sum(UsageByModel.cost_usd), func.sum(UsageByModel.credits_used))
        .where(UsageByModel.day >= since.date()).group_by(UsageByModel.model)
        .order_by(func.sum(UsageByModel.cost_usd).desc())
    ).all()
    by_plan = db.execute(
        select(Payment.plan_id, func.count(), func.sum(Payment.amount_usd))
        .where(Payment.status == "completed", Payment.paid_at >= since).group_by(Payment.plan_id)
    ).all()
    daily = db.execute(
        select(func.date(LLMCall.created_at), func.sum(LLMCall.cost_usd), func.count())
        .where(LLMCall.created_at >= since).group_by(func.date(LLMCall.created_at)).order_by(func.date(LLMCall.created_at))
    ).all()
    errors = db.scalar(select(func.count()).select_from(LLMCall).where(LLMCall.created_at >= since, LLMCall.status == "error")) or 0
    return {
        "days": days, "revenue_usd": float(revenue), "refunds_usd": float(refunds), "ai_cost_usd": float(cost),
        "gross_margin_usd": float(revenue) - float(cost), "ai_errors": errors,
        "by_model": [{"model": m, "calls": int(c or 0), "prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0),
                      "cost_usd": float(cu or 0), "credits": int(cr or 0)} for m, c, pt, ct, cu, cr in by_model],
        "by_plan": [{"plan_id": pid, "payments": int(n), "revenue_usd": float(a or 0)} for pid, n, a in by_plan],
        "daily_cost": [{"day": str(d), "cost_usd": float(c or 0), "calls": int(n)} for d, c, n in daily],
    }

