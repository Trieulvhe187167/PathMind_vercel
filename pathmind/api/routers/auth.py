"""Auth router — login, logout, status, registration, profile, and user-management endpoints."""

from contextvars import Token as _CtxToken
from datetime import datetime, timedelta, timezone
import logging
import os
import re

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator

from pathmind.services import email_verification as _email_verification
from pathmind.services.i18n import t as _t
from pathmind.services.config import load_auth_settings

# SameSite=None lets the cookie work when the browser accesses the frontend via
# 127.0.0.1 and the backend via localhost (different origins on the same machine).
# Browsers require Secure=True for SameSite=None, but that needs HTTPS — so in
# local dev we fall back to SameSite=Lax and tell users to use localhost:// URLs.
_SECURE = bool(load_auth_settings()["cookie_secure"])
_SAMESITE = "none" if _SECURE else "lax"

from pathmind.multi_user.audit import log_admin_action
from pathmind.multi_user.context import (
    reset_current_user,
    set_current_user,
    user_from_token_payload,
)
from pathmind.multi_user.device_credentials import (
    heartbeat_device_credential,
    issue_device_credential,
    list_device_credentials,
    revoke_device_credential,
)
from pathmind.multi_user.identity import get_user_by_id
from pathmind.multi_user.learning_access import learning_policy_for_user
from pathmind.multi_user.models import AccountPreset
from pathmind.multi_user.paths import local_admin_user
from pathmind.services.auth import (
    AUTH_ENABLED,
    POCKETBASE_ENABLED,
    TOKEN_EXPIRE_HOURS,
    TokenPayload,
    add_user,
    authenticate,
    authenticate_device,
    authenticate_pb,
    create_token,
    decode_token,
    delete_user,
    get_user_info,
    is_first_user,
    list_users,
    register_pb,
    set_avatar,
    set_role,
)
from pathmind.services.codex_auth.contracts import CodexAuthError
from pathmind.services.codex_auth.service import deliver_codex_oauth_callback

logger = logging.getLogger(__name__)

router = APIRouter()

_COOKIE_NAME = "dt_token"
_COOKIE_MAX_AGE = TOKEN_EXPIRE_HOURS * 3600


