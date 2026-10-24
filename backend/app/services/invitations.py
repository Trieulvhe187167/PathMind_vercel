"""Invitations: an admin invites an email address with a preset role; the link sets up the account.

Only a SHA-256 of the token is stored, so the link is shown to the admin once, when it is made.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User, UserInvitation, utcnow


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def status_of(inv: UserInvitation, now: datetime | None = None) -> str:
    if inv.accepted_at:
        return "accepted"
    if inv.revoked_at:
        return "revoked"
    if inv.expires_at <= (now or utcnow()):
        return "expired"
    return "open"


def open_for_email(db: Session, email: str) -> UserInvitation | None:
    now = utcnow()
    return db.scalar(
        select(UserInvitation).where(
            UserInvitation.email == email, UserInvitation.accepted_at.is_(None),
            UserInvitation.revoked_at.is_(None), UserInvitation.expires_at > now,
        )
    )


def find_open(db: Session, token: str) -> UserInvitation | None:
    if not token or len(token) > 200:
        return None
    inv = db.scalar(select(UserInvitation).where(UserInvitation.token_hash == hash_token(token)))
    return inv if inv is not None and status_of(inv) == "open" else None


def expiry(days: int) -> datetime:
    return utcnow() + timedelta(days=days)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def public(inv: UserInvitation, db: Session | None = None) -> dict:
    out = {
        "id": inv.id, "email": inv.email, "role": inv.role, "status": status_of(inv),
        "created_at": _iso(inv.created_at), "expires_at": _iso(inv.expires_at),
        "accepted_at": _iso(inv.accepted_at), "revoked_at": _iso(inv.revoked_at),
    }
    if db is not None:
        inviter = db.get(User, inv.invited_by) if inv.invited_by else None
        joined = db.get(User, inv.accepted_user_id) if inv.accepted_user_id else None
        out["invited_by"] = inviter.username if inviter else None
        out["accepted_username"] = joined.username if joined else None
    return out
