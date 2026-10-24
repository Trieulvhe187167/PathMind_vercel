"""Deployment-wide settings stored in the ``app_settings`` table, with defaults in code.

Each key holds a JSON object; reads merge it over the defaults so new options appear without a
migration. Fields whose name ends in ``_enc`` hold encrypted secrets: callers send the plain value
under the name without the suffix (``password`` for ``password_enc``) and reads never return it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import AppSetting
from . import secretbox

DEFAULTS: dict[str, dict[str, Any]] = {
    "general": {
        "site_name": "PathMind",
        # The address people use to reach the web app, e.g. https://learn.example.com - used in
        # links (invitations, password reset e-mails) and as the Google sign-in redirect.
        "public_url": "",
        "default_locale": "vi",
        "support_email": "",
    },
    "registration": {
        "allow_signup": True,  # anyone may create an account from the sign-up page
        # People may choose "educator" when they sign up...
        "allow_educator_signup": True,
        # ...and then an admin must approve it (until then they are a learner).
        "educator_requires_approval": True,
        # With SMTP configured, new accounts must confirm their e-mail before signing in.
        "require_email_verification": False,
    },
    "smtp": {
        "host": "", "port": 587, "username": "", "password_enc": "", "from_address": "",
        "use_tls": True, "use_ssl": False,
    },
    "google_oauth": {"enabled": False, "client_id": "", "client_secret_enc": "", "allow_signup": True},
    "billing": {
        "demo_mode": False,  # shows a "simulate payment" button - only for demos
        "usd_vnd_rate": 25400,
        "credit_usd_per_mtok": 0.25,  # 1 credit ~ 1 token of a $0.25/1M model
        "pending_payment_hours": 24,
    },
    "vnpay": {"tmn_code": "", "hash_secret_enc": "", "env": "sandbox"},
    "rewards": {
        "points_integrated": 50,  # contribution approved and integrated into the tree
        "points_cited": 1,  # the AI cited a document the educator contributed
        "cited_daily_cap": 20,
        "points_gap_resolved": 30,
        "redeem_enabled": True,
        "points_per_plan_day": 20,  # points for one day of the redeem plan
        "redeem_plan_id": "plus",
        "points_per_1k_credits": 5,
    },
    "ai": {
        "temperature": 0.4,
        "max_context_chunks": 6,
        "memory_enabled": True,
        "min_retrieval_score": 0.35,  # below this, a question counts as not answerable from the tree
    },
    "knowledge": {
        "review_interval_days": 365,
        "retire_when_overdue": False,
    },
}


def _plain_name(k: str) -> str:
    return k[: -len("_enc")]


def get_setting(db: Session, key: str) -> dict[str, Any]:
    """Full value including encrypted fields (server side only)."""
    row = db.get(AppSetting, key)
    stored = row.value if row and isinstance(row.value, dict) else {}
    return {**DEFAULTS.get(key, {}), **stored}


def secret(db: Session, key: str, field: str) -> str:
    """Decrypted secret, e.g. ``secret(db, "smtp", "password")``."""
    return secretbox.try_decrypt(get_setting(db, key).get(field + "_enc", ""))


def public_setting(db: Session, key: str) -> dict[str, Any]:
    """What the admin UI may see: secrets replaced by ``<name>_set: bool``."""
    value = get_setting(db, key)
    out: dict[str, Any] = {}
    for k, v in value.items():
        if k.endswith("_enc"):
            out[_plain_name(k) + "_set"] = bool(v)
        else:
            out[k] = v
    return out


def set_setting(db: Session, key: str, changes: dict[str, Any], *, updated_by: str | None) -> dict[str, Any]:
    """Merge ``changes`` (only keys known in DEFAULTS) into the stored value. Caller commits.

    For a secret field ``x_enc`` pass ``x``: a non-empty string replaces it, "" clears it,
    ``None`` / absent keeps it.
    """
    allowed = DEFAULTS.get(key, {})
    clean: dict[str, Any] = {}
    for k, default in allowed.items():
        if k.endswith("_enc"):
            plain = changes.get(_plain_name(k))
            if isinstance(plain, str):
                clean[k] = secretbox.encrypt(plain) if plain else ""
            continue
        if k in changes and changes[k] is not None:
            v = changes[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int) and not isinstance(default, bool):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    continue
            elif isinstance(default, float):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
            elif isinstance(default, str):
                v = str(v).strip()[:1000]
            clean[k] = v
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value={})
        db.add(row)
    row.value = {**(row.value or {}), **clean}
    row.updated_by = updated_by
    return {**allowed, **row.value}
