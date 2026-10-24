"""Administrators: educator requests and the registration settings."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import RoleRequest, User
from ..services import audit, role_requests
from .auth import _client_ip

router = APIRouter()


class DecisionBody(BaseModel):
    note: str = Field(default="", max_length=500)


@router.get("/role-requests")
def list_requests(
    status: Literal["pending", "approved", "rejected", "canceled", "all"] = "pending",
    db: Session = Depends(get_db), _: User = Depends(require_admin),
):
    stmt = select(RoleRequest, User).join(User, User.id == RoleRequest.user_id)
    if status != "all":
        stmt = stmt.where(RoleRequest.status == status)
    stmt = stmt.order_by(RoleRequest.created_at.desc() if status != "pending" else RoleRequest.created_at)
    return {"role_requests": [role_requests.public(r, u) for r, u in db.execute(stmt).all()]}


def _still_admin(db: Session, admin: User) -> None:
    db.refresh(admin)  # another admin may have demoted us while we waited for the lock
    if admin.role != "admin" or not admin.is_active:
        raise HTTPException(status_code=403, detail="Administrators only.")


def _decide(request_id: str, approve: bool, body: DecisionBody, request: Request, db: Session, admin: User):
    with request.app.state.accounts_lock:
        _still_admin(db, admin)
        req = db.get(RoleRequest, request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="Request not found.")
        role_requests.decide(db, admin, req, approve, body.note, ip=_client_ip(request))
        db.commit()
        return {"role_request": role_requests.public(req, db.get(User, req.user_id))}


@router.post("/role-requests/{request_id}/approve")
def approve(request_id: str, body: DecisionBody, request: Request, db: Session = Depends(get_db),
            admin: User = Depends(require_admin)):
    return _decide(request_id, True, body, request, db, admin)


@router.post("/role-requests/{request_id}/reject")
def reject(request_id: str, body: DecisionBody, request: Request, db: Session = Depends(get_db),
           admin: User = Depends(require_admin)):
    return _decide(request_id, False, body, request, db, admin)
