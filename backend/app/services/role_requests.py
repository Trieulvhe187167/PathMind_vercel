"""Becoming an educator: request at sign-up or later, approved or rejected by an admin."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RoleRequest, User, utcnow
from . import audit
from .app_settings import get_setting
from .notify import notify

REQUESTABLE = {"educator"}


def pending_for(db: Session, user_id: str) -> RoleRequest | None:
    return db.scalar(
        select(RoleRequest).where(RoleRequest.user_id == user_id, RoleRequest.status == "pending")
    )


def public(req: RoleRequest, user: User | None = None) -> dict:
    out = {
        "id": req.id, "requested_role": req.requested_role, "reason": req.reason, "status": req.status,
        "decision_note": req.decision_note,
        "created_at": req.created_at.isoformat() + "Z" if req.created_at else None,
        "decided_at": req.decided_at.isoformat() + "Z" if req.decided_at else None,
    }
    if user is not None:
        out["user"] = {"id": user.id, "username": user.username, "full_name": user.full_name,
                       "email": user.email, "role": user.role, "is_active": user.is_active}
    return out


def request_role(db: Session, user: User, role: str, reason: str, *, ip: str = "") -> tuple[str, RoleRequest | None]:
    """Ask for ``role``. Returns ("granted", None) when no approval is needed, else ("pending", request).

    Caller commits.
    """
    if role not in REQUESTABLE:
        raise HTTPException(status_code=422, detail={"field": "role", "message": "Only the educator role can be requested."})
    settings = get_setting(db, "registration")
    if not settings["allow_educator_signup"]:
        raise HTTPException(status_code=403, detail={"field": "role", "message": "Educator sign-up is turned off. Ask an administrator."})
    if user.role != "learner":
        raise HTTPException(status_code=409, detail={"field": "role", "message": f"Your account is already {user.role}."})
    if pending_for(db, user.id):
        raise HTTPException(status_code=409, detail={"field": "role", "message": "You already have a request waiting for review."})
    reason = (reason or "").strip()[:1000]
    if not settings["educator_requires_approval"]:
        user.role = role
        audit.record(db, user.id, "user.role_self_granted", target_type="user", target_id=user.id,
                     data={"role": role}, ip=ip)
        return "granted", None
    req = RoleRequest(user_id=user.id, requested_role=role, reason=reason)
    db.add(req)
    audit.record(db, user.id, "role_request.create", target_type="user", target_id=user.id,
                 data={"role": role}, ip=ip)
    return "pending", req


def decide(db: Session, admin: User, req: RoleRequest, approve: bool, note: str, *, ip: str = "") -> None:
    """Approve or reject a pending request. Caller commits."""
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="This request was already decided.")
    user = db.get(User, req.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    note = (note or "").strip()[:500]
    if not approve and not note:
        raise HTTPException(status_code=422, detail={"field": "note", "message": "Say why the request is rejected."})
    if approve and not user.is_active:
        raise HTTPException(status_code=409, detail="The account is deactivated; activate it first.")
    req.status = "approved" if approve else "rejected"
    req.decided_by, req.decided_at, req.decision_note = admin.id, utcnow(), note
    if approve and user.role == "learner":  # an admin may already have changed the role by hand
        user.role = req.requested_role
    audit.record(db, admin.id, f"role_request.{req.status}", target_type="user", target_id=user.id,
                 data={"request_id": req.id, "role": req.requested_role, "note": note}, ip=ip)
    notify(db, user.id, f"role_request_{req.status}",
           "Your educator request was approved." if approve else f"Your educator request was not approved: {note}",
           actor_id=admin.id, target_type="role_request", target_id=req.id)


def close_on_role_change(db: Session, admin: User, user: User) -> None:
    """When an admin sets a role by hand, settle the user's pending request to match. Caller commits."""
    req = pending_for(db, user.id)
    if req is None:
        return
    req.status = "approved" if user.role == req.requested_role else "canceled"
    req.decided_by, req.decided_at = admin.id, utcnow()
    req.decision_note = "Role changed by an administrator."