def _cookie_attrs() -> dict:
    """Attribute set shared by ``login``'s ``set_cookie`` and ``logout``'s
    ``delete_cookie``.

    The deletion ``Set-Cookie`` must carry the same attributes as the one
    that created the cookie — ``delete_cookie`` defaults ``secure=False``,
    which browsers reject when paired with ``SameSite=None``, silently
    keeping the old cookie. See #623. Reads the module globals at call time
    so tests can monkeypatch ``_SECURE``/``_SAMESITE``.
    """
    return {
        "key": _COOKIE_NAME,
        "httponly": True,
        "samesite": _SAMESITE,
        "secure": _SECURE,
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Payload for the POST /login endpoint."""

    username: str
    password: str


class DeviceLoginRequest(BaseModel):
    """Payload for the built-in device-credential login endpoint."""

    pairing_code: str = Field(min_length=8, max_length=128)
    pin: str = Field(min_length=6, max_length=6)


class DeviceCredentialCreateRequest(BaseModel):
    """Admin payload for issuing a local ordinary-user device credential."""

    user_id: str = Field(min_length=1, max_length=64)
    device_name: str = Field(min_length=1, max_length=80)
    expires_in_days: int = Field(ge=1, le=365)
    daily_limit_minutes: int = Field(ge=5, le=1440)


class RegisterRequest(BaseModel):
    """Payload for the POST /register endpoint."""

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        import re

        v = v.strip()
        if not v:
            raise ValueError("Email cannot be empty")
        # Accept standard email addresses (used by PocketBase mode) or plain
        # usernames (used by the built-in SQLite/JSON auth mode).
        email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        plain_re = re.compile(r"^[A-Za-z0-9_\-.]{3,64}$")
        if not email_re.match(v) and not plain_re.match(v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class SignupRequest(BaseModel):
    """Payload for the public POST /register endpoint (detailed sign-up).

    Types are lenient on purpose: every rule is checked in
    ``_validate_signup`` so errors come back localized with a ``field`` code
    the form can highlight.
    """

    full_name: str = ""
    email: str = ""
    username: str = ""  # optional — defaults to the email
    password: str = ""
    confirm_password: str | None = None
    accept_terms: bool = False
    # "user" (learner) or "educator". Educators need no approval: what they can
    # do with it (edit a knowledge branch, contribute to R&D) is behind a paid plan.
    role: str = "user"
    # Signed invitation token (R&D staff are invited by email, never self-assigned).
    invite: str = ""


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=1, max_length=12)


class EmailOnlyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    confirm: str = Field(default="", max_length=20)


class UpdateProfileDetailsRequest(BaseModel):
    full_name: str = Field(default="", max_length=120)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLAIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{3,64}$")


def _signup_error(field: str, key: str, status_code: int = status.HTTP_400_BAD_REQUEST, **kw):
    return HTTPException(
        status_code=status_code,
        detail={"code": key.split(".", 1)[-1], "field": field, "message": _t(key, **kw)},
    )


def _password_problem(password: str, *identities: str) -> str | None:
    if len(password) < 8:
        return "auth.password_too_short"
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "auth.password_weak"
    lowered = password.lower()
    if any(ident and lowered == ident.lower() for ident in identities):
        return "auth.password_same_as_username"
    return None


def _validate_signup(body: SignupRequest) -> tuple[str, str, str]:
    """Return (username, email, full_name) or raise a localized 400/409."""
    from pathmind.multi_user.identity import find_user_by_email

    full_name = " ".join(body.full_name.split())
    if not 2 <= len(full_name) <= 120:
        raise _signup_error("full_name", "auth.full_name_invalid")
    email = body.email.strip().lower()
    if len(email) > 255 or not _EMAIL_RE.match(email):
        raise _signup_error("email", "auth.email_invalid")
    username = body.username.strip() or email
    if username != email and not (
        _PLAIN_USERNAME_RE.match(username) or _EMAIL_RE.match(username)
    ):
        raise _signup_error("username", "auth.username_invalid")
    problem = _password_problem(body.password, username, email)
    if problem:
        raise _signup_error("password", problem)
    if body.confirm_password is not None and body.confirm_password != body.password:
        raise _signup_error("confirm_password", "auth.password_mismatch")
    if not body.accept_terms:
        raise _signup_error("accept_terms", "auth.terms_required")
    existing = {str(u["username"]).lower() for u in list_users()}
    if username.lower() in existing:
        raise _signup_error("username", "auth.username_taken", status.HTTP_409_CONFLICT)
    if find_user_by_email(email) is not None:
        raise _signup_error("email", "auth.email_taken", status.HTTP_409_CONFLICT)
    return username, email, full_name


class SetRoleRequest(BaseModel):
    """Payload for the PUT /users/{username}/role endpoint."""

    role: str

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in ("admin", "rnd", "educator", "user"):
            raise ValueError("Role must be 'admin', 'rnd', 'educator' or 'user'")
        return v


class AdminCreateUserRequest(RegisterRequest):
    """Admin user-creation payload.

    A preset configures an ordinary account; it never becomes a third role.
    """

    preset: AccountPreset = "standard"
    full_name: str = ""
    email: str = ""


class AuthStatusResponse(BaseModel):
    """Response body for the GET /status endpoint."""

    enabled: bool
    authenticated: bool
    user_id: str | None = None
    username: str | None = None
    role: str | None = None
    is_admin: bool = False
    avatar: str = ""
    preset: AccountPreset | None = None
    learning_policy: dict | None = None
    full_name: str = ""
    email: str = ""
    email_verified: bool = False
    # True when SMTP is configured, i.e. codes can actually be delivered.
    email_verification_available: bool = False


class UserInfo(BaseModel):
    """Single user record returned by the GET /users and /profile endpoints."""

    id: str = ""
    username: str
    role: str
    created_at: str
    disabled: bool = False
    avatar: str = ""
    preset: AccountPreset = "standard"
    full_name: str = ""
    email: str = ""
    email_verified: bool = False


# Markers settable through PUT /profile. Image markers ("img:<version>") are
# managed exclusively by the upload endpoint so users cannot point their
# avatar at a file that was never validated.
_ICON_MARKER_RE = re.compile(r"^icon:[a-z0-9-]{1,32}:[a-z0-9-]{1,32}$")

# User ids are generated as "u_<uuid hex>" (plus the "local-admin" /
# "env-admin" sentinels); reject anything else before it reaches the
# filesystem layer.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UpdateProfileRequest(BaseModel):
    """Payload for the PUT /profile endpoint."""

    avatar: str

    @field_validator("avatar")
    @classmethod
    def avatar_valid(cls, v: str) -> str:
        v = v.strip()
        if v and not _ICON_MARKER_RE.match(v):
            raise ValueError("Avatar must be empty or 'icon:<name>:<color>'")
        return v


# ---------------------------------------------------------------------------
# Shared helper — extract token from cookie or Bearer header
# ---------------------------------------------------------------------------


def _bearer_token_from_header(authorization: str | None) -> str | None:
    """Parse ``Authorization: Bearer <token>`` without using ``HTTPBearer``.

    ``HTTPBearer`` is a class-based dependency whose ``__call__`` is annotated
    ``request: Request``. FastAPI doesn't inject a Request into WebSocket
    dependency resolution, which makes ``HTTPBearer`` raise ``TypeError`` the
    moment a router with this dep mounts a WS endpoint. Doing the parse by
    hand keeps ``require_auth`` HTTP/WS-symmetric.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    return None


def _extract_token(authorization: str | None, dt_token: str | None) -> str | None:
    return _bearer_token_from_header(authorization) or dt_token


# ---------------------------------------------------------------------------
# Dependencies — reusable auth guards for other routers
# ---------------------------------------------------------------------------


def _install_current_user(payload: TokenPayload | None) -> _CtxToken:
    """Install the request-local current-user ContextVar from an auth result.

    Single point of truth for ``payload → CurrentUser`` so HTTP and WebSocket
    entry points produce identical user objects. ``payload is None`` means
    "no JWT was required" (AUTH_ENABLED=false) and resolves to the local
    admin user; a non-None payload resolves through ``user_from_token_payload``.

    Returns the ContextVar reset token. HTTP callers ignore it (the request
    ends with the task, so the var is GC'd with the task context). WebSocket
    callers keep it and call ``reset_current_user`` in their ``finally`` block,
    because a WS connection outlives the dependency-resolution task.

    ⚠ Invariant: every authenticated entry point MUST call this before the
    handler runs. Skipping it leaves ``get_current_path_service()`` falling
    back to the admin workspace — the silent-routing root cause of #481.
    """
    user = local_admin_user() if payload is None else user_from_token_payload(payload)
    return set_current_user(user)


async def require_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    request: Request = None,
) -> TokenPayload | None:
    """
    FastAPI dependency that enforces authentication when AUTH_ENABLED=true.

    Accepts the JWT from either:
      - Authorization: Bearer <token> header
      - dt_token cookie

    ``Header`` and ``Cookie`` are kept here in place of ``HTTPBearer`` so the
    function stays usable from WebSocket call sites that don't go through
    FastAPI's standard HTTP request lifecycle.

    Returns the authenticated TokenPayload, or None if auth is disabled.
    Raises HTTP 401 if auth is enabled but the token is missing or invalid.

    Declared ``async def`` so the ``set_current_user`` call runs in the same
    asyncio context as the endpoint. A sync dependency is dispatched via
    ``anyio.to_thread.run_sync``, which executes the function in a worker
    thread under a *copy* of the request context; any ``ContextVar.set``
    inside that thread is discarded when the thread returns, leaving the
    endpoint to read the unset default. That regression was the root cause
    of #481.
    """
    if not AUTH_ENABLED:
        _install_current_user(None)
        _install_request_workspace(request)
        return None

    token = _extract_token(authorization, dt_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _install_current_user(payload)
    _install_request_workspace(request)
    return payload


def _install_request_workspace(request) -> None:
    from pathmind.services.workspace.context import install_workspace_scope
    from pathmind.services.workspace.models import WorkspaceError

    headers = getattr(request, "headers", {})
    params = getattr(request, "query_params", {})
    header = headers.get("x-pathmind-workspace")
    query = params.get("dt_workspace")
    if header is not None and query is not None and header != query:
        raise HTTPException(status_code=400, detail="Conflicting workspace scopes.")
    try:
        path = getattr(getattr(request, "url", None), "path", "")
        from pathmind.services.workspace.knowledge import library_request

        library_request.set(
            path.startswith(("/api/knowledge-bases", "/ws/knowledge-bases"))
            and params.get("resource_library") == "true"
        )
        from pathmind.services.skill.runtime import library_workspace

        skill_workspace = (
            params.get("skill_workspace", "") if path.startswith("/api/skills") else ""
        )
        library_workspace.set(skill_workspace)
        catalog_management = library_request.get() or path.startswith(
            ("/api/skills", "/api/space/mcp")
        )
        management = path.startswith(("/api/settings", "/api/auth", "/api/multi-user"))
        selected = install_workspace_scope(
            None if management or catalog_management else header if header is not None else query
        )
        if skill_workspace:
            from pathmind.services.workspace import get_content_workspace_service

            service = get_content_workspace_service()
            service.validate_chat_binding(
                skill_workspace,
                existing=getattr(request, "method", "GET") in {"GET", "HEAD", "OPTIONS"},
            )
        if (
            not management
            and selected.archived
            and getattr(request, "method", "GET") not in {"GET", "HEAD", "OPTIONS"}
        ):
            raise WorkspaceError("Restore this workspace before changing its data.")
        # Management/migration requests acquire their own exclusive lease.
        if getattr(request, "method", None) is not None and not management:
            from pathmind.services.workspace.activity import acquire_activity

            state = getattr(request, "state", None)
            if state is not None and getattr(state, "workspace_activity", None) is None:
                state.workspace_activity = acquire_activity()
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class _WsAuthFailed:
    """Sentinel: ws_require_auth failed and closed the WebSocket."""


ws_auth_failed: _WsAuthFailed = _WsAuthFailed()


async def ws_require_auth(ws: WebSocket) -> _CtxToken | _WsAuthFailed:
    """Authenticate a WebSocket connection and set the user ContextVar.

    Must be called **before** ``ws.accept()`` so the server can reject
    unauthenticated upgrades cleanly.

    Returns a ContextVar reset token on success, or ``ws_auth_failed``
    on failure (the WebSocket is already closed — the caller should
    ``return`` immediately).

    Usage::

        user_token = await ws_require_auth(ws)
        if user_token is ws_auth_failed:
            return
        await ws.accept()
        try:
            ...
        finally:
            reset_current_user(user_token)
    """
    payload = None
    if AUTH_ENABLED:
        token = ws.query_params.get("token") or ws.cookies.get(_COOKIE_NAME)
        payload = decode_token(token) if token else None
        if not payload:
            await ws.close(code=4001)
            return ws_auth_failed
    user_token = _install_current_user(payload)
    try:
        _install_request_workspace(ws)
    except HTTPException:
        reset_current_user(user_token)
        await ws.close(code=4004)
        return ws_auth_failed
    return user_token


async def require_admin(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload:
    """
    FastAPI dependency that requires the caller to be an admin.

    Raises HTTP 403 if the authenticated user is not an admin.
    When AUTH_ENABLED=false, all requests are treated as admin.

    ``async def`` mirrors ``require_auth`` so the dependency chain stays on
    the event loop and the user ContextVar set by ``require_auth`` is visible
    to the endpoint.
    """
    if not AUTH_ENABLED:
        return _local_admin_token_payload()

    if payload is None or payload.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return payload


def _live_role(payload: TokenPayload | None) -> str:
    """Role from the identity store (a token issued before an upgrade is stale)."""
    if payload is None:
        return ""
    info = get_user_info(payload.username)
    return str((info or {}).get("role") or payload.role)


async def require_educator(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload:
    """Educator accounts (admins pass too). Contribution features also check the plan."""
    if not AUTH_ENABLED:
        return _local_admin_token_payload()
    if payload is None or _live_role(payload) not in ("educator", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_t("auth.educator_only"))
    return payload


async def require_rnd(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload:
    """R&D accounts (admins pass too)."""
    if not AUTH_ENABLED:
        return _local_admin_token_payload()
    if payload is None or _live_role(payload) not in ("rnd", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_t("auth.rnd_only"))
    return payload


async def require_rnd_only(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload:
    """R&D accounts only: changes to the main knowledge tree (administrators just read it)."""
    if not AUTH_ENABLED:
        return _local_admin_token_payload()
    if payload is None or _live_role(payload) != "rnd":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_t("auth.rnd_only"))
    return payload


async def optional_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    request: Request = None,
) -> TokenPayload | None:
    """Like ``require_auth`` but never rejects: for public endpoints whose
    answer should follow the signed-in user when there is one (e.g. the
    pre-session UI preferences). A missing/invalid token keeps the default
    scope instead of raising 401."""
    if not AUTH_ENABLED:
        _install_current_user(None)
        _install_request_workspace(request)
        return None
    token = _extract_token(authorization, dt_token)
    payload = decode_token(token) if token else None
    if payload is not None:
        _install_current_user(payload)
        _install_request_workspace(request)
    return payload


def _learning_surface_for_path(path: str) -> str:
    normalized = "/" + str(path or "").lstrip("/")
    for root, surface in (
        ("/api/reading", "reading"),
        ("/api/courses", "reading"),
        ("/api/chat", "chat"),
        ("/api/question", "chat"),
        ("/api/question-notebook", "chat"),
        ("/api/sessions", "chat"),
    ):
        if normalized == root or normalized.startswith(f"{root}/"):
            return surface
    return ""


async def require_learning_surface(
    request: Request,
    _: TokenPayload | None = Depends(require_auth),
) -> None:
    """Second-stage default-deny guard for configured learning accounts."""
    from pathmind.multi_user.learning_access import assert_learning_surface

    try:
        assert_learning_surface(_learning_surface_for_path(request.url.path))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _local_admin_token_payload() -> TokenPayload:
    """Synthetic admin payload used when AUTH_ENABLED=false.

    Mirrors the local admin identity (LOCAL_ADMIN_USERNAME / LOCAL_ADMIN_ID)
    so audit logs and self-reference checks behave the same as in multi-user
    mode. Values are kept aligned with ``local_admin_user()`` in
    ``pathmind/multi_user/paths.py``.
    """
    from pathmind.multi_user.models import LOCAL_ADMIN_ID, LOCAL_ADMIN_USERNAME

    return TokenPayload(
        username=LOCAL_ADMIN_USERNAME,
        role="admin",
        user_id=LOCAL_ADMIN_ID,
    )


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.get("/openai-codex/callback")
async def receive_codex_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    headers = {"Cache-Control": "no-store"}
    try:
        callback_state = state if len(request.query_params.getlist("state")) == 1 else None
        await deliver_codex_oauth_callback(code, callback_state, error)
    except CodexAuthError as exc:
        return HTMLResponse(
            (
                "<!doctype html><title>PathMind Codex</title>"
                "<p>Authentication could not be received. Return to PathMind and try again.</p>"
            ),
            status_code=exc.http_status,
            headers=headers,
        )
    return HTMLResponse(
        (
            "<!doctype html><title>PathMind Codex</title>"
            "<p>Authentication received. You can return to PathMind.</p>"
        ),
        headers=headers,
    )


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None, alias=_COOKIE_NAME),
) -> AuthStatusResponse:
    """Return whether auth is enabled and whether the current request is authenticated."""
    if not AUTH_ENABLED:
        return AuthStatusResponse(
            enabled=False,
            authenticated=True,
            user_id="local-admin",
            username="local",
            role="admin",
            is_admin=True,
            preset="standard",
        )

    token = _extract_token(authorization, dt_token)
    payload = decode_token(token) if token else None
    avatar = ""
    preset: AccountPreset | None = None
    learning_policy = None
    profile_fields: dict = {}
    live_role = payload.role if payload else None
    if payload is not None:
        info = get_user_info(payload.username)
        if info:
            # The token's role can be stale after a self-service upgrade.
            live_role = str(info.get("role") or payload.role)
            profile_fields = {
                "full_name": str(info.get("full_name") or ""),
                "email": str(info.get("email") or ""),
                "email_verified": bool(info.get("email_verified", False)),
            }
            avatar = str(info.get("avatar") or "")
            raw_preset = str(info.get("preset") or "standard")
            if raw_preset == "learner":
                preset = "learner"
            elif raw_preset == "custom":
                preset = "custom"
            else:
                preset = "standard"
        learning_policy = learning_policy_for_user(
            payload.user_id,
            is_admin=payload.role == "admin",
        )
    return AuthStatusResponse(
        enabled=True,
        authenticated=payload is not None,
        user_id=payload.user_id if payload else None,
        username=payload.username if payload else None,
        role=live_role,
        is_admin=live_role == "admin",
        avatar=avatar,
        preset=preset,
        learning_policy=learning_policy,
        email_verification_available=_email_verification.smtp_configured(),
        **profile_fields,
    )


# Brute-force protection for POST /login (in-memory, per backend process).
# * the same client IP + account: LOGIN_MAX_FAILURES wrong passwords within
#   the window lock that pair for LOGIN_LOCK_MINUTES;
# * one account from any IPs: 4x that many failures lock the account too,
#   which slows attacks spread over many addresses.
# X-Forwarded-For is only trusted when PATHMIND_TRUST_PROXY=1 (i.e. behind a
# reverse proxy you control), so clients cannot fake a fresh IP per attempt.
_LOGIN_MAX_FAILURES = max(1, int(os.environ.get("LOGIN_MAX_FAILURES", "5") or 5))
_LOGIN_LOCK_SECONDS = max(60, int(os.environ.get("LOGIN_LOCK_MINUTES", "15") or 15) * 60)
_login_failures: dict[str, list[float]] = {}
_login_locked_until: dict[str, float] = {}


def _client_ip(request: Request | None) -> str:
    if request is None:
        return "unknown"
    if os.environ.get("PATHMIND_TRUST_PROXY", "").strip() in {"1", "true", "yes"}:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _login_keys(request: Request | None, username: str) -> tuple[str, str]:
    account = (username or "").strip().lower()
    return f"ip:{_client_ip(request)}|{account}", f"acct:{account}"


def _login_check_locked(keys: tuple[str, str]) -> None:
    import math
    import time

    now = time.monotonic()
    remaining = max((_login_locked_until.get(key, 0.0) - now) for key in keys)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "login_locked",
                "retry_after": int(remaining),
                "message": _t("auth.login_locked", minutes=max(1, math.ceil(remaining / 60))),
            },
            headers={"Retry-After": str(int(remaining))},
        )


