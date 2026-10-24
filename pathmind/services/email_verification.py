"""Email verification codes for self-registered accounts.

A 6-digit code is emailed after sign-up (and on request). Only a salted hash
is stored (``email_verifications`` table); codes expire after
``EMAIL_CODE_TTL_MINUTES`` and allow ``EMAIL_CODE_MAX_ATTEMPTS`` guesses.

Delivery uses SMTP configured through the environment::

    SMTP_HOST, SMTP_PORT (587), SMTP_USERNAME, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USERNAME), SMTP_TLS ("starttls" | "ssl" | "none")

Without ``SMTP_HOST`` nothing is sent: the code is written to the backend log
(for local development) and the account stays usable but marked unverified.
Only when SMTP is configured does login require a verified email — and only
for accounts that registered an email (older accounts are unaffected).
Set ``EMAIL_VERIFICATION_REQUIRED=false`` to never block login.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = int(os.environ.get("EMAIL_CODE_TTL_MINUTES", "30") or 30)
MAX_ATTEMPTS = int(os.environ.get("EMAIL_CODE_MAX_ATTEMPTS", "5") or 5)
RESEND_COOLDOWN_SECONDS = 60


def _smtp(key: str) -> str:
    """SMTP setting: administrator value first, then the environment."""
    try:
        from pathmind.services import system_settings

        return system_settings.get(f"smtp.{key}")
    except Exception:  # pragma: no cover
        return os.environ.get(f"SMTP_{key.upper()}", "").strip()


def smtp_configured() -> bool:
    return bool(_smtp("host"))


def verification_required() -> bool:
    """Whether login is blocked until the registered email is verified."""
    flag = os.environ.get("EMAIL_VERIFICATION_REQUIRED", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return smtp_configured()


def _secret() -> bytes:
    try:
        from pathmind.multi_user.identity import load_or_create_auth_secret

        return load_or_create_auth_secret().encode()
    except Exception:  # pragma: no cover - fallback for odd setups
        return b"pathmind-email-verification"


def _hash(email: str, code: str) -> str:
    return hmac.new(_secret(), f"{email.lower()}:{code}".encode(), hashlib.sha256).hexdigest()


def _send_mail(to: str, subject: str, body: str) -> bool:
    host = _smtp("host")
    if not host:
        return False
    port = int(_smtp("port") or 587)
    user = _smtp("username")
    password = _smtp("password")
    sender = _smtp("from") or user
    mode = (_smtp("tls") or "starttls").lower()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)
    try:
        if mode == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, context=ssl.create_default_context(), timeout=20
            )
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if mode == "starttls":
                server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:
        logger.warning("Sending verification email to %s failed: %s", to, exc)
        return False


def issue_code(user_id: str, email: str) -> dict:
    """Create a fresh code for ``email`` (invalidating older ones) and send it.

    Returns ``{"sent": bool, "delivery": "email" | "log", "retry_after": int}``.
    ``retry_after`` > 0 means the previous code is too recent; nothing was sent.
    """
    from pathmind.database.connection import get_db_session
    from pathmind.database.models import EmailVerification

    email = email.strip().lower()
    now = datetime.utcnow()
    with get_db_session() as db:
        latest = (
            db.query(EmailVerification)
            .filter_by(email=email, used_at=None)
            .order_by(EmailVerification.created_at.desc())
            .first()
        )
        if latest and latest.created_at:
            elapsed = (now - latest.created_at).total_seconds()
            if elapsed < RESEND_COOLDOWN_SECONDS:
                return {"sent": False, "delivery": "none", "retry_after": int(RESEND_COOLDOWN_SECONDS - elapsed) + 1}
        for old in db.query(EmailVerification).filter_by(email=email, used_at=None).all():
            old.used_at = now  # superseded
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(
            EmailVerification(
                user_id=user_id,
                email=email,
                code_hash=_hash(email, code),
                attempts=0,
                expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
                created_at=now,
            )
        )
        db.commit()

    subject = "PathMind — your verification code"
    body = (
        f"Your PathMind verification code is: {code}\n\n"
        f"It expires in {CODE_TTL_MINUTES} minutes. If you did not create a PathMind "
        "account, you can ignore this email.\n"
    )
    if _send_mail(email, subject, body):
        return {"sent": True, "delivery": "email", "retry_after": 0}
    # No SMTP (or delivery failed): surface the code to the operator only.
    logger.warning("Email verification code for %s: %s (SMTP not configured or failed)", email, code)
    return {"sent": False, "delivery": "log", "retry_after": 0}


def check_code(email: str, code: str) -> str:
    """Validate a code. Returns "ok", "invalid", "expired" or "too_many_attempts"."""
    from pathmind.database.connection import get_db_session
    from pathmind.database.models import EmailVerification

    email = email.strip().lower()
    code = (code or "").strip()
    now = datetime.utcnow()
    with get_db_session() as db:
        row = (
            db.query(EmailVerification)
            .filter_by(email=email, used_at=None)
            .order_by(EmailVerification.created_at.desc())
            .first()
        )
        if row is None:
            return "invalid"
        if row.expires_at and row.expires_at < now:
            return "expired"
        if (row.attempts or 0) >= MAX_ATTEMPTS:
            return "too_many_attempts"
        if not (code.isdigit() and len(code) == 6) or not hmac.compare_digest(
            row.code_hash, _hash(email, code)
        ):
            row.attempts = (row.attempts or 0) + 1
            db.commit()
            return "too_many_attempts" if row.attempts >= MAX_ATTEMPTS else "invalid"
        row.used_at = now
        db.commit()
        return "ok"


def mark_verified(username: str, user_id: str) -> None:
    """Record verification in the identity store and its DB mirror."""
    from pathmind.multi_user.identity import update_profile

    stamp = datetime.utcnow()
    update_profile(username, email_verified=True, email_verified_at=stamp.isoformat() + "Z")
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.database.models import User

        with get_db_session() as db:
            row = db.get(User, user_id)
            if row is not None:
                row.email_verified = True
                row.email_verified_at = stamp
                db.commit()
    except Exception as exc:  # mirror only
        logger.debug("Email verified mirror update failed: %s", exc)


__all__ = [
    "check_code",
    "issue_code",
    "mark_verified",
    "smtp_configured",
    "verification_required",
]
