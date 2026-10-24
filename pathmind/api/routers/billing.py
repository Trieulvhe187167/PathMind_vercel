"""Billing, subscription and payment API for end users.

Flow
----
1. ``POST /checkout``   → creates a *pending* ``Payment`` (nothing is activated).
2. The user pays on VNPAY's page (VNPAY-QR from any banking app, domestic ATM
   card, or Visa/Mastercard/JCB) — see ``pathmind.services.vnpay``.
3. The payment becomes ``completed`` through ONE of:
   * VNPAY IPN (``GET /vnpay/ipn``) or the signed return page
     (``POST /payments/vnpay-return``);
   * ``POST /webhook``  — signed (HMAC-SHA256) callback from the gateway /
     bank-sync service (PayOS, Casso, SePay, …);
   * ``POST /api/admin/payments/{tx}/confirm`` — admin confirms manually;
   * ``POST /payments/{tx}/simulate`` — only when ``BILLING_DEMO_MODE=true``.
4. ``complete_payment`` activates / renews the subscription.

Environment
-----------
``BILLING_DEMO_MODE``        "true" enables the simulate endpoint & demo gateways.
``BILLING_WEBHOOK_SECRET``   shared secret for ``X-Billing-Signature``; unset = webhook off.
``BILLING_USD_VND_RATE``     USD → VND rate for the amount charged by VNPAY (default 25400).
``VNPAY_*``                  see ``pathmind.services.vnpay``.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.services import vnpay
from pathmind.services.i18n import t as _t
from pathmind.api.routers.auth import require_auth
from pathmind.database.connection import get_db
from pathmind.database.models import Payment, SubscriptionPlan
from pathmind.services.quota_service import (
    FREE_PLAN_ID,
    _utcnow,
    activate_subscription,
    complete_payment,
    ensure_db_user,
    get_active_subscription,
    get_user_usage_summary,
    payment_to_dict,
    plan_to_dict,
    subscription_to_dict,
    usd_to_vnd_rate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PAYMENT_TTL_HOURS = 24
Gateway = Literal["vnpay"]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def demo_mode() -> bool:
    return _env_bool("BILLING_DEMO_MODE")


def _gateways() -> list[dict[str, Any]]:
    """One gateway: VNPAY (QR, domestic ATM cards, international cards)."""
    return [
        {
            "id": "vnpay",
            "label": _t("billing.gateway_vnpay"),
            "enabled": vnpay.configured() or demo_mode(),
            "demo_only": not vnpay.configured(),
            "sandbox": vnpay.is_sandbox() if vnpay.configured() else None,
        },
    ]


def _current_identity(current: Any) -> tuple[str, str | None, str | None]:
    """(user_id, username, role) of the caller; local admin when auth is off."""
    if current is None:
        return "local-admin", "local", "admin"
    user_id = str(getattr(current, "user_id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail=_t("billing.user_unknown"))
    return user_id, getattr(current, "username", None), getattr(current, "role", None)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    plan_id: str
    interval: Literal["monthly", "yearly"] = "monthly"
    gateway: Gateway = "vnpay"


class SwitchPlanRequest(BaseModel):
    plan_id: str


class WebhookPayload(BaseModel):
    transaction_id: str
    status: Literal["completed", "failed"] = "completed"
    amount: float | None = Field(default=None, description="Amount actually received")
    currency: Literal["USD", "VND"] = "USD"


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


@router.get("/config")
async def billing_config() -> dict[str, Any]:
    """Which payment methods are usable, and whether demo mode is on."""
    return {
        "demo_mode": demo_mode(),
        "usd_vnd_rate": usd_to_vnd_rate(),
        "gateways": _gateways(),
        "bank": None,
    }


@router.get("/model-rates")
async def model_rates(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Credit multiplier of each priced model (public, for the pricing page)."""
    from pathmind.database.models import ModelPrice
    from pathmind.services import model_pricing

    rates = []
    for row in db.query(ModelPrice).filter(ModelPrice.id != model_pricing.FALLBACK_PRICE_ID).all():
        if row.is_active is False:
            continue
        price = model_pricing.price_for(row.id, db=db)
        rates.append(
            {"id": row.id, "name": row.display_name or row.id, "multiplier": round(price.multiplier, 2)}
        )
    rates.sort(key=lambda r: r["multiplier"])
    return {
        "credit_base_usd_per_mtok": model_pricing.credit_base_usd_per_m(),
        "rates": rates,
    }


