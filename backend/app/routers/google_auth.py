"""Sign in with Google (OpenID Connect, authorization code flow).

The admin enters the OAuth client ID and secret in Settings -> Google sign-in, and registers
``<public_url>/api/auth/google/callback`` as an authorised redirect URI in Google Cloud Console.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..deps import get_db, get_settings
from ..models import User, UserIdentity, utcnow
from ..services import audit
from ..services.app_settings import get_setting, secret
from ..security import _b64, _unb64
from .auth import _client_ip, _set_session

router = APIRouter()

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
STATE_COOKIE = "pm_oauth_state"
TRANSPORT: httpx.BaseTransport | None = None  # tests fake Google here


def _base(db: Session, request: Request) -> str:
    return (get_setting(db, "general").get("public_url") or str(request.base_url)).rstrip("/")


def _sign_state(secret_key: str, data: dict) -> str:
    body = _b64(json.dumps(data, separators=(",", ":")).encode())
    sig = hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def _read_state(secret_key: str, value: str) -> dict | None:
    try:
        body, sig = value.split(".")
        if not hmac.compare_digest(_unb64(sig), hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).digest()):
            return None
        data = json.loads(_unb64(body))
        return data if data.get("exp", 0) > time.time() else None
    except (ValueError, TypeError):
        return None


def _back(db: Session, request: Request, path: str) -> RedirectResponse:
    return RedirectResponse(_base(db, request) + path, status_code=303)


@router.get("/start")
def start(request: Request, next: str = "/", db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    cfg = get_setting(db, "google_oauth")
    if not (cfg.get("enabled") and cfg.get("client_id")):
        return _back(db, request, "/login?error=google_off")
    nonce = secrets.token_urlsafe(16)
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
    state = _sign_state(settings.secret_key, {"n": nonce, "next": safe_next, "exp": int(time.time()) + 600})
    params = {
        "client_id": cfg["client_id"], "redirect_uri": _base(db, request) + "/api/auth/google/callback",
        "response_type": "code", "scope": "openid email profile", "state": nonce, "prompt": "select_account",
    }
    resp = RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", status_code=303)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
    return resp


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    saved = _read_state(settings.secret_key, request.cookies.get(STATE_COOKIE, ""))
    if error or not code or saved is None or not hmac.compare_digest(saved.get("n", ""), state):
        return _back(db, request, "/login?error=google_failed")
    cfg = get_setting(db, "google_oauth")
    try:
        with httpx.Client(timeout=20, transport=TRANSPORT) as c:
            tok = c.post(TOKEN_URL, data={
                "code": code, "client_id": cfg["client_id"], "client_secret": secret(db, "google_oauth", "client_secret"),
                "redirect_uri": _base(db, request) + "/api/auth/google/callback", "grant_type": "authorization_code",
            })
            tok.raise_for_status()
            info = c.get(USERINFO_URL, headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
            info.raise_for_status()
            profile = info.json()
    except (httpx.HTTPError, KeyError, ValueError):
        return _back(db, request, "/login?error=google_failed")
    sub, email = str(profile.get("sub") or ""), str(profile.get("email") or "").lower()
    if not sub:
        return _back(db, request, "/login?error=google_failed")

    with request.app.state.accounts_lock:
        ident = db.scalar(select(UserIdentity).where(UserIdentity.provider == "google", UserIdentity.subject == sub))
        user = db.get(User, ident.user_id) if ident else None
        if user is None and email and profile.get("email_verified"):
            # Link to an existing account with the same verified address.
            user = db.scalar(select(User).where(User.email == email))
        created = False
        if user is None:
            reg = get_setting(db, "registration")
            if not (cfg.get("allow_signup", True) and reg.get("allow_signup", True)):
                return _back(db, request, "/login?error=signup_closed")
            first = db.scalar(select(func.count()).select_from(User)) == 0
            base = (email.split("@")[0] if email else "user").lower()
            base = "".join(ch for ch in base if ch.isalnum() or ch in "._-")[:24] or "user"
            username = base if len(base) >= 3 else base + "001"
            n = 1
            while db.scalar(select(User.id).where(func.lower(User.username) == username)):
                n += 1
                username = f"{base}{n}"
            user = User(username=username, email=email or None, full_name=str(profile.get("name") or "")[:120],
                        avatar_url=str(profile.get("picture") or "")[:500], role="admin" if first else "learner",
                        password_hash=None, email_verified_at=utcnow() if profile.get("email_verified") else None)
            db.add(user)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                return _back(db, request, "/login?error=google_failed")
            created = True
        if not user.is_active or user.deleted_at is not None:
            return _back(db, request, "/login?error=deactivated")
        if ident is None:
            db.add(UserIdentity(user_id=user.id, provider="google", subject=sub, email=email))
        else:
            ident.last_used_at = utcnow()
        if email and user.email == email and user.email_verified_at is None and profile.get("email_verified"):
            user.email_verified_at = utcnow()
        user.last_login_at = utcnow()
        if created:
            audit.record(db, user.id, "user.signup_google", target_type="user", target_id=user.id, ip=_client_ip(request))
        db.commit()
    resp = _back(db, request, saved.get("next") or "/")
    _set_session(resp, settings, user, db, request)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp
