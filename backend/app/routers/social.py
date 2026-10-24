"""Friends and shared study rooms (group chat that can ask the room's AI partner)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import Friendship, Partner, PartnerShare, RoomMember, RoomMessage, StudyRoom, User, utcnow
from ..services import billing, llm
from ..services.notify import notify

router = APIRouter()
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def iso(d):
    return d.isoformat() + "Z" if d else None


def _person(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "avatar_url": u.avatar_url}


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


# ── Friends ──────────────────────────────────────────────────────────────────


@router.get("/friends")
def friends(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Friendship).where(or_(Friendship.user_a_id == user.id, Friendship.user_b_id == user.id))).all()
    out = {"friends": [], "incoming": [], "outgoing": [], "blocked": []}
    for f in rows:
        other_id = f.user_b_id if f.user_a_id == user.id else f.user_a_id
        other = db.get(User, other_id)
        if other is None or other.deleted_at is not None:
            continue
        item = {**_person(other), "since": iso(f.updated_at)}
        if f.status == "accepted":
            out["friends"].append(item)
        elif f.status == "pending":
            out["incoming" if f.requested_by != user.id else "outgoing"].append(item)
        elif f.status == "blocked" and f.blocked_by == user.id:
            out["blocked"].append(item)
    return out


class RequestBody(BaseModel):
    target: str = Field(min_length=1, max_length=255)  # username or email


@router.post("/friends/request")
def request_friend(body: RequestBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    typed = body.target.strip().lower()
    other = db.scalar(select(User).where(or_(func.lower(User.username) == typed, User.email == typed),
                                         User.is_active.is_(True), User.deleted_at.is_(None)))
    if other is None or other.id == user.id:
        raise HTTPException(status_code=404, detail="No such person.")
    a, b = _pair(user.id, other.id)
    f = db.get(Friendship, (a, b))
    if f is not None:
        if f.status == "blocked":
            raise HTTPException(status_code=409, detail="You cannot add this person.")
        if f.status == "accepted":
            return {"status": "accepted"}
        if f.requested_by != user.id:  # they asked us already: asking back accepts
            f.status = "accepted"
            notify(db, other.id, "friend_accepted", f"{user.username} accepted your friend request.", actor_id=user.id,
                   target_type="user", target_id=user.id)
            db.commit()
            return {"status": "accepted"}
        return {"status": "pending"}
    db.add(Friendship(user_a_id=a, user_b_id=b, status="pending", requested_by=user.id))
    notify(db, other.id, "friend_request", f"{user.username} wants to be your friend.", actor_id=user.id,
           target_type="user", target_id=user.id)
    db.commit()
    return {"status": "pending"}


class RespondBody(BaseModel):
    user_id: str
    action: str  # accept | reject | block | unblock | remove


@router.post("/friends/respond")
def respond(body: RespondBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a, b = _pair(user.id, body.user_id)
    f = db.get(Friendship, (a, b))
    if body.action == "block":
        if f is None:
            if db.get(User, body.user_id) is None:
                raise HTTPException(status_code=404, detail="No such person.")
            f = Friendship(user_a_id=a, user_b_id=b, requested_by=user.id)
            db.add(f)
        f.status, f.blocked_by = "blocked", user.id
        db.query(PartnerShare).filter(PartnerShare.user_id == body.user_id,
                                      PartnerShare.partner_id.in_(select(Partner.id).where(Partner.owner_id == user.id))).delete(synchronize_session=False)
        db.commit()
        return {"status": "blocked"}
    if f is None:
        raise HTTPException(status_code=404, detail="No request from this person.")
    if body.action == "accept":
        if f.status != "pending" or f.requested_by == user.id:
            raise HTTPException(status_code=409, detail="Nothing to accept.")
        f.status = "accepted"
        notify(db, body.user_id, "friend_accepted", f"{user.username} accepted your friend request.", actor_id=user.id,
               target_type="user", target_id=user.id)
    elif body.action in ("reject", "remove", "unblock"):
        if f.status == "blocked" and f.blocked_by != user.id:
            raise HTTPException(status_code=404, detail="No request from this person.")
        # Removing a friend also stops sharing partners between the two.
        for owner, other in ((user.id, body.user_id), (body.user_id, user.id)):
            db.query(PartnerShare).filter(PartnerShare.user_id == other,
                                          PartnerShare.partner_id.in_(select(Partner.id).where(Partner.owner_id == owner))).delete(synchronize_session=False)
        db.delete(f)
    else:
        raise HTTPException(status_code=422, detail="Unknown action.")
    db.commit()
    return {"status": body.action}


# ── Study rooms ──────────────────────────────────────────────────────────────


def room_public(r: StudyRoom, db: Session) -> dict:
    members = db.execute(select(RoomMember, User).join(User, User.id == RoomMember.user_id).where(RoomMember.room_id == r.id)).all()
    partner = db.get(Partner, r.partner_id) if r.partner_id else None
    return {"id": r.id, "code": r.code, "title": r.title, "host_id": r.host_id, "is_open": r.is_open,
            "partner": {"id": partner.id, "name": partner.name} if partner else None,
            "members": [{**_person(u), "role": m.role} for m, u in members], "created_at": iso(r.created_at)}


def _rooms_feature(db: Session, user: User) -> None:
    billing.require_feature(db, user, "rooms")


class RoomBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    partner_id: str | None = None


@router.get("/rooms")
def my_rooms(db: Session = Depends(get_db), user: User = Depends(current_user)):
    ids = select(RoomMember.room_id).where(RoomMember.user_id == user.id)
    rows = db.scalars(select(StudyRoom).where(StudyRoom.id.in_(ids), StudyRoom.is_open.is_(True)).order_by(StudyRoom.created_at.desc())).all()
    return {"rooms": [room_public(r, db) for r in rows]}


@router.post("/rooms", status_code=201)
def create_room(body: RoomBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _rooms_feature(db, user)
    if body.partner_id:
        p = db.get(Partner, body.partner_id)
        if p is None or p.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Partner not found.")
    code = "".join(secrets.choice(CODE_CHARS) for _ in range(6))
    while db.scalar(select(StudyRoom.id).where(StudyRoom.code == code)):
        code = "".join(secrets.choice(CODE_CHARS) for _ in range(6))
    r = StudyRoom(code=code, title=body.title.strip(), host_id=user.id, partner_id=body.partner_id)
    db.add(r)
    db.flush()
    db.add(RoomMember(room_id=r.id, user_id=user.id, role="host"))
    db.commit()
    return {"room": room_public(r, db)}


def _room(db: Session, code: str) -> StudyRoom:
    r = db.scalar(select(StudyRoom).where(StudyRoom.code == code.strip().upper()))
    if r is None or not r.is_open:
        raise HTTPException(status_code=404, detail="Room not found or closed.")
    return r


def _member(db: Session, r: StudyRoom, user: User) -> RoomMember:
    m = db.get(RoomMember, (r.id, user.id))
    if m is None:
        raise HTTPException(status_code=403, detail="Join the room first.")
    return m


@router.get("/rooms/{code}")
def get_room(code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _room(db, code)
    joined = db.get(RoomMember, (r.id, user.id)) is not None
    return {"room": room_public(r, db), "joined": joined}


@router.post("/rooms/{code}/join")
def join(code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _rooms_feature(db, user)
    r = _room(db, code)
    if db.get(RoomMember, (r.id, user.id)) is None:
        db.add(RoomMember(room_id=r.id, user_id=user.id))
        db.add(RoomMessage(room_id=r.id, role="system", content=f"{user.username} joined."))
    db.commit()
    return {"room": room_public(r, db)}


@router.post("/rooms/{code}/leave")
def leave(code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _room(db, code)
    m = _member(db, r, user)
    if m.role == "host":
        raise HTTPException(status_code=409, detail="The host closes the room instead of leaving.")
    db.delete(m)
    db.add(RoomMessage(room_id=r.id, role="system", content=f"{user.username} left."))
    db.commit()
    return {"ok": True}


@router.delete("/rooms/{code}")
def close(code: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _room(db, code)
    if r.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the host can close the room.")
    r.is_open, r.closed_at = False, utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/rooms/{code}/members/{user_id}")
def kick(code: str, user_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _room(db, code)
    if r.host_id != user.id or user_id == user.id:
        raise HTTPException(status_code=403, detail="Only the host can remove others.")
    m = db.get(RoomMember, (r.id, user_id))
    if m is not None:
        db.delete(m)
    db.commit()
    return {"ok": True}


def message_public(m: RoomMessage, db: Session) -> dict:
    u = db.get(User, m.user_id) if m.user_id else None
    return {"id": m.id, "role": m.role, "content": m.content, "user": _person(u) if u else None, "created_at": iso(m.created_at)}


@router.get("/rooms/{code}/messages")
def messages(code: str, after: int = 0, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _room(db, code)
    _member(db, r, user)
    rows = db.scalars(select(RoomMessage).where(RoomMessage.room_id == r.id, RoomMessage.id > after)
                      .order_by(RoomMessage.id).limit(200)).all()
    return {"messages": [message_public(m, db) for m in rows]}


class PostBody(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    ask_partner: bool = False


@router.post("/rooms/{code}/messages", status_code=201)
def post(code: str, body: PostBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _room(db, code)
    _member(db, r, user)
    m = RoomMessage(room_id=r.id, user_id=user.id, role="user", content=body.content.strip())
    db.add(m)
    db.commit()
    out = [message_public(m, db)]
    if body.ask_partner:
        p = db.get(Partner, r.partner_id) if r.partner_id else None
        if p is None:
            raise HTTPException(status_code=409, detail="This room has no partner.")
        history = db.scalars(select(RoomMessage).where(RoomMessage.room_id == r.id).order_by(RoomMessage.id.desc()).limit(20)).all()[::-1]
        transcript = "\n".join(f"{(db.get(User, h.user_id).username if h.user_id else p.name)}: {h.content}"
                               for h in history if h.role != "system")
        try:
            # The person who asks pays the credits.
            answer = llm.complete(db, user=user, model=p.model or None, purpose="room", messages=[
                {"role": "system", "content": f"You are {p.name}, a study partner in a group study room. {p.system_prompt}"},
                {"role": "user", "content": f"Conversation so far:\n{transcript}\n\nReply to {user.username}'s last message."}])
            reply = RoomMessage(room_id=r.id, role="partner", content=answer)
        except Exception as exc:  # noqa: BLE001 - shown in the room
            msg = exc.detail.get("message") if hasattr(exc, "detail") and isinstance(exc.detail, dict) else str(getattr(exc, "detail", exc))
            reply = RoomMessage(room_id=r.id, role="system", content=f"{p.name} could not answer: {msg}")
        db.add(reply)
        db.commit()
        out.append(message_public(reply, db))
    return {"messages": out}