@router.get("/plans")
async def list_plans(
    lang: str | None = None, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """List all active plans, cheapest / lowest sort_order first.

    Public endpoint (no user scope), so the page passes its UI language for the
    generated feature lines.
    """
    plans = db.query(SubscriptionPlan).filter_by(is_active=True).all()
    plans.sort(key=lambda p: (p.sort_order or 0, p.price_monthly or 0))
    return [plan_to_dict(p, lang) for p in plans]


# ---------------------------------------------------------------------------
# Authenticated user
# ---------------------------------------------------------------------------


@router.get("/summary")
async def get_billing_summary(
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Current plan, subscription and usage of the caller."""
    user_id, username, role = _current_identity(current)
    ensure_db_user(db, user_id, username, role)
    return get_user_usage_summary(user_id, db, role=role)


@router.get("/entitlements")
async def get_entitlements(current: Any = Depends(require_auth)) -> dict[str, Any]:
    """What the caller's plan unlocks, their usage and which plan to upgrade to."""
    from pathmind.services import entitlements

    return entitlements.summary()


@router.post("/checkout")
async def create_checkout_session(
    payload: CheckoutRequest,
    request: Request,
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a *pending* payment. Nothing is activated until it is confirmed."""
    user_id, username, role = _current_identity(current)
    plan = db.query(SubscriptionPlan).filter_by(id=payload.plan_id, is_active=True).first()
    if not plan:
        raise HTTPException(status_code=404, detail=_t("billing.plan_unavailable"))

    if payload.interval == "yearly":
        amount = plan.price_yearly if (plan.price_yearly or 0) > 0 else (plan.price_monthly or 0) * 12
    else:
        amount = plan.price_monthly or 0
    amount = round(float(amount), 2)
    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail=_t("billing.free_no_checkout"),
        )

    gateway = next((g for g in _gateways() if g["id"] == payload.gateway), None)
    if not gateway or not gateway["enabled"]:
        raise HTTPException(
            status_code=400,
            detail=_t("billing.gateway_not_configured"),
        )

    ensure_db_user(db, user_id, username, role)

    # One open checkout per user: older pending ones are superseded.
    for old in db.query(Payment).filter_by(user_id=user_id, status="pending").all():
        old.status = "canceled"
        old.note = "Replaced by a newer payment"

    tx_id = f"DT{uuid.uuid4().hex[:10].upper()}"
    amount_vnd = int(round(amount * usd_to_vnd_rate()))
    payment = Payment(
        user_id=user_id,
        plan_id=plan.id,
        amount=amount,
        currency="USD",
        amount_vnd=amount_vnd,
        billing_interval=payload.interval,
        payment_method=payload.gateway,
        transaction_id=tx_id,
        status="pending",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    result: dict[str, Any] = {
        **payment_to_dict(payment, username),
        "gateway": payload.gateway,
        "demo_mode": demo_mode(),
        "expires_at": (payment.created_at + timedelta(hours=PAYMENT_TTL_HOURS)).isoformat() + "Z",
        "transfer_content": tx_id,
        "qr_image_url": None,
        "bank": None,
        "message": "",
    }
    if payload.gateway == "vnpay" and vnpay.configured():
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        client_ip = forwarded or (request.client.host if request.client else "") or "127.0.0.1"
        result["checkout_url"] = vnpay.build_payment_url(
            transaction_id=tx_id,
            amount_vnd=amount_vnd,
            order_info=f"Thanh toan goi {plan.name or plan.id} ma {tx_id}",
            client_ip=client_ip,
        )
        result["message"] = _t("billing.checkout_msg_vnpay")
    else:
        result["message"] = _t("billing.checkout_msg_demo")
    return result


@router.get("/payments")
async def list_my_payments(
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    user_id, username, _ = _current_identity(current)
    rows = (
        db.query(Payment)
        .filter_by(user_id=user_id)
        .order_by(Payment.created_at.desc())
        .limit(50)
        .all()
    )
    return [payment_to_dict(p, username) for p in rows]


def _own_payment(db: Session, tx_id: str, user_id: str) -> Payment:
    payment = db.query(Payment).filter_by(transaction_id=tx_id).first()
    if not payment or payment.user_id != user_id:
        raise HTTPException(status_code=404, detail=_t("billing.payment_not_found"))
    return payment


@router.get("/payments/{tx_id}")
async def get_my_payment(
    tx_id: str,
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Poll a checkout's status."""
    user_id, username, _ = _current_identity(current)
    payment = _own_payment(db, tx_id, user_id)
    if (
        payment.status == "pending"
        and payment.created_at
        and payment.created_at + timedelta(hours=PAYMENT_TTL_HOURS) < _utcnow()
    ):
        payment.status = "expired"
        db.commit()
    return payment_to_dict(payment, username)


@router.post("/payments/{tx_id}/cancel")
async def cancel_my_payment(
    tx_id: str,
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user_id, username, _ = _current_identity(current)
    payment = _own_payment(db, tx_id, user_id)
    if payment.status != "pending":
        raise HTTPException(status_code=409, detail=_t("billing.only_pending_cancel"))
    payment.status = "canceled"
    payment.note = "Canceled by user"
    db.commit()
    return payment_to_dict(payment, username)


@router.post("/payments/{tx_id}/simulate")
async def simulate_payment(
    tx_id: str,
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """DEMO ONLY: pretend the gateway confirmed this payment."""
    if not demo_mode():
        raise HTTPException(status_code=403, detail=_t("billing.demo_disabled"))
    user_id, username, _ = _current_identity(current)
    payment = _own_payment(db, tx_id, user_id)
    if payment.status != "pending":
        raise HTTPException(status_code=409, detail=_t("billing.payment_in_status", status=payment.status))
    sub = complete_payment(db, payment, confirmed_by="demo")
    return {"payment": payment_to_dict(payment, username), "subscription": subscription_to_dict(sub)}


@router.post("/cancel")
async def cancel_subscription(
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Stop auto-renewal: the paid plan stays until the end of the period."""
    user_id, _, _ = _current_identity(current)
    sub = get_active_subscription(user_id, db)
    if not sub or sub.plan_id == FREE_PLAN_ID:
        raise HTTPException(status_code=400, detail=_t("billing.already_free"))
    if sub.current_period_end is None:
        raise HTTPException(
            status_code=400,
            detail=_t("billing.admin_grant_cancel"),
        )
    sub.cancel_at_period_end = True
    db.commit()
    return {"status": "ok", "subscription": subscription_to_dict(sub)}


@router.post("/resume")
async def resume_subscription(
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user_id, _, _ = _current_identity(current)
    sub = get_active_subscription(user_id, db)
    if not sub or not sub.cancel_at_period_end:
        raise HTTPException(status_code=400, detail=_t("billing.nothing_to_resume"))
    sub.cancel_at_period_end = False
    db.commit()
    return {"status": "ok", "subscription": subscription_to_dict(sub)}


@router.post("/upgrade")
async def switch_plan(
    payload: SwitchPlanRequest,
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Switch to a FREE plan. Paid plans must go through ``/checkout``.

    (Kept under the old path for backward compatibility. It used to activate
    any plan without payment, which let every user get Pro for free.)
    """
    user_id, username, role = _current_identity(current)
    plan = db.query(SubscriptionPlan).filter_by(id=payload.plan_id, is_active=True).first()
    if not plan:
        raise HTTPException(status_code=404, detail=_t("billing.plan_unavailable"))
    if (plan.price_monthly or 0) > 0 or (plan.price_yearly or 0) > 0:
        raise HTTPException(status_code=402, detail=_t("billing.paid_needs_checkout"))

    ensure_db_user(db, user_id, username, role)
    current_sub = get_active_subscription(user_id, db)
    if current_sub and current_sub.plan_id == plan.id:
        return {
            "status": "ok",
            "plan_id": plan.id,
            "plan_name": plan.name,
            "message": _t("billing.already_on_plan", plan=plan.name),
            "subscription": subscription_to_dict(current_sub),
        }
    if current_sub and current_sub.current_period_end is not None:
        # Downgrade from a paid plan: keep what was paid for, stop renewal.
        current_sub.cancel_at_period_end = True
        db.commit()
        return {
            "status": "scheduled",
            "plan_id": plan.id,
            "plan_name": plan.name,
            "message": _t("billing.downgrade_scheduled"),
            "subscription": subscription_to_dict(current_sub),
        }
    sub = activate_subscription(db, user_id, plan, source="user")
    db.commit()
    return {
        "status": "ok",
        "plan_id": plan.id,
        "plan_name": plan.name,
        "message": _t("billing.switched_plan", plan=plan.name),
        "subscription": subscription_to_dict(sub),
    }


# ---------------------------------------------------------------------------
# Gateway webhook (signed)
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Payment confirmation from a gateway / bank-sync service.

    Header ``X-Billing-Signature: <hex hmac_sha256(BILLING_WEBHOOK_SECRET, raw_body)>``.
    The user and plan are taken from OUR pending payment, never from the body.
    """
    secret = os.environ.get("BILLING_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail=_t("billing.webhook_not_configured"))
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    signature = request.headers.get("x-billing-signature", "")
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = WebhookPayload(**json.loads(raw or b"{}"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}") from exc

    payment = db.query(Payment).filter_by(transaction_id=payload.transaction_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Unknown transaction")
    if payment.status == "completed":
        return {"status": "ok", "transaction_id": payment.transaction_id, "idempotent": True}

    if payload.status == "failed":
        payment.status = "failed"
        payment.note = "Gateway reported failure"
        db.commit()
        return {"status": "ok", "transaction_id": payment.transaction_id}

    if payload.amount is not None:
        expected_amount = payment.amount_vnd if payload.currency == "VND" else payment.amount
        if expected_amount is not None and payload.amount + 1e-6 < float(expected_amount):
            payment.status = "failed"
            payment.note = f"Amount received ({payload.amount} {payload.currency}) is below the required amount"
            db.commit()
            raise HTTPException(status_code=400, detail="Amount mismatch")

    try:
        complete_payment(db, payment, confirmed_by="webhook")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "transaction_id": payment.transaction_id}


# ---------------------------------------------------------------------------
# VNPAY (IPN = server-to-server, return = browser coming back to /pricing)
# ---------------------------------------------------------------------------


def apply_vnpay_result(db: Session, query: dict[str, Any], *, via: str) -> tuple[str, str]:
    """Apply one signed VNPAY result. Returns VNPAY's ``(RspCode, Message)``.

    ``00`` = updated, ``02`` = already confirmed (both end VNPAY's retries),
    ``01`` = order not found, ``04`` = invalid amount, ``97`` = bad signature.
    Idempotent: a repeated IPN or a return after the IPN changes nothing.
    """
    if not vnpay.verify(query):
        return "97", "Invalid signature"
    tx_id = str(query.get("vnp_TxnRef") or "")
    payment = db.query(Payment).filter_by(transaction_id=tx_id).first() if tx_id else None
    if payment is None:
        return "01", "Order not found"
    if vnpay.amount_vnd(query) != int(payment.amount_vnd or 0):
        return "04", "Invalid amount"
    if payment.status == "completed":
        return "02", "Order already confirmed"

    bank_tx = str(query.get("vnp_TransactionNo") or "")
    if not vnpay.is_success(query):
        if payment.status == "pending":
            payment.status = "failed"
            payment.note = (
                f"VNPAY: ResponseCode {query.get('vnp_ResponseCode')}, "
                f"TransactionStatus {query.get('vnp_TransactionStatus')}"
            )[:500]
            db.commit()
        return "00", "Confirm Success"

    if payment.status in {"canceled", "expired", "failed"}:
        payment.status = "pending"  # the money was captured: honour it
    payment.gateway_reference = f"vnpay:{bank_tx or tx_id}"[:255]
    if query.get("vnp_BankCode"):
        payment.note = f"VNPAY {query.get('vnp_BankCode')} {query.get('vnp_CardType') or ''}".strip()[:500]
    try:
        complete_payment(db, payment, confirmed_by=via)
    except ValueError:
        db.rollback()
        logger.exception("VNPAY payment %s could not be completed", tx_id)
        return "99", "Unknown error"
    return "00", "Confirm Success"


@router.get("/vnpay/ipn")
async def vnpay_ipn(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """VNPAY's server calls this (register the URL with VNPAY); always 200 + RspCode."""
    query = dict(request.query_params)
    try:
        code, message = apply_vnpay_result(db, query, via="vnpay")
    except Exception:
        logger.exception("VNPAY IPN failed")
        code, message = "99", "Unknown error"
    logger.info("VNPAY IPN %s → %s %s", query.get("vnp_TxnRef"), code, message)
    return {"RspCode": code, "Message": message}


class VnpayReturnBody(BaseModel):
    params: dict[str, str]


@router.post("/payments/vnpay-return")
async def vnpay_return(
    body: VnpayReturnBody,
    current: Any = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The /pricing page forwards the signed ``vnp_…`` parameters VNPAY redirected with.

    Works even when VNPAY cannot reach the IPN (e.g. local testing). The
    signature proves the result came from VNPAY; the payment must belong to
    the caller.
    """
    user_id, username, _ = _current_identity(current)
    payment = _own_payment(db, str(body.params.get("vnp_TxnRef") or ""), user_id)
    code, message = apply_vnpay_result(db, body.params, via="vnpay")
    if code == "97":
        raise HTTPException(status_code=400, detail="Invalid VNPAY signature")
    db.refresh(payment)
    return {
        **payment_to_dict(payment, username),
        "vnpay_code": code,
        "vnpay_response_code": body.params.get("vnp_ResponseCode"),
    }
