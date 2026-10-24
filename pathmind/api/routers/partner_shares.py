"""Social features around Partners: share with friends, and study rooms.

Mounted at ``/api/social`` (auth required on the whole router). It used to
share ``/api/partners`` with the main partners router, where its routes could
collide with ``/api/partners/{partner_id}``.

Access model (enforced in ``multi_user.partner_access``):

* Sharing a partner gives the friend **use** access (chat with it). Only the
  partner's manager (its owner, or an admin) can share, and only with an
  accepted friend. Managing (soul, channels, deletion) is never shared.
* A study room may be bound to a partner the host manages. Every member of an
  active room gets **use** access to that partner while they are in the room.
  Rooms also have a shared chat (``RoomMessage``): every member sees every
  line, polled with ``GET /rooms/{code}/messages?after=<id>``. A member can
  ask the room's Partner in that chat; the Partner answers publicly and the
  credits are charged to the member who asked.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import string
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.api.routers.friends import are_friends
from pathmind.database.connection import get_db
from pathmind.database.models import PartnerShare, RoomMember, RoomMessage, SharedRoom
from pathmind.multi_user.context import get_current_user
from pathmind.services.i18n import t as _t
from pathmind.services.quota_service import ensure_db_user

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_ROOM_MEMBERS = 30
_CODE_ALPHABET = string.ascii_uppercase + string.digits


class ShareBody(BaseModel):
    target_user_id: str = Field(min_length=1, max_length=64)


class CreateRoomBody(BaseModel):
    room_name: str = Field(min_length=1, max_length=120)
    partner_id: str | None = None


def _me(db: Session):
    user = get_current_user()
    ensure_db_user(db, user.id, user.username, user.role)
    return user


def _usernames() -> dict[str, str]:
    from pathmind.services.auth import list_users

    try:
        return {str(u.get("id") or ""): str(u.get("username") or "") for u in list_users()}
    except Exception:
        return {}


def _partner_names() -> dict[str, str]:
    try:
        from pathmind.services.partners import get_partner_manager

        return {
            str(p.get("partner_id") or ""): str(p.get("name") or p.get("partner_id") or "")
            for p in get_partner_manager().list_partners()
        }
    except Exception:
        return {}


def _iso(value) -> str:
    return value.isoformat() + "Z" if value else ""


def _require_manageable(partner_id: str) -> None:
    from pathmind.multi_user.partner_access import can_manage_partner

    if partner_id not in _partner_names():
        raise HTTPException(status_code=404, detail=_t("social.partner_not_found"))
    if not can_manage_partner(partner_id):
        raise HTTPException(status_code=403, detail=_t("social.partner_not_yours"))


# ---------------------------------------------------------------------------
# Partner sharing
# ---------------------------------------------------------------------------


@router.post("/partners/{partner_id}/share")
async def share_partner(partner_id: str, body: ShareBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    _require_manageable(partner_id)
    if body.target_user_id == me.id:
        raise HTTPException(status_code=400, detail=_t("social.share_self"))
    if not are_friends(db, me.id, body.target_user_id):
        raise HTTPException(status_code=403, detail=_t("social.share_friends_only"))
    ensure_db_user(db, body.target_user_id)
    existing = (
        db.query(PartnerShare)
        .filter_by(partner_id=partner_id, owner_id=me.id, shared_with_id=body.target_user_id)
        .first()
    )
    if not existing:
        db.add(
            PartnerShare(
                partner_id=partner_id,
                owner_id=me.id,
                shared_with_id=body.target_user_id,
                permission="use",
            )
        )
        db.commit()
    name = _usernames().get(body.target_user_id) or _t("social.your_friend")
    return {"status": "ok", "message": _t("social.shared_with", name=name)}


@router.get("/partners/shared-with-me")
async def shared_with_me(db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    names, partners = _usernames(), _partner_names()
    rows = db.query(PartnerShare).filter_by(shared_with_id=me.id).all()
    return {
        "shared_partners": [
            {
                "share_id": r.id,
                "partner_id": r.partner_id,
                "partner_name": partners.get(r.partner_id, r.partner_id),
                "owner_id": r.owner_id,
                "owner_username": names.get(r.owner_id, ""),
                "permission": r.permission or "use",
                "shared_at": _iso(r.created_at),
            }
            for r in rows
            if r.partner_id in partners  # partner deleted → hide
        ]
    }


@router.get("/partners/{partner_id}/shares")
async def list_partner_shares(partner_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _me(db)
    _require_manageable(partner_id)
    names = _usernames()
    rows = db.query(PartnerShare).filter_by(partner_id=partner_id).all()
    return {
        "shares": [
            {
                "share_id": r.id,
                "user_id": r.shared_with_id,
                "username": names.get(r.shared_with_id, _t("social.deleted_user")),
                "shared_at": _iso(r.created_at),
            }
            for r in rows
        ]
    }


@router.delete("/shares/{share_id}")
async def revoke_share(share_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The owner revokes it, or the recipient removes it from their list."""
    me = _me(db)
    row = db.get(PartnerShare, share_id)
    if not row:
        raise HTTPException(status_code=404, detail=_t("social.share_not_found"))
    if me.id not in {row.owner_id, row.shared_with_id} and not me.is_admin:
        raise HTTPException(status_code=403, detail=_t("social.share_forbidden"))
    db.delete(row)
    db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Study rooms
