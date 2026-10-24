"""Password hashing and signed session tokens, standard library only.

Passwords: scrypt (n=2**14, r=8, p=1) with a 16-byte random salt, stored as
``scrypt$n$r$p$salt_b64$hash_b64``.

Tokens: ``base64url(json payload) . base64url(HMAC-SHA256)``. The payload holds
the user id (``sub``), the user's ``token_version`` (``ver``) and an expiry
(``exp``, unix seconds). The server re-checks the user on every request, so a
deactivated user or a changed password invalidates old tokens immediately.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_N, _R, _P = 2**14, 8, 1


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:  # accounts that only sign in with Google have no password
        return False
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = _unb64(digest)
        got = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def create_token(secret: str, user_id: str, version: int, ttl_seconds: int, sid: str | None = None) -> str:
    payload = {"sub": user_id, "ver": version, "exp": int(time.time()) + ttl_seconds}
    if sid:
        payload["sid"] = sid
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def decode_token(secret: str, token: str) -> dict | None:
    """Return the payload of a valid, unexpired token, else ``None``."""
    try:
        body, sig = token.split(".")
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(sig), expected):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    if not isinstance(payload.get("sub"), str) or not isinstance(payload.get("ver"), int):
        return None
    return payload
