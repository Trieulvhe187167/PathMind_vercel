"""Request dependencies: database session, current user, admin guard."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import Settings
from .models import AuthSession, User, utcnow
from .security import decode_token


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def _token_from(request: Request, settings: Settings) -> str | None:
    cookie = request.cookies.get(settings.cookie_name)
    if cookie:
        return cookie
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def optional_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User | None:
    token = _token_from(request, settings)
    if not token:
        return None
    payload = decode_token(settings.secret_key, token)
    if payload is None:
        return None
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active or user.token_version != payload["ver"] or user.deleted_at is not None:
        return None
    sid = payload.get("sid")
    if not isinstance(sid, str):
        return None  # tokens from before device sessions existed: sign in again
    session = db.get(AuthSession, sid)
    now = utcnow()
    if session is None or session.user_id != user.id or session.revoked_at is not None or session.expires_at <= now:
        return None
    if (now - session.last_seen_at).total_seconds() > 300:
        session.last_seen_at = now
        db.commit()
    request.state.session_id = sid
    return user


def signed_in_user(user: User | None = Depends(optional_user)) -> User:
    """Any signed-in person, including one who still has to replace a temporary password.

    Only for the few routes that person needs: who am I, and change my password.
    """
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user


def current_user(user: User = Depends(signed_in_user)) -> User:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"field": None, "code": "password_change_required",
                     "message": "Choose a new password before continuing."},
        )
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrators only.")
    return user


def require_roles(*roles: str):
    """Dependency factory: only these roles may call the route (e.g. require_roles("rnd"))."""
    allowed = frozenset(roles)

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role cannot use this.")
        return user

    return dependency