# ---------------------------------------------------------------------------


def _new_code(db: Session) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if not db.query(SharedRoom).filter_by(room_code=code).first():
            return code
    raise HTTPException(status_code=500, detail=_t("social.room_code_failed"))


def _room_or_404(db: Session, code: str) -> SharedRoom:
    room = (
        db.query(SharedRoom)
        .filter(SharedRoom.room_code == code.strip().upper(), SharedRoom.is_active.is_(True))
        .first()
    )
    if not room:
        raise HTTPException(status_code=404, detail=_t("social.room_not_found"))
    return room


def _member_room_or_403(db: Session, code: str, user_id: str) -> SharedRoom:
    room = _room_or_404(db, code)
    if not db.query(RoomMember).filter_by(room_id=room.id, user_id=user_id).first():
        raise HTTPException(status_code=403, detail=_t("social.room_not_member"))
    return room


def _room_view(db: Session, room: SharedRoom, me_id: str) -> dict[str, Any]:
    names, partners = _usernames(), _partner_names()
    members = db.query(RoomMember).filter_by(room_id=room.id).all()
    return {
        "room_code": room.room_code,
        "room_name": room.title,
        "title": room.title,
        "host_id": room.host_id,
        "host_username": names.get(room.host_id, ""),
        "is_host": room.host_id == me_id,
        "partner_id": room.partner_id,
        "partner_name": partners.get(room.partner_id or "", "") if room.partner_id else "",
        "is_active": bool(room.is_active),
        "created_at": _iso(room.created_at),
        "members": [
            {
                "user_id": m.user_id,
                "username": names.get(m.user_id, _t("social.deleted_user")),
                "role": m.role,
                "joined_at": _iso(m.joined_at),
            }
            for m in members
        ],
    }


