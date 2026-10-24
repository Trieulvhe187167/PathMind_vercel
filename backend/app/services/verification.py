"""Six-digit e-mail codes: confirm an address, reset a forgotten password.

Only an HMAC of the code is stored. Codes expire after 30 minutes, allow 5 wrong tries and can be
re-sent once a minute.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User, VerificationCode, utcnow
from . import mailer
from .app_settings import get_setting

TTL = timedelta(minutes=30)
RESEND = timedelta(seconds=60)
MAX_ATTEMPTS = 5
_secret = b""


def configure(secret_key: str) -> None:
    global _secret
    _secret = hashlib.sha256(("pathmind-codes:" + secret_key).encode()).digest()


def _hash(purpose: str, email: str, code: str) -> str:
    return hmac.new(_secret, f"{purpose}:{email.lower()}:{code}".encode(), hashlib.sha256).hexdigest()


def issue(db: Session, user: User, purpose: str, email: str | None = None) -> bool:
    """Create and e-mail a code. Returns False if one was sent less than a minute ago."""
    email = (email or user.email or "").lower()
    if not email:
        return False
    last = db.scalar(select(VerificationCode).where(
        VerificationCode.user_id == user.id, VerificationCode.purpose == purpose,
        VerificationCode.used_at.is_(None)).order_by(VerificationCode.created_at.desc()))
    now = utcnow()
    if last is not None and now - last.created_at < RESEND:
        return False
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(VerificationCode(user_id=user.id, purpose=purpose, email=email, code_hash=_hash(purpose, email, code),
                            expires_at=now + TTL))
    db.commit()
    site = get_setting(db, "general").get("site_name") or "PathMind"
    if purpose == "verify_email":
        subject, text = f"{site}: confirm your e-mail", f"Your confirmation code is {code}. It is valid for 30 minutes."
    else:
        subject = f"{site}: reset your password"
        text = (f"Your password reset code is {code}. It is valid for 30 minutes.\n"
                "If you did not ask for this, ignore this e-mail; your password stays the same.")
    try:
        mailer.send(db, email, subject, text)
    except mailer.MailError:
        pass  # logged by the mailer; the code still works if an admin reads it from the log
    return True


def check(db: Session, user: User, purpose: str, code: str) -> None:
    """Consume a valid code or raise 400."""
    row = db.scalar(select(VerificationCode).where(
        VerificationCode.user_id == user.id, VerificationCode.purpose == purpose,
        VerificationCode.used_at.is_(None)).order_by(VerificationCode.created_at.desc()))
    bad = HTTPException(status_code=400, detail={"field": "code", "message": "The code is wrong or has expired."})
    if row is None or row.expires_at <= utcnow() or row.attempts >= MAX_ATTEMPTS:
        raise bad
    if not hmac.compare_digest(row.code_hash, _hash(purpose, row.email, (code or "").strip())):
        row.attempts += 1
        db.commit()
        raise bad
    row.used_at = utcnow()
