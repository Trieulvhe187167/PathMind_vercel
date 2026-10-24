"""Forgot-password flow: a signed, expiring, single-use link sent by email."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from pathmind.services import email_verification as ev

TTL_SECONDS = 3600


def _sig(body: str) -> str:
    return hmac.new(ev._secret(), body.encode(), hashlib.sha256).hexdigest()


def _stamp(password_hash: str) -> str:
    # Ties the link to the current password, so using it once (or any other change) kills it.
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def make_token(username: str, password_hash: str) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps({"u": username, "x": int(time.time()) + TTL_SECONDS, "h": _stamp(password_hash)}).encode()
    ).decode().rstrip("=")
    return f"{body}.{_sig(body)}"


def read_token(token: str) -> tuple[str, str] | None:
    """(username, stamp) for a well-formed, unexpired token, else None."""
    try:
        body, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(sig, _sig(body)):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if int(data["x"]) < time.time():
            return None
        return str(data["u"]), str(data["h"])
    except Exception:  # noqa: BLE001
        return None


def still_valid(stamp: str, password_hash: str) -> bool:
    return hmac.compare_digest(stamp, _stamp(password_hash))