@router.post("/rooms")
async def create_room(body: CreateRoomBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    partner_id = (body.partner_id or "").strip() or None
    if partner_id:
        _require_manageable(partner_id)
    room = SharedRoom(
        room_code=_new_code(db),
        title=body.room_name.strip(),
        host_id=me.id,
        partner_id=partner_id,
        discussion_mode="round_robin",
        is_active=True,
    )
    db.add(room)
    db.flush()
    db.add(RoomMember(room_id=room.id, user_id=me.id, role="host"))
    db.commit()
    return _room_view(db, room, me.id)


@router.get("/rooms")
async def my_rooms(db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    room_ids = [m.room_id for m in db.query(RoomMember).filter_by(user_id=me.id).all()]
    rooms = (
        db.query(SharedRoom)
        .filter(SharedRoom.id.in_(room_ids), SharedRoom.is_active.is_(True))
        .order_by(SharedRoom.created_at.desc())
        .all()
        if room_ids
        else []
    )
    return {"rooms": [_room_view(db, r, me.id) for r in rooms]}


@router.get("/rooms/{room_code}")
async def get_room(room_code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    room = _member_room_or_403(db, room_code, me.id)
    return _room_view(db, room, me.id)


@router.post("/rooms/{room_code}/join")
async def join_room(room_code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    room = _room_or_404(db, room_code)
    if not db.query(RoomMember).filter_by(room_id=room.id, user_id=me.id).first():
        if db.query(RoomMember).filter_by(room_id=room.id).count() >= MAX_ROOM_MEMBERS:
            raise HTTPException(status_code=409, detail=_t("social.room_full"))
        db.add(RoomMember(room_id=room.id, user_id=me.id, role="participant"))
        _add_message(db, room, role="system", content=_t("social.room_joined", name=me.username))
        db.commit()
    return _room_view(db, room, me.id)


@router.post("/rooms/{room_code}/leave")
async def leave_room(room_code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Leave a room. When the host leaves, the room is closed."""
    me = _me(db)
    room = _room_or_404(db, room_code)
    if room.host_id == me.id:
        room.is_active = False
    elif db.query(RoomMember).filter_by(room_id=room.id, user_id=me.id).delete():
        _add_message(db, room, role="system", content=_t("social.room_left", name=me.username))
    db.commit()
    return {"status": "ok", "closed": room.host_id == me.id}


@router.delete("/rooms/{room_code}")
async def close_room(room_code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    room = _room_or_404(db, room_code)
    if room.host_id != me.id and not me.is_admin:
        raise HTTPException(status_code=403, detail=_t("social.room_host_only_close"))
    room.is_active = False
    db.commit()
    return {"status": "ok"}


@router.delete("/rooms/{room_code}/members/{user_id}")
async def remove_member(room_code: str, user_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    me = _me(db)
    room = _room_or_404(db, room_code)
    if room.host_id != me.id:
        raise HTTPException(status_code=403, detail=_t("social.room_host_only_remove"))
    if user_id == me.id:
        raise HTTPException(status_code=400, detail=_t("social.room_remove_self"))
    db.query(RoomMember).filter_by(room_id=room.id, user_id=user_id).delete()
    db.commit()
    return _room_view(db, room, me.id)


# ---------------------------------------------------------------------------
# Room chat
# ---------------------------------------------------------------------------

MAX_MESSAGE_CHARS = 4000
PARTNER_CONTEXT_MESSAGES = 30
PARTNER_TURN_TIMEOUT_S = 240
_partner_locks: dict[str, asyncio.Lock] = {}


class RoomMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    ask_partner: bool = False


def _add_message(
    db: Session, room: SharedRoom, *, role: str, content: str, user_id: str | None = None
) -> RoomMessage:
    msg = RoomMessage(room_id=room.id, user_id=user_id, role=role, content=content)
    db.add(msg)
    db.flush()
    return msg


def _message_view(msg: RoomMessage, names: dict[str, str], partner_name: str) -> dict[str, Any]:
    if msg.role == "user":
        author = names.get(msg.user_id or "", "") or _t("social.deleted_user")
    elif msg.role == "partner":
        author = partner_name or "Partner"
    else:
        author = ""
    return {
        "id": msg.id,
        "role": msg.role,
        "user_id": msg.user_id,
        "author": author,
        "content": msg.content,
        "created_at": _iso(msg.created_at),
    }


@router.get("/rooms/{room_code}/messages")
async def list_room_messages(
    room_code: str, after: int = 0, limit: int = 100, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Messages newer than ``after`` (oldest first). ``after=0`` → the latest ``limit``."""
    me = _me(db)
    room = _member_room_or_403(db, room_code, me.id)
    limit = max(1, min(int(limit or 100), 200))
    query = db.query(RoomMessage).filter(RoomMessage.room_id == room.id)
    if after > 0:
        rows = query.filter(RoomMessage.id > after).order_by(RoomMessage.id.asc()).limit(limit).all()
    else:
        rows = list(reversed(query.order_by(RoomMessage.id.desc()).limit(limit).all()))
    names, partner_name = _usernames(), _partner_names().get(room.partner_id or "", "")
    return {
        "messages": [_message_view(m, names, partner_name) for m in rows],
        "partner_busy": bool(_partner_locks.get(room.id) and _partner_locks[room.id].locked()),
    }


def _partner_context(db: Session, room: SharedRoom, names: dict[str, str], partner_name: str) -> str:
    rows = list(
        reversed(
            db.query(RoomMessage)
            .filter(RoomMessage.room_id == room.id, RoomMessage.role != "system")
            .order_by(RoomMessage.id.desc())
            .limit(PARTNER_CONTEXT_MESSAGES)
            .all()
        )
    )
    lines = []
    for m in rows:
        who = partner_name if m.role == "partner" else names.get(m.user_id or "", "?")
        lines.append(f"{who}: {m.content}")
    people = [
        names.get(m.user_id, "?") for m in db.query(RoomMember).filter_by(room_id=room.id).all()
    ]
    return (
        f"Study room: {room.title}\n"
        f"People in the room: {', '.join(people)}\n"
        "Everyone in the room reads your answer; address the person who asked.\n\n"
        "Recent room chat (oldest first):\n" + ("\n".join(lines) or "(empty)")
    )


async def _ask_room_partner(db: Session, room: SharedRoom, question: str) -> str:
    """Run one public Partner turn for the room; returns the answer text."""
    from pathmind.multi_user.partner_access import can_use_partner
    from pathmind.services.partners import get_partner_manager

    partner_id = room.partner_id or ""
    manager = get_partner_manager()
    cfg = manager.load_config(partner_id)
    if cfg is None or not can_use_partner(partner_id):
        raise HTTPException(status_code=409, detail=_t("social.room_partner_unavailable"))
    instance = manager.get_partner(partner_id)
    if instance is None or not instance.running:
        await manager.start_partner(partner_id, cfg)
    names = _usernames()
    user = get_current_user()
    turn = await asyncio.wait_for(
        manager.send_group_message(
            partner_id,
            f"{user.username} asks in the study room: {question}",
            session_key=f"room-{room.id}",
            group_id=f"room-{room.id}",
            group_name=room.title,
            group_members=[
                {"partner_id": partner_id, "name": cfg.name, "description": cfg.description or ""}
            ],
            public_context=_partner_context(db, room, names, cfg.name),
            actor=user,
            allow_invoke_other=False,
        ),
        timeout=PARTNER_TURN_TIMEOUT_S,
    )
    content = turn if isinstance(turn, str) else getattr(turn, "content", "")
    return str(content or "").strip()


@router.post("/rooms/{room_code}/messages")
async def post_room_message(
    room_code: str, body: RoomMessageBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Post to the room chat; with ``ask_partner`` the room's Partner answers publicly."""
    me = _me(db)
    room = _member_room_or_403(db, room_code, me.id)
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Empty message")
    lock: asyncio.Lock | None = None
    if body.ask_partner:
        if not room.partner_id:
            raise HTTPException(status_code=400, detail=_t("social.room_no_partner"))
        from pathmind.services.quota_service import check_turn_allowed

        allowed, reason = check_turn_allowed(me.id, db, role=me.role)
        if not allowed:
            raise HTTPException(status_code=402, detail=reason)
        lock = _partner_locks.setdefault(room.id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(status_code=409, detail=_t("social.room_partner_busy"))

    new = [_add_message(db, room, role="user", content=content, user_id=me.id)]
    db.commit()
    if lock is not None:
        async with lock:
            try:
                answer = await _ask_room_partner(db, room, content)
                new.append(_add_message(db, room, role="partner", content=answer or "…"))
            except HTTPException as exc:
                new.append(_add_message(db, room, role="system", content=str(exc.detail)))
            except Exception as exc:  # partner crash, timeout, provider error
                logger.warning("Room %s partner turn failed: %s", room.room_code, exc)
                error = str(exc) or type(exc).__name__
                new.append(
                    _add_message(
                        db, room, role="system", content=_t("social.room_partner_failed", error=error[:300])
                    )
                )
            db.commit()
    names, partner_name = _usernames(), _partner_names().get(room.partner_id or "", "")
    return {"messages": [_message_view(m, names, partner_name) for m in new]}
