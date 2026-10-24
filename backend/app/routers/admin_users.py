"""Administrators: list accounts, change role, activate / deactivate."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import ROLES, User
from ..services import audit, role_requests
from .auth import _client_ip

router = APIRouter()


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None


def _active_admins(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    ) or 0


@router.get("/users")
def list_users(q: str = "", db: Session = Depends(get_db), _: User = Depends(require_admin)):
    stmt = select(User).order_by(User.created_at)
    if q.strip():
        # Escape LIKE wildcards: "_" is common in usernames and must match literally.
        term = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{term}%"
        stmt = stmt.where(or_(
            func.lower(User.username).like(like, escape="\\"), func.lower(User.email).like(like, escape="\\"),
            func.lower(User.full_name).like(like, escape="\\"),
        ))
    return {"users": [u.public() for u in db.scalars(stmt)]}


@router.patch("/users/{user_id}")
def update_user(
    user_id: str, body: UserPatch, request: Request, db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # Held across check and commit so two admins demoting each other cannot both pass the guard.
    with request.app.state.accounts_lock:
        return _apply_patch(db, admin, user_id, body, _client_ip(request))


def _apply_patch(db: Session, admin: User, user_id: str, body: UserPatch, ip: str = "") -> dict:
    db.refresh(admin)  # another admin may have demoted us while we waited for the lock
    if admin.role != "admin" or not admin.is_active:
        raise HTTPException(status_code=403, detail="Administrators only.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(status_code=422, detail=f"Role must be one of: {', '.join(ROLES)}.")
    losing_admin = user.role == "admin" and user.is_active and (
        (body.role is not None and body.role != "admin") or body.is_active is False
    )
    if losing_admin and user.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot remove your own administrator access.")
    if losing_admin and _active_admins(db) <= 1:
        raise HTTPException(status_code=409, detail="At least one active administrator must remain.")
    if body.role is not None and body.role != user.role:
        audit.record(db, admin.id, "user.role_change", target_type="user", target_id=user.id,
                     data={"from": user.role, "to": body.role}, ip=ip)
        user.role = body.role
        role_requests.close_on_role_change(db, admin, user)
    if body.is_active is not None and body.is_active != user.is_active:
        audit.record(db, admin.id, "user.activate" if body.is_active else "user.deactivate",
                     target_type="user", target_id=user.id, ip=ip)
        user.is_active = body.is_active
        if not body.is_active:
            user.token_version += 1  # sign the account out everywhere
    db.commit()
    return {"user": user.public()}
