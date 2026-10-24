"""Accounts and roles, sign-in (password + Google), invitations, verification codes, audit trail."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk

# learner: studies. educator: a learner who can also contribute documents for the knowledge tree.
# rnd: curates the company knowledge tree (reviews contributions, edits, publishes).
# admin: runs the system and accounts; may READ the knowledge tree but never change it.
ROLES = ("learner", "educator", "rnd", "admin")
# Who may hand out which role (admin creates or promotes rnd and other admins).
ROLE_GRANTORS = {"learner": {"admin"}, "educator": {"admin"}, "rnd": {"admin"}, "admin": {"admin"}}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    locale: Mapped[str] = mapped_column(String(10), default="vi")  # vi | en | zh
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Ho_Chi_Minh")
    theme: Mapped[str] = mapped_column(String(8), default="light")  # light | dark
    # NULL for accounts that only sign in with Google.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="learner", index=True)  # learner | educator | rnd | admin
    # Accounts an admin creates get a temporary password and must set their own at first sign-in.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Bumped on password change / deactivation so every older session token stops working.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Self-service deletion anonymises the row (payments/usage must keep a user id).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def public(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
            "avatar_url": self.avatar_url,
            "locale": self.locale,
            "timezone": self.timezone,
            "theme": self.theme,
            "email_verified": self.email_verified_at is not None,
            "has_password": bool(self.password_hash),
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() + "Z" if self.last_login_at else None,
        }


# Usernames are unique ignoring case; the database enforces it too, not only the check in register.
Index("uq_users_username_lower", func.lower(User.username), unique=True)


class AuthSession(Base):
    """One signed-in device. Lets a user see and revoke sessions; logout really ends one."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = created_at()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VerificationCode(Base):
    """6-digit codes for email verification and password reset (only the HMAC is stored)."""

    __tablename__ = "verification_codes"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    purpose: Mapped[str] = mapped_column(String(20))  # verify_email | reset_password
    email: Mapped[str] = mapped_column(String(255))
    code_hash: Mapped[str] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()


class UserIdentity(Base):
    """An external sign-in linked to an account (Google OAuth; more providers later)."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    provider: Mapped[str] = mapped_column(String(20))  # google
    subject: Mapped[str] = mapped_column(String(255))  # the provider's stable user id ("sub")
    email: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RoleRequest(Base):
    """Someone who signs up as educator starts as learner until an admin approves (setting can turn this off)."""

    __tablename__ = "role_requests"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    requested_role: Mapped[str] = mapped_column(String(16))  # educator
    reason: Mapped[str] = mapped_column(String(1000), default="")  # e.g. subject taught, organisation
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | approved | rejected | canceled
    decided_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = created_at()


class UserInvitation(Base):
    """Admin invites someone by email with a preset role (alternative to creating the account directly)."""

    __tablename__ = "user_invitations"

    id: Mapped[str] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16), default="learner")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    invited_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    """Who did what to what (admin actions, sign-ins, security events). Append-only."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)  # e.g. user.create, user.promote, user.deactivate
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
