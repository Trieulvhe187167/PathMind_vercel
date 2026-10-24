"""VNPAY payment gateway (API 2.1.0): build the signed payment URL and verify what VNPAY sends back.

On VNPAY's page people pay with VNPAY-QR (most banking apps), domestic ATM cards or Visa/Master/JCB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from urllib.parse import quote_plus, urlencode

URLS = {
    "sandbox": "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html",
    "production": "https://pay.vnpay.vn/vpcpay.html",
}
VN_TZ = timezone(timedelta(hours=7))


def _query(params: dict[str, str]) -> str:
    return "&".join(f"{k}={quote_plus(str(v))}" for k, v in sorted(params.items()))


def sign(params: dict[str, str], secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), _query(params).encode("utf-8"), hashlib.sha512).hexdigest()


def payment_url(*, tmn_code: str, secret: str, env: str, txn_ref: str, amount_vnd: int, order_info: str,
                return_url: str, ip: str, now: datetime | None = None, locale: str = "vn") -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(VN_TZ)
    params = {
        "vnp_Version": "2.1.0", "vnp_Command": "pay", "vnp_TmnCode": tmn_code,
        "vnp_Amount": str(int(amount_vnd) * 100), "vnp_CurrCode": "VND", "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": order_info[:255], "vnp_OrderType": "other", "vnp_Locale": locale,
        "vnp_ReturnUrl": return_url, "vnp_IpAddr": ip or "127.0.0.1",
        "vnp_CreateDate": now.strftime("%Y%m%d%H%M%S"),
        "vnp_ExpireDate": (now + timedelta(minutes=30)).strftime("%Y%m%d%H%M%S"),
    }
    return f"{URLS.get(env, URLS['sandbox'])}?{_query(params)}&{urlencode({'vnp_SecureHash': sign(params, secret)})}"


def verify(params: dict[str, str], secret: str) -> bool:
    data = {k: v for k, v in params.items() if k.startswith("vnp_") and k not in ("vnp_SecureHash", "vnp_SecureHashType")}
    given = params.get("vnp_SecureHash", "")
    return bool(given) and hmac.compare_digest(sign(data, secret).lower(), given.lower())


def succeeded(params: dict[str, str]) -> bool:
    return params.get("vnp_ResponseCode") == "00" and params.get("vnp_TransactionStatus", "00") == "00"
