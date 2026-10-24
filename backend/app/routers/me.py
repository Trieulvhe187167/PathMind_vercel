"""Things about the signed-in person: profile, devices, educator request, notifications, account deletion."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import AuthSession, BackgroundTask, Notification, StoredFile, User, utcnow
from ..security import verify_password
from ..services import account_deletion, audit, mailer, role_requests, verification
from .auth import _client_ip

router = APIRouter()


class RoleRequestBody(BaseModel):
    role: str = "educator"
    reason: str = Field(default="", max_length=1000)


class ReadBody(BaseModel):
    ids: list[str] | None = None  # None = mark everything read


@router.get("/role-request")
def my_role_request(user: User = Depends(current_user), db: Session = Depends(get_db)):
    req = role_requests.pending_for(db, user.id)
    return {"role_request": role_requests.public(req) if req else None}


@router.post("/role-request", status_code=201)
def create_role_request(
    body: RoleRequestBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    # Same lock as sign-up and admin decisions, so two quick clicks cannot leave two pending requests.
    with request.app.state.accounts_lock:
        db.refresh(user)  # an admin may have changed the role while we waited
        outcome, req = role_requests.request_role(db, user, body.role, body.reason, ip=_client_ip(request))
        db.commit()
    return {"outcome": outcome, "user": user.public(), "role_request": role_requests.public(req) if req else None}


@router.delete("/role-request")
def cancel_role_request(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with request.app.state.accounts_lock:
        req = role_requests.pending_for(db, user.id)
        if req is None:
            raise HTTPException(status_code=404, detail="No pending request.")
        req.status, req.decided_at = "canceled", utcnow()
        audit.record(db, user.id, "role_request.cancel", target_type="user", target_id=user.id,
                     data={"request_id": req.id}, ip=_client_ip(request))
        db.commit()
    return {"ok": True}


@router.get("/notifications")
def my_notifications(limit: int = 20, user: User = Depends(current_user), db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = db.scalars(
        select(Notification).where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc()).limit(limit)
    ).all()
    unread = db.scalar(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    ) or 0
    return {
        "unread": unread,
        "notifications": [
            {"id": n.id, "kind": n.kind, "preview": n.preview, "target_type": n.target_type,
             "target_id": n.target_id, "read": n.read_at is not None,
             "created_at": n.created_at.isoformat() + "Z" if n.created_at else None}
            for n in rows
        ],
    }


@router.post("/notifications/read")
def mark_read(body: ReadBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if body.ids is not None:
        stmt = stmt.where(Notification.id.in_(body.ids[:200]))
    db.execute(stmt.values(read_at=utcnow()))
    db.commit()
    return {"ok": True}


# ── Devices (sign-in sessions) ────────────────────────────────────────────────


def _session_public(s: AuthSession, current: str | None) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {"id": s.id, "ip": s.ip, "user_agent": s.user_agent, "created_at": iso(s.created_at),
            "last_seen_at": iso(s.last_seen_at), "expires_at": iso(s.expires_at), "current": s.id == current}


@router.get("/sessions")
def my_sessions(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = utcnow()
    rows = db.scalars(select(AuthSession).where(
        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
        .order_by(AuthSession.last_seen_at.desc())).all()
    current = getattr(request.state, "session_id", None)
    return {"sessions": [_session_public(s, current) for s in rows]}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(AuthSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    s.revoked_at = s.revoked_at or utcnow()
    db.commit()
    return {"ok": True}


@router.post("/sessions/revoke-others")
def revoke_others(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    current = getattr(request.state, "session_id", None)
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None),
                                         AuthSession.id != current).values(revoked_at=utcnow()))
    db.commit()
    return {"ok": True}


# ── Profile ───────────────────────────────────────────────────────────────────


class ProfileBody(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    locale: Literal["vi", "en", "zh"] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    theme: Literal["light", "dark"] | None = None
    avatar_file_id: str | None = None  # "" removes the avatar


@router.patch("/profile")
def update_profile(body: ProfileBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.locale is not None:
        user.locale = body.locale
    if body.timezone is not None:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(body.timezone)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="Unknown time zone.") from None
        user.timezone = body.timezone
    if body.theme is not None:
        user.theme = body.theme
    if body.avatar_file_id is not None:
        if body.avatar_file_id == "":
            user.avatar_url = ""
        else:
            f = db.get(StoredFile, body.avatar_file_id)
            if f is None or f.owner_id != user.id or f.deleted_at is not None or not f.mime_type.startswith("image/"):
                raise HTTPException(status_code=422, detail="Upload an image first.")
            f.purpose = "avatar"
            user.avatar_url = f"/api/files/{f.id}/download?inline=true"
    db.commit()
    return {"user": user.public()}


class EmailBody(BaseModel):
    email: EmailStr | None = None  # None removes it
    password: str = ""


@router.put("/email")
def change_email(body: EmailBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.password_hash and not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=400, detail={"field": "password", "message": "Password is wrong."})
    email = str(body.email).strip().lower() if body.email else None
    with request.app.state.accounts_lock:
        if email and email != user.email and db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(status_code=409, detail={"field": "email", "message": "That email is already registered."})
        if email != user.email:
            user.email, user.email_verified_at = email, None
            audit.record(db, user.id, "user.email_change", target_type="user", target_id=user.id, ip=_client_ip(request))
        db.commit()
    if email and mailer.configured(db):
        verification.issue(db, user, "verify_email")
    return {"user": user.public()}


# ── Delete my account ────────────────────────────────────────────────────────


class DeleteBody(BaseModel):
    password: str = ""
    confirm: str


@router.post("/delete")
def delete_account(body: DeleteBody, request: Request, response: Response, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    if body.confirm != "DELETE":
        raise HTTPException(status_code=422, detail={"field": "confirm", "message": 'Type DELETE to confirm.'})
    if user.password_hash and not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=400, detail={"field": "password", "message": "Password is wrong."})
    with request.app.state.accounts_lock:
        db.refresh(user)
        if user.role == "admin":
            admins = db.scalar(select(func.count()).select_from(User).where(
                User.role == "admin", User.is_active.is_(True), User.id != user.id)) or 0
            if admins == 0:
                raise HTTPException(status_code=409, detail="You are the last administrator. Make someone else admin first.")
        account_deletion.delete_account(db, request.app.state.settings.data_dir, user, ip=_client_ip(request))
        db.commit()
    settings = request.app.state.settings
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/tasks/{task_id}")
def my_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import tasks

    t = db.get(BackgroundTask, task_id)
    if t is None or (t.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"task": tasks.public(t)}
