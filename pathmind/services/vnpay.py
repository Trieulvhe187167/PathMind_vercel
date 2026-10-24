"""VNPAY payment gateway (v2.1.0) — QR, domestic ATM cards and international cards.

Flow
----
1. ``POST /api/billing/checkout {gateway: "vnpay"}`` creates our pending payment
   and returns a signed VNPAY payment URL (``vnp_TxnRef`` = our code ``DT…``,
   amount in VND). The browser goes there; the customer picks VNPAY-QR (any
   banking app), a domestic ATM card or Visa/Mastercard/JCB.
2. VNPAY calls our **IPN** ``GET /api/billing/vnpay/ipn?vnp_…`` (server to
   server, retried by VNPAY until we answer ``00``/``02``) and redirects the
   browser back to ``/pricing?vnp_…``; the page forwards those signed
   parameters to ``POST /api/billing/payments/vnpay-return``.
3. Either path verifies ``vnp_SecureHash`` (HMAC-SHA512 with our hash secret),
   checks the amount and activates the plan once (idempotent).

The user and plan always come from OUR payment row, never from the callback.

Environment
-----------
``VNPAY_TMN_CODE``      merchant code (``vnp_TmnCode``) — sandbox codes come from
                        https://sandbox.vnpayment.vn/devreg/
``VNPAY_HASH_SECRET``   secret for ``vnp_SecureHash``.
``VNPAY_ENV``           ``sandbox`` (default) or ``production``.
``VNPAY_PAYMENT_URL``   override the payment page URL.
``PATHMIND_PUBLIC_URL`` public site URL (return page; default http://localhost:3782).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import unicodedata
from typing import Any
from urllib.parse import quote_plus

SANDBOX_PAYMENT_URL = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
PRODUCTION_PAYMENT_URL = "https://pay.vnpay.vn/vpcpay.html"
IPN_PATH = "/api/billing/vnpay/ipn"
RETURN_PATH = "/pricing"
PAYMENT_EXPIRE_MINUTES = 30
VN_TZ = timezone(timedelta(hours=7))  # VNPAY dates are GMT+7


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def environment() -> str:
    return "production" if _env("VNPAY_ENV").lower() in {"prod", "production", "live"} else "sandbox"


def is_sandbox() -> bool:
    return environment() == "sandbox"


def payment_url() -> str:
    return _env("VNPAY_PAYMENT_URL") or (SANDBOX_PAYMENT_URL if is_sandbox() else PRODUCTION_PAYMENT_URL)


def configured() -> bool:
    return bool(_env("VNPAY_TMN_CODE") and _env("VNPAY_HASH_SECRET"))


def public_url() -> str:
    return (_env("PATHMIND_PUBLIC_URL") or "http://localhost:3782").rstrip("/")


def status_summary() -> dict[str, Any]:
    """Secret-free configuration summary for the admin page."""
    code = _env("VNPAY_TMN_CODE")
    return {
        "configured": configured(),
        "environment": environment(),
        "payment_url": payment_url(),
        "tmn_code": code,
        "ipn_url": f"{public_url()}{IPN_PATH}",
        "return_url": f"{public_url()}{RETURN_PATH}",
    }


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def _hash_data(params: dict[str, Any]) -> str:
    """``key=quote_plus(value)`` joined by ``&``, keys sorted (VNPAY v2.1.0)."""
    return "&".join(
        f"{key}={quote_plus(str(value))}"
        for key, value in sorted(params.items())
        if value is not None  # VNPAY's samples hash empty values too
    )


def sign(params: dict[str, Any], secret: str | None = None) -> str:
    return hmac.new(
        (secret if secret is not None else _env("VNPAY_HASH_SECRET")).encode("utf-8"),
        _hash_data(params).encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()


def verify(query: dict[str, Any]) -> bool:
    """Check ``vnp_SecureHash`` of a return / IPN query string."""
    if not configured():
        return False
    received = str(query.get("vnp_SecureHash") or "")
    fields = {
        key: value
        for key, value in query.items()
        if key.startswith("vnp_") and key not in {"vnp_SecureHash", "vnp_SecureHashType"}
    }
    if not received or not fields:
        return False
    return hmac.compare_digest(sign(fields).lower(), received.lower())


# ---------------------------------------------------------------------------
# Payment URL
# ---------------------------------------------------------------------------


def _ascii(text: str) -> str:
    """Order info must be unaccented, without special characters."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z ]", " ", text)).strip()[:255]


def build_payment_url(
    *,
    transaction_id: str,
    amount_vnd: int,
    order_info: str,
    client_ip: str,
    locale: str = "vn",
    bank_code: str | None = None,
    now: datetime | None = None,
) -> str:
    """Signed VNPAY payment page URL for one pending payment."""
    created = (now or datetime.now(VN_TZ)).astimezone(VN_TZ)
    params: dict[str, Any] = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": _env("VNPAY_TMN_CODE"),
        "vnp_Amount": int(amount_vnd) * 100,
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": transaction_id,
        "vnp_OrderInfo": _ascii(order_info) or transaction_id,
        "vnp_OrderType": "other",
        "vnp_Locale": "en" if locale == "en" else "vn",
        "vnp_ReturnUrl": f"{public_url()}{RETURN_PATH}",
        "vnp_IpAddr": (client_ip or "127.0.0.1")[:45],
        "vnp_CreateDate": created.strftime("%Y%m%d%H%M%S"),
        "vnp_ExpireDate": (created + timedelta(minutes=PAYMENT_EXPIRE_MINUTES)).strftime("%Y%m%d%H%M%S"),
    }
    if bank_code:
        params["vnp_BankCode"] = bank_code
    return f"{payment_url()}?{_hash_data(params)}&vnp_SecureHash={sign(params)}"


def is_success(query: dict[str, Any]) -> bool:
    return str(query.get("vnp_ResponseCode")) == "00" and str(query.get("vnp_TransactionStatus")) == "00"


def amount_vnd(query: dict[str, Any]) -> int:
    try:
        return int(str(query.get("vnp_Amount") or "0")) // 100
    except ValueError:
        return -1


__all__ = [
    "IPN_PATH",
    "amount_vnd",
    "build_payment_url",
    "configured",
    "environment",
    "is_sandbox",
    "is_success",
    "payment_url",
    "public_url",
    "sign",
    "status_summary",
    "verify",
]
