"""Sign-up, sign-in, sign-out, current account, password change."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..deps import get_db, get_settings, signed_in_user
from ..models import AuthSession, User, utcnow
from ..services import accounts, audit, invitations, mailer, org_import, role_requests, verification
from ..services.app_settings import get_setting
from ..security import create_token, decode_token, hash_password, verify_password

router = APIRouter()

# Verified against when the account does not exist, so "unknown user" takes as long as "wrong password".
_DUMMY_HASH = hash_password("timing-equaliser-0")
_error = accounts.error
check_password = accounts.check_password


class RegisterBody(BaseModel):
    username: str
    email: EmailStr | None = None
    full_name: str = Field(default="", max_length=120)
    password: str
    confirm_password: str
    # What the person signs up as. "educator" becomes a request an admin approves (see role_requests).
    role: Literal["learner", "educator"] = "learner"
    educator_reason: str = Field(default="", max_length=1000)


class LoginBody(BaseModel):
    login: str = Field(min_length=1, max_length=255)  # username or email
    password: str = Field(min_length=1, max_length=256)


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    # The browser reaches us through the Next.js rewrite on this machine, so the peer is always
    # loopback; the proxy appends the real client to X-Forwarded-For. Only trust it from loopback.
    if host in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if forwarded:
            return forwarded
    return host


def _set_session(response: Response, settings: Settings, user: User, db: Session, request: Request) -> None:
    """Open a device session (listed under Account -> Devices) and set the cookie. Commits."""
    ttl = settings.session_hours * 3600
    session = AuthSession(user_id=user.id, ip=_client_ip(request)[:64],
                          user_agent=request.headers.get("user-agent", "")[:300],
                          expires_at=utcnow() + timedelta(seconds=ttl))
    db.add(session)
    db.commit()
    token = create_token(settings.secret_key, user.id, user.token_version, ttl, sid=session.id)
    response.set_cookie(
        settings.cookie_name, token, max_age=ttl, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/",
    )


@router.post("/register", status_code=201)
def register(
    body: RegisterBody, request: Request, response: Response, db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    username = accounts.clean_username(body.username)
    email = str(body.email).strip().lower() if body.email else None
    if body.password != body.confirm_password:
        raise _error(422, "confirm_password", "Passwords do not match.")
    check_password(body.password, username=username, email=email or "")
    password_hash = hash_password(body.password)  # slow; done outside the lock
    with request.app.state.accounts_lock:
        accounts.ensure_available(db, username, email)
        # The very first account becomes the administrator.
        first = db.scalar(select(func.count()).select_from(User)) == 0
        reg = get_setting(db, "registration")
        if not first and not reg.get("allow_signup", True):
            raise _error(403, None, "Sign-up is closed. Ask an administrator for an account or an invitation.")
        user = User(
            username=username, email=email, full_name=body.full_name.strip(),
            password_hash=password_hash, role="admin" if first else "learner",
            last_login_at=utcnow(),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:  # lost a race with another process
            db.rollback()
            raise _error(409, "username", "That username or email is already registered.") from None
        req = None
        if body.role == "educator" and not first:
            try:
                _, req = role_requests.request_role(
                    db, user, "educator", body.educator_reason, ip=_client_ip(request)
                )
            except HTTPException:
                db.rollback()  # drop the flushed account: sign-up as educator is refused as a whole
                raise
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise _error(409, "username", "That username or email is already registered.") from None
    role_req = role_requests.public(req) if req else None
    if email and mailer.configured(db):
        verification.issue(db, user, "verify_email")
        if get_setting(db, "registration").get("require_email_verification") and not first:
            # No session yet: the person confirms the code first (POST /email/confirm).
            return {"user": user.public(), "role_request": role_req, "needs_verification": True}
    _set_session(response, settings, user, db, request)
    return {"user": user.public(), "role_request": role_req, "needs_verification": False}


@router.post("/login")
def login(
    body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    throttle = request.app.state.login_throttle
    ip, typed = _client_ip(request), body.login.strip().lower()
    user = db.scalar(
        select(User).where((func.lower(User.username) == typed) | (User.email == typed))
    )
    # Count failures per account, not per spelling, so username and email share one budget.
    account = f"id:{user.id}" if user else f"name:{typed}"
    wait = throttle.retry_after(ip, account)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"field": None, "message": f"Too many failed attempts. Try again in {-(-wait // 60)} min."},
            headers={"Retry-After": str(wait)},
        )
    valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valid:
        throttle.failure(ip, account)
        raise _error(401, None, "Wrong username/email or password.")
    if not user.is_active or user.deleted_at is not None:
        raise _error(403, None, "This account has been deactivated. Contact an administrator.")
    throttle.success(ip, account)
    if _must_verify(db, user):
        verification.issue(db, user, "verify_email")
        raise HTTPException(status_code=403, detail={
            "field": None, "code": "email_not_verified",
            "message": "Confirm your e-mail first: enter the code we sent to your address."})
    user.last_login_at = utcnow()
    db.commit()
    _set_session(response, settings, user, db, request)
    return {"user": user.public()}


def _must_verify(db: Session, user: User) -> bool:
    return bool(user.email and user.email_verified_at is None and user.role != "admin"
                and get_setting(db, "registration").get("require_email_verification") and mailer.configured(db))


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           settings: Settings = Depends(get_settings)):
    token = request.cookies.get(settings.cookie_name)
    payload = decode_token(settings.secret_key, token) if token else None
    if payload and isinstance(payload.get("sid"), str):
        s = db.get(AuthSession, payload["sid"])
        if s is not None and s.revoked_at is None:
            s.revoked_at = utcnow()  # signing out really ends this session on the server
            db.commit()
    response.delete_cookie(
        settings.cookie_name, path="/", httponly=True, samesite="lax", secure=settings.cookie_secure,
    )
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(signed_in_user)):
    return {"user": user.public()}


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """Public: what the sign-in and sign-up pages need to know before anyone signs in."""
    reg = get_setting(db, "registration")
    google = get_setting(db, "google_oauth")
    general = get_setting(db, "general")
    return {
        "has_users": bool(db.scalar(select(func.count()).select_from(User))),
        "signup_open": bool(reg.get("allow_signup", True)),
        "educator_signup": bool(reg["allow_educator_signup"]),
        "educator_requires_approval": bool(reg["educator_requires_approval"]),
        "google": bool(google.get("enabled") and google.get("client_id") and google.get("client_secret_enc")),
        "email": mailer.configured(db),
        "site_name": general.get("site_name") or "PathMind",
        "default_locale": general.get("default_locale") or "vi",
    }


@router.post("/password")
def change_password(
    body: PasswordBody, request: Request, response: Response, user: User = Depends(signed_in_user),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
):
    with request.app.state.accounts_lock:
        # Re-read inside the lock: an admin reset committed meanwhile must not be overwritten.
        db.refresh(user)
        return _change_password(body, response, user, db, settings, request)


def _change_password(body: PasswordBody, response: Response, user: User, db: Session, settings: Settings,
                     request: Request):
    # Accounts made through Google have no password yet: they set one without the current password.
    if user.password_hash and not verify_password(body.current_password, user.password_hash):
        raise _error(400, "current_password", "Current password is wrong.")
    check_password(body.new_password, username=user.username, email=user.email or "")
    if body.new_password == body.current_password:
        raise _error(422, "new_password", "The new password must be different from the current one.")
    user.must_change_password = False
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # signs out every other session
    _revoke_all(db, user.id)
    db.commit()
    _set_session(response, settings, user, db, request)
    return {"ok": True}


def _revoke_all(db: Session, user_id: str) -> None:
    from sqlalchemy import update

    db.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


# ── Invitations (public: the link itself is the credential) ──────────────────────────────────
# The token travels in the request body, never in the URL path, so it does not end up in access logs.

_BAD_INVITE = "This invitation link is invalid, already used, revoked or expired."


class InviteTokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class AcceptInviteBody(InviteTokenBody):
    username: str
    full_name: str = Field(default="", max_length=120)
    password: str
    confirm_password: str


def _usable_invitation(db: Session, token: str):
    """An open invitation whose inviter is still an active administrator (else it is void)."""
    inv = invitations.find_open(db, token)
    if inv is None:
        return None
    inviter = db.get(User, inv.invited_by) if inv.invited_by else None
    if inviter is None or inviter.role != "admin" or not inviter.is_active:
        return None
    return inv


@router.post("/invitations/lookup")
def view_invitation(body: InviteTokenBody, db: Session = Depends(get_db)):
    inv = _usable_invitation(db, body.token)
    if inv is None:
        raise _error(404, None, _BAD_INVITE)
    return {"invitation": {"email": inv.email, "role": inv.role,
                           "expires_at": inv.expires_at.isoformat() + "Z"}}


@router.post("/invitations/accept", status_code=201)
def accept_invitation(
    body: AcceptInviteBody, request: Request, response: Response,
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
):
    username = accounts.clean_username(body.username)
    if body.password != body.confirm_password:
        raise _error(422, "confirm_password", "Passwords do not match.")
    # Validate and hash outside the lock (scrypt is slow); everything is re-checked inside it.
    first_look = _usable_invitation(db, body.token)
    if first_look is None:
        raise _error(404, None, _BAD_INVITE)
    check_password(body.password, username=username, email=first_look.email)
    password_hash = hash_password(body.password)
    with request.app.state.accounts_lock:
        db.expire_all()
        inv = _usable_invitation(db, body.token)
        if inv is None:
            raise _error(404, None, _BAD_INVITE)
        accounts.ensure_available(db, username, inv.email)
        user = User(
            username=username, email=inv.email, full_name=body.full_name.strip(),
            password_hash=password_hash, role=inv.role, created_by=inv.invited_by,
            last_login_at=utcnow(),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise _error(409, "username", "That username or email is already registered.") from None
        inv.accepted_at = utcnow()
        inv.accepted_user_id = user.id
        org_import.apply_pending_invitation(db, inv, user)
        audit.record(db, user.id, "invitation.accept", target_type="user", target_id=user.id,
                     data={"invitation_id": inv.id, "role": inv.role}, ip=_client_ip(request))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise _error(409, "username", "That username or email is already registered.") from None
    _set_session(response, settings, user, db, request)
    return {"user": user.public()}


# ── E-mail confirmation and password reset ───────────────────────────────────


class CodeBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


@router.post("/email/send-code")
def send_code(db: Session = Depends(get_db), user: User = Depends(signed_in_user)):
    if not user.email:
        raise _error(409, "email", "Add an e-mail address to your account first.")
    if user.email_verified_at is not None:
        return {"sent": False, "verified": True}
    return {"sent": verification.issue(db, user, "verify_email"), "verified": False, "email_configured": mailer.configured(db)}


@router.post("/email/verify")
def verify_email(body: CodeBody, db: Session = Depends(get_db), user: User = Depends(signed_in_user)):
    verification.check(db, user, "verify_email", body.code)
    user.email_verified_at = utcnow()
    db.commit()
    return {"user": user.public()}


class ConfirmBody(BaseModel):
    login: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=4, max_length=12)


def _find(db: Session, login: str) -> User | None:
    typed = login.strip().lower()
    return db.scalar(select(User).where((func.lower(User.username) == typed) | (User.email == typed)))


@router.post("/email/confirm")
def confirm_email(body: ConfirmBody, request: Request, response: Response, db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    """Public: confirm the code sent at sign-up / sign-in, then sign in."""
    user = _find(db, body.login)
    if user is None or not user.is_active:
        raise _error(400, "code", "The code is wrong or has expired.")
    verification.check(db, user, "verify_email", body.code)
    user.email_verified_at = utcnow()
    user.last_login_at = utcnow()
    db.commit()
    _set_session(response, settings, user, db, request)
    return {"user": user.public()}


class ResendBody(BaseModel):
    login: str = Field(min_length=1, max_length=255)


@router.post("/email/resend")
def resend_code(body: ResendBody, db: Session = Depends(get_db)):
    user = _find(db, body.login)
    if user is not None and user.email and user.email_verified_at is None:
        verification.issue(db, user, "verify_email")
    return {"ok": True}  # same answer either way: does not reveal which accounts exist


class ForgotBody(BaseModel):
    email: EmailStr


@router.post("/password/forgot")
def forgot_password(body: ForgotBody, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    if user is not None and user.is_active and user.deleted_at is None:
        verification.issue(db, user, "reset_password")
    return {"ok": True, "email_configured": mailer.configured(db)}


class ResetBody(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    new_password: str


@router.post("/password/reset")
def reset_password(body: ResetBody, request: Request, response: Response, db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    if user is None or not user.is_active:
        raise _error(400, "code", "The code is wrong or has expired.")
    check_password(body.new_password, username=user.username, email=user.email or "")
    with request.app.state.accounts_lock:
        verification.check(db, user, "reset_password", body.code)
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = False
        user.email_verified_at = user.email_verified_at or utcnow()  # they proved they read the mailbox
        user.token_version += 1
        _revoke_all(db, user.id)
        audit.record(db, user.id, "user.password_reset_self", target_type="user", target_id=user.id,
                     ip=_client_ip(request))
        db.commit()
    _set_session(response, settings, user, db, request)
    return {"user": user.public()}
