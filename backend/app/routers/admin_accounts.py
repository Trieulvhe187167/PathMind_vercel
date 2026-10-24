"""Administrators: create accounts directly, reset passwords, and invite people by link."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import ROLES, User, UserInvitation, utcnow
from ..security import hash_password
from ..services import accounts, audit, invitations, mailer
from ..services.app_settings import get_setting
from .auth import _client_ip

router = APIRouter()


def _role(role: str) -> str:
    if role not in ROLES:
        raise accounts.error(422, "role", f"Role must be one of: {', '.join(ROLES)}.")
    return role


def _still_admin(db: Session, admin: User) -> None:
    db.refresh(admin)  # may have been demoted while waiting for the lock
    if admin.role != "admin" or not admin.is_active:
        raise HTTPException(status_code=403, detail="Administrators only.")


class CreateUserBody(BaseModel):
    username: str
    email: EmailStr | None = None
    full_name: str = Field(default="", max_length=120)
    role: str = "learner"
    # Leave empty to have one generated. Either way the person must replace it at first sign-in.
    password: str = ""


@router.post("/users", status_code=201)
def create_user(body: CreateUserBody, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    username = accounts.clean_username(body.username)
    email = str(body.email).strip().lower() if body.email else None
    role = _role(body.role)
    password = body.password or accounts.temporary_password()
    if body.password:
        accounts.check_password(password, username=username, email=email or "")
    password_hash = hash_password(password)
    with request.app.state.accounts_lock:
        _still_admin(db, admin)
        accounts.ensure_available(db, username, email)
        user = User(username=username, email=email, full_name=body.full_name.strip(), role=role,
                    password_hash=password_hash, must_change_password=True, created_by=admin.id)
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise accounts.error(409, "username", "That username or email is already registered.") from None
        audit.record(db, admin.id, "user.create", target_type="user", target_id=user.id,
                     data={"role": role}, ip=_client_ip(request))
        db.commit()
    # The password is returned this once and never stored in plain text.
    return {"user": user.public(), "temporary_password": password}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    password = accounts.temporary_password()
    password_hash = hash_password(password)
    with request.app.state.accounts_lock:
        _still_admin(db, admin)
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="Change your own password on the Account page.")
        user.password_hash = password_hash
        user.must_change_password = True
        user.token_version += 1  # signs the person out everywhere
        audit.record(db, admin.id, "user.password_reset", target_type="user", target_id=user.id,
                     ip=_client_ip(request))
        db.commit()
    return {"user": user.public(), "temporary_password": password}


class InviteBody(BaseModel):
    email: EmailStr
    role: str = "learner"
    days: int = Field(default=7, ge=1, le=30)
    link_base: str = Field(default="", max_length=300)  # the site address the admin is using, to build the link


@router.get("/invitations")
def list_invitations(
    status: Literal["open", "accepted", "revoked", "expired", "all"] = "open",
    db: Session = Depends(get_db), _: User = Depends(require_admin),
):
    now = utcnow()
    I = UserInvitation
    pending = (I.accepted_at.is_(None), I.revoked_at.is_(None))
    where = {
        "open": (*pending, I.expires_at > now),
        "expired": (*pending, I.expires_at <= now),
        "accepted": (I.accepted_at.is_not(None),),
        "revoked": (I.accepted_at.is_(None), I.revoked_at.is_not(None)),
        "all": (),
    }[status]
    rows = db.scalars(select(I).where(*where).order_by(I.created_at.desc()).limit(500)).all()
    return {"invitations": [invitations.public(r, db) for r in rows]}


@router.post("/invitations", status_code=201)
def create_invitation(body: InviteBody, request: Request, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    email = str(body.email).strip().lower()
    role = _role(body.role)
    with request.app.state.accounts_lock:
        _still_admin(db, admin)
        if db.scalar(select(User.id).where(User.email == email)):
            raise accounts.error(409, "email", "That email already has an account.")
        if invitations.open_for_email(db, email):
            raise accounts.error(409, "email", "An invitation to this email is still open. Revoke it first.")
        token, token_hash = invitations.new_token()
        inv = UserInvitation(email=email, role=role, token_hash=token_hash, invited_by=admin.id,
                             expires_at=invitations.expiry(body.days))
        db.add(inv)
        db.flush()
        audit.record(db, admin.id, "invitation.create", target_type="invitation", target_id=inv.id,
                     data={"email": email, "role": role, "days": body.days}, ip=_client_ip(request))
        db.commit()
    emailed = False
    base = body.link_base.strip().rstrip("/")
    if base.startswith(("http://", "https://")) and mailer.configured(db):
        site = get_setting(db, "general").get("site_name") or "PathMind"
        try:
            emailed = mailer.send(db, email, f"You are invited to {site}",
                                  f"You have been invited to join {site} as {role}.\n\nCreate your account here "
                                  f"(the link works for {body.days} day(s)):\n{base}/invite/{token}\n")
        except mailer.MailError:
            emailed = False
    # The token is only returned here; the page turns it into a link.
    return {"invitation": invitations.public(inv, db), "token": token, "emailed": emailed}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: str, request: Request, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    with request.app.state.accounts_lock:
        _still_admin(db, admin)
        inv = db.get(UserInvitation, invitation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="Invitation not found.")
        if invitations.status_of(inv) != "open":
            raise HTTPException(status_code=409, detail="Only open invitations can be revoked.")
        inv.revoked_at = utcnow()
        audit.record(db, admin.id, "invitation.revoke", target_type="invitation", target_id=inv.id,
                     data={"email": inv.email}, ip=_client_ip(request))
        db.commit()
    return {"invitation": invitations.public(inv, db)}
