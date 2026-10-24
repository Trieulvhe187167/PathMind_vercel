"""Friends: requests, accept / reject / block, list, remove.

Mounted at ``/api/friends`` (auth required on the whole router).

Accounts live in the auth store (``services.auth``); the relational
``users`` table is a mirror, so every user touched here is mirrored on demand
with ``quota_service.ensure_db_user``. A friendship row is directional:
``user_id`` sent the request, ``friend_id`` received it.

Blocking: the receiver may block a request. Only the *blocker* can later remove
that row (= unblock); the blocked user cannot delete it to try again.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from pathmind.database.connection import get_db
from pathmind.database.models import Friendship, User
from pathmind.multi_user.context import get_current_user
from pathmind.services.i18n import t as _t
from pathmind.services.quota_service import ensure_db_user

logger = logging.getLogger(__name__)

router = APIRouter()


class FriendRequestBody(BaseModel):
    target: str = Field(min_length=1, max_length=255)  # username or email


class FriendRespondBody(BaseModel):
    request_id: str
    action: Literal["accept", "reject", "block"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str:
    return value.isoformat() + "Z" if value else ""


def _auth_users() -> list[dict]:
    from pathmind.services.auth import list_users

    try:
        return list_users()
    except Exception as exc:  # pragma: no cover
        logger.warning("Auth store unavailable: %s", exc)
        return []


def _users_by_id() -> dict[str, dict]:
    return {str(u.get("id") or ""): u for u in _auth_users()}


def _me(db: Session) -> str:
    user = get_current_user()
    ensure_db_user(db, user.id, user.username, user.role)
    return user.id


def _pair(db: Session, a: str, b: str) -> Friendship | None:
    return (
        db.query(Friendship)
        .filter(
            or_(
                (Friendship.user_id == a) & (Friendship.friend_id == b),
                (Friendship.user_id == b) & (Friendship.friend_id == a),
            )
        )
        .first()
    )


def are_friends(db: Session, a: str, b: str) -> bool:
    row = _pair(db, a, b)
    return bool(row and row.status == "accepted")


def _resolve_target(db: Session, query: str) -> tuple[str, str]:
    """(user_id, username) for a username (case-insensitive) or email."""
    q = query.strip()
    for u in _auth_users():
        if str(u.get("username") or "").lower() == q.lower():
            uid = str(u.get("id") or "")
            ensure_db_user(db, uid, str(u.get("username")), str(u.get("role") or "user"))
            return uid, str(u.get("username"))
    row = db.query(User).filter(User.email == q).first()
    if row:
        return row.id, row.username
    raise HTTPException(status_code=404, detail=_t("social.user_not_found"))


@router.get("/list")
async def list_friends(db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    rows = (
        db.query(Friendship)
        .filter(
            or_(Friendship.user_id == me, Friendship.friend_id == me),
            Friendship.status == "accepted",
        )
        .all()
    )
    users = _users_by_id()
    friends = []
    for row in rows:
        other = row.friend_id if row.user_id == me else row.user_id
        info = users.get(other)
        if info is None:
            continue  # account deleted
        friends.append(
            {
                "friendship_id": row.id,
                "user_id": other,
                "username": info.get("username"),
                "role": info.get("role") or "user",
                "avatar": info.get("avatar") or "",
                "created_at": _iso(row.updated_at or row.created_at),
                "status": row.status,
            }
        )
    friends.sort(key=lambda f: str(f["username"]).lower())
    return {"friends": friends}


@router.get("/pending")
async def pending_requests(db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    users = _users_by_id()
    incoming = db.query(Friendship).filter_by(friend_id=me, status="pending").all()
    outgoing = db.query(Friendship).filter_by(user_id=me, status="pending").all()
    blocked = db.query(Friendship).filter_by(friend_id=me, status="blocked").all()
    return {
        "pending_requests": [
            {
                "request_id": r.id,
                "requester_id": r.user_id,
                "requester_username": users.get(r.user_id, {}).get("username", _t("social.deleted_user")),
                "created_at": _iso(r.created_at),
            }
            for r in incoming
        ],
        "sent_requests": [
            {
                "request_id": r.id,
                "target_id": r.friend_id,
                "target_username": users.get(r.friend_id, {}).get("username", _t("social.deleted_user")),
                "created_at": _iso(r.created_at),
            }
            for r in outgoing
        ],
        "blocked": [
            {
                "request_id": r.id,
                "user_id": r.user_id,
                "username": users.get(r.user_id, {}).get("username", _t("social.deleted_user")),
            }
            for r in blocked
        ],
    }


@router.post("/request")
async def send_friend_request(body: FriendRequestBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    target_id, target_name = _resolve_target(db, body.target)
    if target_id == me:
        raise HTTPException(status_code=400, detail=_t("social.cannot_friend_self"))

    existing = _pair(db, me, target_id)
    if existing:
        if existing.status == "accepted":
            raise HTTPException(status_code=409, detail=_t("social.already_friends"))
        if existing.status == "blocked":
            raise HTTPException(status_code=403, detail=_t("social.cannot_request"))
        if existing.status == "pending" and existing.user_id == me:
            raise HTTPException(status_code=409, detail=_t("social.request_already_sent"))
        if existing.status == "pending" and existing.friend_id == me:
            # They already invited us: sending back means yes.
            existing.status = "accepted"
            existing.updated_at = _now()
            db.commit()
            return {"status": "accepted", "message": _t("social.now_friends", name=target_name)}

    db.add(Friendship(user_id=me, friend_id=target_id, status="pending"))
    db.commit()
    return {"status": "pending", "message": _t("social.request_sent", name=target_name)}


@router.post("/respond")
async def respond_to_request(body: FriendRespondBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    row = db.query(Friendship).filter_by(id=body.request_id, friend_id=me, status="pending").first()
    if not row:
        raise HTTPException(status_code=404, detail=_t("social.request_missing"))
    if body.action == "accept":
        row.status = "accepted"
        row.updated_at = _now()
    elif body.action == "reject":
        db.delete(row)
    else:
        row.status = "blocked"
        row.updated_at = _now()
    db.commit()
    return {"status": "ok", "action": body.action}


@router.delete("/requests/{request_id}")
async def cancel_or_unblock(request_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Cancel a request I sent, or lift a block I placed."""
    me = _me(db)
    row = db.get(Friendship, request_id)
    if not row:
        raise HTTPException(status_code=404, detail=_t("social.request_not_found"))
    if row.status == "pending" and row.user_id == me:
        db.delete(row)
    elif row.status == "blocked" and row.friend_id == me:
        db.delete(row)
    else:
        raise HTTPException(status_code=403, detail=_t("social.request_forbidden"))
    db.commit()
    return {"status": "ok"}


@router.delete("/{friend_user_id}")
async def remove_friend(friend_user_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    row = _pair(db, me, friend_user_id)
    if not row or row.status != "accepted":
        raise HTTPException(status_code=404, detail=_t("social.not_friends"))
    db.delete(row)
    # Partners shared between the two stop being shared.
    from pathmind.database.models import PartnerShare

    db.query(PartnerShare).filter(
        or_(
            (PartnerShare.owner_id == me) & (PartnerShare.shared_with_id == friend_user_id),
            (PartnerShare.owner_id == friend_user_id) & (PartnerShare.shared_with_id == me),
        )
    ).delete(synchronize_session=False)
    db.commit()
    return {"status": "ok", "message": _t("social.unfriended")}