def _lockout_limits() -> tuple[int, int]:
    """(max failures, lock seconds): administrator settings win over the environment."""
    try:
        from pathmind.services import system_settings as _ss

        return _ss.get_int("auth.login_max_failures"), _ss.get_int("auth.login_lock_minutes") * 60
    except Exception:  # noqa: BLE001
        return _LOGIN_MAX_FAILURES, _LOGIN_LOCK_SECONDS


def _login_record_failure(keys: tuple[str, str]) -> None:
    import time

    now = time.monotonic()
    max_failures, lock_seconds = _lockout_limits()
    for key, limit in ((keys[0], max_failures), (keys[1], max_failures * 4)):
        hits = [t for t in _login_failures.get(key, []) if now - t < lock_seconds]
        hits.append(now)
        _login_failures[key] = hits
        if len(hits) >= limit:
            _login_locked_until[key] = now + lock_seconds
            _login_failures[key] = []
            logger.warning("Login locked for %s after %d failed attempts", key, len(hits))


def _login_record_success(keys: tuple[str, str]) -> None:
    for key in keys:
        _login_failures.pop(key, None)
        _login_locked_until.pop(key, None)


@router.post("/login")
async def login(body: LoginRequest, response: Response, request: Request = None) -> dict:
    """Validate credentials and set a JWT cookie."""
    if not AUTH_ENABLED:
        return {"ok": True, "message": "Auth is disabled — no login required."}
    login_keys = _login_keys(request, body.username)
    _login_check_locked(login_keys)

    if POCKETBASE_ENABLED:
        # PocketBase mode: email = username field for backwards-compat with the
        # existing LoginRequest schema; users can pass their email as "username".
        pb_result = authenticate_pb(body.username, body.password)
        if not pb_result:
            _login_record_failure(login_keys)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )
        payload, pb_token = pb_result
        response.set_cookie(value=pb_token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())
        logger.info(f"User '{payload.username}' logged in via PocketBase (role={payload.role!r})")
        return {
            "ok": True,
            "user_id": payload.user_id,
            "username": payload.username,
            "role": payload.role,
            "is_admin": payload.role == "admin",
        }

    # Standard JWT + bcrypt mode
    result = authenticate(body.username, body.password)
    if not result:
        from pathmind.services.auth import is_locked_account

        if is_locked_account(body.username, body.password):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "account_disabled", "message": _t("auth.account_disabled")},
            )
        _login_record_failure(login_keys)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_t("auth.login_failed"),
        )
    _login_record_success(login_keys)
    if _email_verification.verification_required():
        info = get_user_info(result.username) or {}
        if info.get("email") and not info.get("email_verified") and result.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "email_not_verified",
                    "email": info["email"],
                    "message": _t("auth.email_not_verified"),
                },
            )

    token = create_token(result.username, result.role, result.user_id)
    response.set_cookie(value=token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())

    logger.info(f"User '{result.username}' logged in (role={result.role!r})")
    return {
        "ok": True,
        "user_id": result.user_id,
        "username": result.username,
        "role": result.role,
        "is_admin": result.role == "admin",
    }


