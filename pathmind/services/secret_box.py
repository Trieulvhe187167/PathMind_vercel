"""Encryption at rest for provider credentials (API keys, tokens, headers).

Secrets in ``model_catalog.json`` (and the settings draft) are stored as
``enc:v1:<Fernet token>`` instead of plain text, so a copied settings file or
backup does not expose them. Everything that reads the catalog through
``ModelCatalogService`` still sees plain values in memory.

Key material, in order of preference:

1. ``PATHMIND_SECRETS_KEY`` environment variable (recommended in production:
   keep it out of the data directory and out of backups). Any string works;
   it is stretched with SHA-256 into a Fernet key.
2. Otherwise a random key generated once at ``data/system/secrets.key``
   (owner-only). That still protects copies of the settings file, but not a
   copy of the whole ``data/`` directory.

Changing or losing the key makes stored secrets unreadable (logged); they are
kept encrypted on disk untouched, so putting the right key back recovers them —
otherwise an administrator re-enters them in Settings.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"
_KEY_ENV = "PATHMIND_SECRETS_KEY"
_lock = threading.Lock()
_fernet = None


def _key_file():
    from pathmind.multi_user.paths import SYSTEM_ROOT

    return SYSTEM_ROOT / "secrets.key"


def _load_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        from cryptography.fernet import Fernet

        material = os.environ.get(_KEY_ENV, "").strip()
        if not material:
            path = _key_file()
            if path.exists():
                material = path.read_text(encoding="utf-8").strip()
            if not material:
                from pathmind.utils.secret_files import write_secret_text

                material = Fernet.generate_key().decode()
                write_secret_text(path, material)
                logger.warning(
                    "Generated an encryption key for stored API keys at %s. "
                    "For production set %s instead and keep it out of backups.",
                    path,
                    _KEY_ENV,
                )
        key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
        _fernet = Fernet(key)
        return _fernet


def reset_cache() -> None:
    """Forget the loaded key (tests / key rotation)."""
    global _fernet
    with _lock:
        _fernet = None


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_value(value: str) -> str:
    if not value or is_encrypted(value):
        return value
    token = _load_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_value(value: str) -> str:
    if not is_encrypted(value):
        return value
    try:
        return _load_fernet().decrypt(value[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except Exception:
        # Keep the ciphertext rather than an empty string: a later save then
        # writes it back unchanged, so restoring the right key recovers every
        # stored secret instead of a misconfigured restart wiping them.
        logger.error(
            "A stored API key could not be decrypted (wrong or missing %s?). "
            "Restore the original key or re-enter the API key in Settings.",
            _KEY_ENV,
        )
        return value


def transform(value: Any, fn) -> Any:
    """Apply *fn* to every string inside a secret value (str / dict / list)."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {key: transform(item, fn) for key, item in value.items()}
    if isinstance(value, list):
        return [transform(item, fn) for item in value]
    return value


def contains_plaintext(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value) and not is_encrypted(value)
    if isinstance(value, dict):
        return any(contains_plaintext(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_plaintext(item) for item in value)
    return False


__all__ = [
    "contains_plaintext",
    "decrypt_value",
    "encrypt_value",
    "is_encrypted",
    "reset_cache",
    "transform",
]
