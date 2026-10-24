"""Signed email invitations (an administrator invites R&D staff). Unused ones can be revoked."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import json

from sqlalchemy.orm import Session

from pathmind.database.models import Invitation
from pathmind.services import audit
from pathmind.services import email_verification as ev

TTL_DAYS = 7
ROLES = {"rnd"}


def _sign(payload: str) -> str:
    return hmac.new(ev._secret(), payload.encode(), hashlib.sha256).hexdigest()


def _token(inv: Invitation) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"i": inv.id, "e": inv.email}).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def _view(inv: Invitation) -> dict:
    return {"id": inv.id, "email": inv.email, "role": inv.role, "status": inv.status,
            "expires_at": inv.expires_at.isoformat() + "Z", "created_at": inv.created_at.isoformat() + "Z" if inv.created_at else None}


def create(db: Session, email: str, base_url: str, actor_id: str, actor_name: str, role: str = "rnd") -> dict:
    if role not in ROLES:
        raise ValueError("Unsupported invitation role.")
    email = email.strip().lower()
    # A fresh invitation replaces any earlier unused one for the same address.
    for old in db.query(Invitation).filter_by(email=email, status="pending").all():
        old.status = "revoked"
    inv = Invitation(email=email, role=role, invited_by=actor_id, expires_at=datetime.utcnow() + timedelta(days=TTL_DAYS))
    db.add(inv)
    db.flush()
    link = f"{base_url.rstrip('/')}/register?invite={_token(inv)}"
    sent = ev._send_mail(
        email,
        "You are invited to join PathMind as R&D",
        f"{actor_name} invited you to join PathMind as an R&D member.\n\n"
        f"Create your account here (valid for {TTL_DAYS} days):\n{link}\n",
    )
    audit.record(db, actor_id, "auth.invite", target_type="invitation", target_id=inv.id,
                 data={"email": email, "delivery": "email" if sent else "link"}, commit=False)
    db.commit()
    return {"invitation": _view(inv), "delivery": "email" if sent else "link", "link": None if sent else link}


def lookup(db: Session, token: str) -> Invitation | None:
    """The pending, unexpired invitation a token points to, else None."""
    try:
        body, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        inv = db.get(Invitation, str(data["i"]))
    except Exception:  # noqa: BLE001 - malformed token
        return None
    if inv is None or inv.status != "pending" or inv.expires_at < datetime.utcnow():
        return None
    return inv


def mark_accepted(db: Session, inv: Invitation, user_id: str) -> None:
    inv.status = "accepted"
    audit.record(db, user_id, "auth.invite.accept", target_type="invitation", target_id=inv.id, commit=False)
    db.commit()


def list_all(db: Session) -> list[dict]:
    return [_view(i) for i in db.query(Invitation).order_by(Invitation.created_at.desc()).limit(100).all()]


def revoke(db: Session, actor_id: str, invitation_id: str) -> dict:
    inv = db.get(Invitation, invitation_id)
    if inv is None:
        raise LookupError("Invitation not found.")
    if inv.status != "pending":
        raise ValueError("Only unused invitations can be revoked.")
    inv.status = "revoked"
    audit.record(db, actor_id, "auth.invite.revoke", target_type="invitation", target_id=inv.id, commit=False)
    db.commit()
    return _view(inv)
