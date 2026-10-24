"""Encrypt secrets at rest (API keys, storage keys, SMTP passwords) with the standard library only.

Format: ``enc:v1:<base64url(nonce16 | ciphertext | tag32)>``.
The keystream is HMAC-SHA256(enc_key, nonce | counter) blocks (a PRF in counter mode) and the tag is
HMAC-SHA256(mac_key, nonce | ciphertext), checked before decrypting (encrypt-then-MAC).

The key comes from ``PATHMIND_SECRETS_KEY`` when set, otherwise it is derived from the app's secret
key. Losing or changing it makes stored secrets unreadable (they must be entered again), so back it up.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

PREFIX = "enc:v1:"
_master: bytes | None = None


class SecretError(ValueError):
    pass


def configure(secret_key: str) -> None:
    """Called once at start-up with the app secret key."""
    global _master
    raw = os.getenv("PATHMIND_SECRETS_KEY", "").strip() or secret_key
    _master = hashlib.sha256(("pathmind-secretbox:" + raw).encode("utf-8")).digest()


def _keys() -> tuple[bytes, bytes]:
    if _master is None:
        raise SecretError("Secret box is not configured.")
    enc = hmac.new(_master, b"enc", hashlib.sha256).digest()
    mac = hmac.new(_master, b"mac", hashlib.sha256).digest()
    return enc, mac


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    enc, mac = _keys()
    nonce = secrets.token_bytes(16)
    data = plain.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(enc, nonce, len(data))))
    tag = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    return PREFIX + base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    if not token.startswith(PREFIX):
        raise SecretError("Not an encrypted value.")
    enc, mac = _keys()
    try:
        raw = base64.urlsafe_b64decode(token[len(PREFIX):].encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise SecretError("Corrupt secret.") from exc
    if len(raw) < 48:
        raise SecretError("Corrupt secret.")
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + ct, hashlib.sha256).digest()):
        raise SecretError("Secret cannot be decrypted (was the key changed?).")
    return bytes(a ^ b for a, b in zip(ct, _keystream(enc, nonce, len(ct)))).decode("utf-8")


def try_decrypt(token: str) -> str:
    """Decrypt, or return "" when the value is unreadable (wrong key) - callers treat it as unset."""
    try:
        return decrypt(token)
    except SecretError:
        return ""


def mask(token: str) -> str:
    """What the UI shows for a stored secret: "" when none, else a fixed placeholder."""
    return "••••••••" if token else ""