def _require_builtin_device_auth() -> None:
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device credentials require built-in authentication.",
        )
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device credentials are not supported in PocketBase mode.",
        )


@router.post("/device-login")
async def device_login(body: DeviceLoginRequest, response: Response) -> dict:
    """Exchange a device pairing code and PIN for the account's normal cookie."""

    _require_builtin_device_auth()
    payload = authenticate_device(body.pairing_code, body.pin)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect device credentials",
        )

    token = create_token(
        payload.username,
        payload.role,
        payload.user_id,
        device_credential_id=payload.device_credential_id,
        device_session_nonce=payload.device_session_nonce,
    )
    response.set_cookie(value=token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())
    logger.info(f"User '{payload.username}' logged in with a device credential")
    return {
        "ok": True,
        "user_id": payload.user_id,
        "username": payload.username,
        "role": payload.role,
        "is_admin": payload.role == "admin",
        "device_credential_id": payload.device_credential_id,
    }


@router.post("/device/heartbeat")
async def device_heartbeat(
    response: Response,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Refresh a device lease and account bounded daily usage."""

    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device credentials require built-in authentication.",
        )
    if payload is None or not payload.device_credential_id or not payload.device_session_nonce:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This session does not use a device credential.",
        )
    try:
        device = heartbeat_device_credential(
            payload.device_credential_id,
            user_id=payload.user_id,
            session_nonce=payload.device_session_nonce,
        )
    except ValueError:
        response.delete_cookie(**_cookie_attrs())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device session is no longer active",
        ) from None
    return {"ok": not device.pop("limit_reached"), **device}


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the JWT cookie.

    Deletion attributes mirror ``login`` structurally via ``_cookie_attrs()``
    (see the rationale there and #623).
    """
    response.delete_cookie(**_cookie_attrs())
    return {"ok": True}


# Self-registration throttle: at most N new accounts per client IP per window.
# In-memory (per backend process) — enough to stop scripted sign-up floods on a
# single-worker deployment; put a CAPTCHA / reverse-proxy limit in front for more.
_REGISTER_WINDOW_SECONDS = 3600
_REGISTER_MAX_PER_WINDOW = int(os.environ.get("REGISTER_MAX_PER_IP_PER_HOUR", "5"))
_register_hits: dict[str, list[float]] = {}


def _register_throttle(request: Request | None) -> None:
    import time

    if request is None or _REGISTER_MAX_PER_WINDOW <= 0:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (forwarded.split(",")[0].strip() if forwarded else "") or (
        request.client.host if request.client else "unknown"
    )
    now = time.monotonic()
    hits = [t for t in _register_hits.get(ip, []) if now - t < _REGISTER_WINDOW_SECONDS]
    if len(hits) >= _REGISTER_MAX_PER_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_t("auth.register_throttled"),
        )
    hits.append(now)
    _register_hits[ip] = hits


class ForgotRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ResetRequest(BaseModel):
    token: str = Field(min_length=10, max_length=2000)
    password: str = Field(min_length=1, max_length=200)


_forgot_hits: dict[str, list[float]] = {}


@router.post("/password/forgot")
async def forgot_password(body: ForgotRequest, request: Request = None) -> dict:
    """Email a reset link. The answer never reveals whether the address is registered."""
    import time

    if not AUTH_ENABLED or POCKETBASE_ENABLED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password reset is not available.")
    from pathmind.multi_user.identity import find_user_by_email
    from pathmind.services import notifications, password_reset

    email = body.email.strip().lower()
    now = time.monotonic()
    hits = [t for t in _forgot_hits.get(email, []) if now - t < 3600]
    if len(hits) >= 3:
        return {"ok": True}  # silently capped: no mail bombing, no enumeration
    _forgot_hits[email] = [*hits, now]
    found = find_user_by_email(email)
    if found is not None and found[1].get("email") and not found[1].get("disabled"):
        username, record = found
        base = str(request.headers.get("origin") or request.base_url).rstrip("/") if request else ""
        link = f"{base}/reset-password?token={password_reset.make_token(username, str(record.get('hash') or ''))}"
        if _email_verification.smtp_configured():
            notifications.send_email(
                str(record["email"]), "Reset your PathMind password",
                f"Open this link to choose a new password (valid for 1 hour):\n{link}\n\n"
                "If you did not ask for this, ignore this email.",
            )
        else:
            logger.warning("Password reset link for %s (SMTP is off): %s", username, link)
    return {"ok": True}


@router.post("/password/reset")
async def reset_password(body: ResetRequest) -> dict:
    from pathmind.multi_user.identity import get_user, set_password
    from pathmind.services import notifications, password_reset
    from pathmind.services.auth import hash_password

    parsed = password_reset.read_token(body.token)
    record = get_user(parsed[0]) if parsed else None
    if parsed is None or record is None or not password_reset.still_valid(parsed[1], str(record.get("hash") or "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has expired.")
    username = parsed[0]
    problem = _password_problem(body.password, username, str(record.get("email") or ""))
    if problem:
        raise _signup_error("password", problem)
    set_password(username, hash_password(body.password))
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.services import audit

        with get_db_session() as db:
            audit.record(db, str(record.get("id") or ""), "auth.password_reset", target_type="user",
                         target_id=str(record.get("id") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not audit password reset: %s", exc)
    notifications.send_email(
        str(record.get("email") or ""), "Your PathMind password was changed",
        "Your password was just changed. If this was not you, contact an administrator.",
    )
    return {"ok": True}


class InviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@router.post("/invite")
async def invite_rnd(
    body: InviteRequest,
    request: Request,
    payload: TokenPayload | None = Depends(require_admin),
) -> dict:
    """Administrator invites an R&D member by email (signed link, valid 7 days)."""
    from pathmind.multi_user.identity import find_user_by_email
    from pathmind.services import invites as _invites

    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise _signup_error("email", "auth.email_invalid")
    if find_user_by_email(email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")
    base = str(request.headers.get("origin") or request.base_url).rstrip("/")
    from pathmind.database.connection import get_db_session

    with get_db_session() as db:
        result = _invites.create(
            db, email, base, str(getattr(payload, "user_id", "") or ""),
            getattr(payload, "username", "An administrator") or "An administrator",
        )
    return {"ok": True, **result}


@router.get("/invitations")
async def list_invitations(payload: TokenPayload | None = Depends(require_admin)) -> dict:
    from pathmind.database.connection import get_db_session
    from pathmind.services import invites as _invites

    with get_db_session() as db:
        return {"items": _invites.list_all(db)}


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(invitation_id: str, payload: TokenPayload | None = Depends(require_admin)) -> dict:
    from pathmind.database.connection import get_db_session
    from pathmind.services import invites as _invites

    try:
        with get_db_session() as db:
            return _invites.revoke(db, str(getattr(payload, "user_id", "") or ""), invitation_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: SignupRequest, request: Request = None) -> dict:
    """
    Public self-registration (detailed sign-up).

    Requires full name, a unique email, a password with letters and digits,
    and acceptance of the Terms. The first account becomes admin. A 6-digit
    code is sent to the email (logged instead when SMTP is not configured);
    see ``pathmind.services.email_verification``.

    Only available when AUTH_ENABLED=true.
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — registration is not available.",
        )

    if POCKETBASE_ENABLED:
        # PocketBase deployments are documented as single-user. Keep registration
        # closed and require admins to provision users in the PocketBase admin UI.
        if not is_first_user():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Self-registration is closed. Ask an administrator to create your account.",
            )
        email = body.email.strip() or body.username.strip()
        result = register_pb(username=email, email=email, password=body.password)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Registration failed — username or email may already be taken.",
            )
        logger.info(f"First user registered via PocketBase: '{email}'")
        return {
            "ok": True,
            "user_id": result.get("id", ""),
            "username": email,
            "role": "user",
            "is_first_user": True,
            "is_admin": False,
        }

    username, email, full_name = _validate_signup(body)

    # Standard mode — allow registration for all users; first user gets admin
    first_user = is_first_user()
    if not first_user:
        _register_throttle(request)
    role = "admin" if first_user else ("educator" if body.role == "educator" else "user")
    invited = None
    if body.invite and not first_user:
        from pathmind.database.connection import get_db_session as _gdb
        from pathmind.services import invites as _invites

        with _gdb() as _db:
            _inv = _invites.lookup(_db, body.invite)
            invited = (
                {"id": _inv.id, "email": _inv.email, "role": _inv.role} if _inv is not None else None
            )
        if invited is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This invitation is invalid, used or expired.")
        if invited["email"] != email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use the email address the invitation was sent to.")
        role = invited["role"]

    add_user(
        username,
        body.password,
        role=role,
        profile={"full_name": full_name, "email": email, "email_verified": False},
    )
    info = get_user_info(username) or {}
    user_id = str(info.get("id") or "")
    role = str(info.get("role") or role)

    # Mirror to the relational DB and grant the default free plan.
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.database.models import Subscription
        from pathmind.services.quota_service import ensure_db_user

        with get_db_session() as db:
            db_user = ensure_db_user(db, user_id, username, role)
            if db_user is not None:
                db_user.full_name = full_name
                if not db.query(type(db_user)).filter_by(email=email).first():
                    db_user.email = email
                db_user.email_verified = False
                if not db.query(Subscription).filter_by(user_id=db_user.id).first():
                    db.add(
                        Subscription(
                            user_id=db_user.id,
                            plan_id="free",
                            status="active",
                            source="register",
                        )
                    )
                db.commit()
    except Exception as db_exc:
        logger.warning("Failed to sync new user to DB: %s", db_exc)

    delivery = "none"
    try:
        delivery = _email_verification.issue_code(user_id, email).get("delivery", "none")
    except Exception as exc:
        logger.warning("Could not issue email verification code: %s", exc)

    if invited is not None:  # receiving the invitation proves control of the address
        try:
            _email_verification.mark_verified(username, user_id)
            from pathmind.database.connection import get_db_session as _gdb2
            from pathmind.database.models import Invitation as _Invitation
            from pathmind.services import invites as _invites2

            with _gdb2() as _db2:
                _row = _db2.get(_Invitation, invited["id"])
                if _row is not None:
                    _invites2.mark_accepted(_db2, _row, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not finish accepting invitation: %s", exc)

    logger.info(f"User registered: '{username}' (role={role})")
    return {
        "ok": True,
        "user_id": user_id,
        "username": username,
        "email": email,
        "role": role,
        "is_first_user": first_user,
        "is_admin": role == "admin",
        "email_verification": {
            # Login is blocked until verified only when codes can be delivered.
            "required": _email_verification.verification_required() and role != "admin",
            "delivery": delivery,
        },
    }


# Public email-verification endpoints. Responses never reveal whether an
# email is registered (resend is always "ok"); guesses are capped per code.
@router.post("/email/verify")
async def verify_email(body: EmailCodeRequest) -> dict:
    from pathmind.multi_user.identity import find_user_by_email

    email = body.email.strip().lower()
    found = find_user_by_email(email)
    if found is not None and found[1].get("email_verified"):
        return {"ok": True, "already_verified": True}
    result = _email_verification.check_code(email, body.code)
    if result != "ok" or found is None:
        key = {
            "expired": "auth.code_expired",
            "too_many_attempts": "auth.code_too_many",
        }.get(result, "auth.code_invalid")
        raise _signup_error("code", key)
    username, record = found
    _email_verification.mark_verified(username, str(record.get("id") or ""))
    return {"ok": True}


@router.post("/email/resend")
async def resend_email_code(body: EmailOnlyRequest) -> dict:
    from pathmind.multi_user.identity import find_user_by_email

    email = body.email.strip().lower()
    found = find_user_by_email(email)
    if found is None or found[1].get("email_verified") or not found[1].get("email"):
        return {"ok": True, "retry_after": 0}
    outcome = _email_verification.issue_code(str(found[1].get("id") or ""), email)
    if outcome.get("retry_after"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "code_resend_wait",
                "field": "code",
                "retry_after": outcome["retry_after"],
                "message": _t("auth.code_resend_wait", seconds=outcome["retry_after"]),
            },
        )
    return {"ok": True, "retry_after": 0}


@router.get("/is_first_user")
async def check_is_first_user() -> dict:
    """Return whether the user store is empty (used by the register UI)."""
    return {"is_first_user": is_first_user() if AUTH_ENABLED else False}


# ---------------------------------------------------------------------------
# Profile endpoints (any authenticated user, self-service)
# ---------------------------------------------------------------------------

_AVATAR_MAX_BYTES = 1 * 1024 * 1024
_AVATAR_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def _sniff_image(data: bytes) -> str | None:
    """Detect a supported raster image format from its magic bytes.

    The uploaded filename and Content-Type are attacker-controlled, so the
    stored extension (and the media type served back) is derived from the
    bytes alone. SVG is deliberately unsupported — serving user-supplied SVG
    is a stored-XSS vector.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _require_profile_identity(payload: TokenPayload | None) -> TokenPayload:
    """Shared guard for the self-service profile endpoints."""
    if not AUTH_ENABLED or payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — profiles are not available.",
        )
    return payload


@router.get("/profile", response_model=UserInfo)
async def get_profile(
    payload: TokenPayload | None = Depends(require_auth),
) -> UserInfo:
    """Return the current user's own account info."""
    current = _require_profile_identity(payload)
    info = get_user_info(current.username)
    if info is None:
        # PocketBase-backed identities have no local record; fall back to the
        # token claims so the profile page still renders.
        return UserInfo(
            id=current.user_id,
            username=current.username,
            role=current.role,
            created_at="",
        )
    return UserInfo(**info)


@router.put("/profile")
async def update_profile(
    body: UpdateProfileRequest,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Update the current user's own avatar marker (icon choice or reset).

    Only the validated ``icon:<name>:<color>`` form (or empty string) is
    accepted here; ``img:`` markers are owned by the upload endpoint.
    """
    current = _require_profile_identity(payload)
    if not set_avatar(current.username, body.avatar):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # The marker no longer references an uploaded image, so drop the file.
    from pathmind.multi_user.identity import delete_avatar_file

    if current.user_id and _USER_ID_RE.match(current.user_id):
        delete_avatar_file(current.user_id)
    return {"ok": True, "avatar": body.avatar}


@router.put("/profile/details")
async def update_profile_details(
    body: UpdateProfileDetailsRequest,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Update the current user's display name."""
    from pathmind.multi_user.identity import update_profile as _update_identity_profile

    current = _require_profile_identity(payload)
    full_name = " ".join(body.full_name.split())
    if full_name and len(full_name) < 2:
        raise _signup_error("full_name", "auth.full_name_invalid")
    record = _update_identity_profile(current.username, full_name=full_name)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.database.models import User as DBUser

        with get_db_session() as db:
            row = db.get(DBUser, current.user_id)
            if row is not None:
                row.full_name = full_name or None
                db.commit()
    except Exception as exc:
        logger.debug("Profile mirror update failed: %s", exc)
    return {"ok": True, "full_name": full_name}


@router.post("/account/delete")
async def delete_own_account(
    body: DeleteAccountRequest,
    response: Response,
    request: Request = None,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Permanently delete the signed-in user's own account and data.

    Requires the current password and the word DELETE. See
    ``pathmind.services.account_deletion`` for exactly what is erased.
    """
    from pathmind.services.account_deletion import AccountDeletionError, delete_account

    current = _require_profile_identity(payload)
    if body.confirm.strip().upper() != "DELETE":
        raise _signup_error("confirm", "auth.delete_confirm_required")
    login_keys = _login_keys(request, current.username)
    _login_check_locked(login_keys)
    if authenticate(current.username, body.password) is None:
        _login_record_failure(login_keys)
        raise _signup_error("password", "auth.delete_wrong_password")
    try:
        delete_account(current.username, actor="self")
    except AccountDeletionError as exc:
        key = {
            "last_admin": "auth.delete_last_admin",
            "not_deletable": "auth.delete_not_allowed",
        }.get(exc.code, "auth.delete_failed")
        raise _signup_error("account", key, status.HTTP_409_CONFLICT) from exc
    response.delete_cookie(**_cookie_attrs())
    return {"ok": True}


@router.put("/profile/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Upload an avatar image for the current user.

    The client is expected to crop/resize before uploading; the server only
    enforces a size cap and validates the format by magic bytes. Not available
    in PocketBase mode (those identities have no local user record).
    """
    current = _require_profile_identity(payload)
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar upload is not available in PocketBase mode.",
        )
    if not current.user_id or not _USER_ID_RE.match(current.user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot store an avatar for this account.",
        )
    info = get_user_info(current.username)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    data = await file.read(_AVATAR_MAX_BYTES + 1)
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Avatar image is too large (max 1 MB).",
        )
    ext = _sniff_image(data)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Avatar must be a PNG, JPEG or WebP image.",
        )

    from pathmind.multi_user.identity import save_avatar_file

    # Bump the version embedded in the marker so clients cache-bust the URL.
    previous = str(info.get("avatar") or "")
    version = 1
    if previous.startswith("img:"):
        try:
            version = int(previous.split(":", 1)[1]) + 1
        except ValueError:
            version = 1
    marker = f"img:{version}"

    save_avatar_file(current.user_id, data, ext)
    if not set_avatar(current.username, marker):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    logger.info(f"User '{current.username}' uploaded a new avatar ({ext}, {len(data)} bytes)")
    return {"ok": True, "avatar": marker}


@router.delete("/profile/avatar")
async def remove_avatar(
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Remove the current user's uploaded avatar image and reset the marker."""
    current = _require_profile_identity(payload)
    from pathmind.multi_user.identity import delete_avatar_file

    if current.user_id and _USER_ID_RE.match(current.user_id):
        delete_avatar_file(current.user_id)
    set_avatar(current.username, "")
    return {"ok": True, "avatar": ""}


@router.get("/avatar/{user_id}")
async def get_avatar_image(
    user_id: str,
    _: TokenPayload | None = Depends(require_auth),
) -> FileResponse:
    """Serve a stored avatar image. Any authenticated user may view avatars
    (they appear in the admin table and next to the viewer's own profile)."""
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

    from pathmind.multi_user.identity import get_avatar_file

    target = get_avatar_file(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

    media_type = _AVATAR_MEDIA_TYPES.get(target.suffix.lstrip("."), "application/octet-stream")
    headers = {
        # Private user content; the marker version in the URL handles busting.
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    }
    return FileResponse(path=str(target), media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Admin-only endpoints
# ---------------------------------------------------------------------------


@router.get("/devices")
async def list_devices(
    user_id: str | None = None,
    include_revoked: bool = False,
    _: TokenPayload = Depends(require_admin),
) -> dict:
    """List local device credential metadata without credential secrets."""

    _require_builtin_device_auth()
    credentials = list_device_credentials(user_id=user_id, include_revoked=include_revoked)
    users = {str(user.get("id") or ""): str(user.get("username") or "") for user in list_users()}
    return {
        "devices": [
            {**device, "username": users.get(device["user_id"], "")} for device in credentials
        ]
    }


@router.post("/devices", status_code=status.HTTP_201_CREATED)
async def issue_device(
    body: DeviceCredentialCreateRequest,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Issue a revocable device credential for an ordinary local account."""

    _require_builtin_device_auth()
    if get_user_by_id(body.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    try:
        device, pairing_code, pin = issue_device_credential(
            user_id=body.user_id,
            device_name=body.device_name,
            expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days),
            daily_limit_minutes=body.daily_limit_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    log_admin_action(
        "device_credential_issue",
        target_user_id=body.user_id,
        summary={
            "device_credential_id": device["id"],
            "device_name": device["device_name"],
            "expires_at": device["expires_at"],
            "daily_limit_minutes": device["daily_limit_minutes"],
        },
    )
    logger.info(
        f"Admin '{current.username if current else 'local'}' issued device "
        f"credential {device['id']} for user id '{body.user_id}'"
    )
    return {
        "device": device,
        "pairing_code": pairing_code,
        "pin": pin,
    }


@router.delete("/devices/{device_credential_id}")
async def revoke_device(
    device_credential_id: str,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    _require_builtin_device_auth()
    device = revoke_device_credential(
        device_credential_id,
        revoked_by=str(current.user_id if current else ""),
    )
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device credential not found",
        )
    log_admin_action(
        "device_credential_revoke",
        target_user_id=device["user_id"],
        summary={"device_credential_id": device["id"]},
    )
    return {"device": device, "ok": True}


@router.get("/users", response_model=list[UserInfo])
async def get_users(_: TokenPayload = Depends(require_admin)) -> list[UserInfo]:
    """List all registered users. Requires admin role."""
    return [UserInfo(**u) for u in list_users()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def admin_create_user(
    body: AdminCreateUserRequest,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Admin-only: create a new user account.

    Replaces the public ``/register`` flow once the first admin exists. The
    new account is always created with role=``user``; admins can promote
    later via ``PUT /users/{username}/role``.
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — user creation is not available.",
        )

    if POCKETBASE_ENABLED:
        if body.preset != "standard":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only the standard preset is available in PocketBase mode.",
            )
        result = register_pb(username=body.username, email=body.username, password=body.password)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Failed to create user — username may already be taken.",
            )
        logger.info(
            f"Admin '{current.username if current else 'local'}' created PocketBase user "
            f"'{body.username}'"
        )
        return {
            "ok": True,
            "user_id": result.get("id", ""),
            "username": body.username,
            "role": "user",
            "is_admin": False,
            "preset": "standard",
        }

    existing = {u["username"] for u in list_users()}
    if body.username in existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    profile: dict = {}
    admin_email = (body.email or "").strip().lower()
    if admin_email:
        from pathmind.multi_user.identity import find_user_by_email

        if not _EMAIL_RE.match(admin_email):
            raise _signup_error("email", "auth.email_invalid")
        if find_user_by_email(admin_email) is not None:
            raise _signup_error("email", "auth.email_taken", status.HTTP_409_CONFLICT)
        # An address entered by an administrator counts as verified.
        profile.update(email=admin_email, email_verified=True)
    if (body.full_name or "").strip():
        profile["full_name"] = " ".join(body.full_name.split())

    add_user(body.username, body.password, preset=body.preset, profile=profile or None)
    user_id = ""
    role = "user"
    preset = "standard"
    for item in list_users():
        if item.get("username") == body.username:
            user_id = str(item.get("id") or "")
            role = str(item.get("role") or "user")
            preset = str(item.get("preset") or "standard")
            break
    if preset == "learner":
        from pathmind.multi_user.grants import learner_grant, save_grant

        try:
            save_grant(user_id, learner_grant(user_id))
        except Exception as exc:
            rolled_back = False
            try:
                rolled_back = delete_user(body.username)
            except Exception:
                logger.exception(
                    "Failed to roll back user '%s' after learner grant initialization failed",
                    body.username,
                )
            if not rolled_back:
                logger.error(
                    "Learner account '%s' may remain after grant initialization failed",
                    body.username,
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The learner preset could not be initialized.",
            ) from exc
    logger.info(
        f"Admin '{current.username if current else 'local'}' created user '{body.username}' "
        f"(role={role!r}, preset={preset!r})"
    )
    return {
        "ok": True,
        "user_id": user_id,
        "username": body.username,
        "role": role,
        "is_admin": role == "admin",
        "preset": preset,
    }


@router.post("/users/{username}/verify-email", status_code=status.HTTP_200_OK)
async def admin_verify_email(
    username: str,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Admin-only: mark a user's registered email as verified."""
    info = get_user_info(username)
    if info is None or not info.get("email"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _email_verification.mark_verified(username, str(info.get("id") or ""))
    log_admin_action("verify_email", target_user_id=str(info.get("id") or ""))
    return {"ok": True}


class SetDisabledRequest(BaseModel):
    disabled: bool


@router.put("/users/{username}/status", status_code=status.HTTP_200_OK)
async def set_user_status(
    username: str,
    body: SetDisabledRequest,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Admin-only: lock or unlock an account.

    Administrators can no longer delete accounts — only the account owner can
    (Settings → Profile → Delete account). Locking keeps all data, ends every
    active session immediately and blocks sign-in until unlocked.
    """
    from pathmind.multi_user.identity import set_disabled

    if current and username == current.username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_t("auth.cannot_lock_self"))
    info = get_user_info(username)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if body.disabled and info.get("role") == "admin":
        active_admins = [
            u for u in list_users() if u.get("role") == "admin" and not u.get("disabled")
        ]
        if len(active_admins) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=_t("auth.cannot_lock_last_admin")
            )
    if set_disabled(username, body.disabled) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user_id = str(info.get("id") or "")
    if body.disabled and user_id:
        try:
            from pathmind.multi_user.device_credentials import revoke_device_credentials_for_user

            revoke_device_credentials_for_user(user_id, revoked_by="account_locked")
        except Exception as exc:
            logger.warning("Could not revoke device credentials of %s: %s", user_id, exc)
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.database.models import User as DBUser

        with get_db_session() as db:
            row = db.get(DBUser, user_id)
            if row is not None:
                row.is_active = not body.disabled
                db.commit()
    except Exception as exc:
        logger.debug("DB mirror of account status failed: %s", exc)
    log_admin_action(
        "account_locked" if body.disabled else "account_unlocked",
        target_user_id=user_id,
    )
    return {"ok": True, "disabled": body.disabled}


@router.put("/users/{username}/role", status_code=status.HTTP_200_OK)
async def update_user_role(
    username: str,
    body: SetRoleRequest,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Change a user's role. Admins cannot change their own role."""
    if current and username == current.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role",
        )

    updated = set_role(username, body.role)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        from pathmind.database.connection import get_db_session
        from pathmind.services.quota_service import ensure_db_user

        info = get_user_info(username)
        if info and info.get("id"):
            with get_db_session() as db:
                ensure_db_user(db, str(info["id"]), username, body.role)
    except Exception as db_exc:
        logger.warning("Failed to mirror role change for '%s': %s", username, db_exc)

    logger.info(
        f"Admin '{current.username if current else 'local'}' set '{username}' role to {body.role!r}"
    )
    return {"ok": True, "username": username, "role": body.role}


@router.post("/role/educator", status_code=status.HTTP_200_OK)
async def become_educator(
    response: Response,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Self-service upgrade learner -> educator (no approval).

    Educator is a label for people who share knowledge; what it unlocks (a
    knowledge branch and contributions to R&D) is gated by the paid plan, so no
    review of the role itself is needed. Admins and R&D accounts keep their role.
    """
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    role = _live_role(payload)
    if role == "user":
        if not set_role(payload.username, "educator"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        role = "educator"
        try:
            from pathmind.database.connection import get_db_session
            from pathmind.services.quota_service import ensure_db_user

            with get_db_session() as db:
                ensure_db_user(db, payload.user_id, payload.username, role)
        except Exception as db_exc:  # noqa: BLE001
            logger.warning("Failed to mirror educator upgrade for '%s': %s", payload.username, db_exc)
        token = create_token(payload.username, role, payload.user_id)
        response.set_cookie(value=token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())
        logger.info(f"User '{payload.username}' became an educator")
    elif role != "educator":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Your account is already {role}.")
    return {"ok": True, "role": role}
