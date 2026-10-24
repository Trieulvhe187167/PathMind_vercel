"""Personalising the AI: personas, skills, partners (study companions), memory and anonymous insights."""

from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db, require_admin
from ..models import (
    ChatMessage, ChatSession, Friendship, Memory, MemoryInsight, MemorySharingConsent, Partner, PartnerShare, Persona,
    Skill, User, utcnow,
)
from ..services import billing, llm

router = APIRouter()

BUILTIN_PERSONAS = [
    ("Teacher", "Patient teacher who explains step by step",
     "Teach like a patient, encouraging teacher: explain step by step, use examples, and check understanding."),
    ("Study buddy", "Friendly classmate who learns with you",
     "Talk like a friendly classmate: casual, curious, and supportive; think out loud together."),
    ("Research assistant", "Precise, cites evidence, structured",
     "Act as a careful research assistant: precise, structured, explicit about evidence and uncertainty."),
    ("Exam coach", "Drills and exam technique",
     "Act as an exam coach: focus on what is tested, common mistakes, and quick practice questions."),
]
BUILTIN_SKILLS = [
    ("flashcards", "Turn any explanation into question/answer flashcards",
     "---\nname: flashcards\ndescription: Make flashcards\n---\nWhen asked, produce concise Q/A flashcards, one fact per card."),
    ("feynman", "Explain like teaching a 12-year-old, then find gaps",
     "---\nname: feynman\ndescription: Feynman technique\n---\nExplain simply, then ask the learner to explain back and point out gaps."),
]


def seed(db: Session) -> None:
    if not db.scalar(select(Persona.id).where(Persona.owner_id.is_(None)).limit(1)):
        for name, desc, prompt in BUILTIN_PERSONAS:
            db.add(Persona(owner_id=None, name=name, description=desc, system_prompt=prompt))
    if not db.scalar(select(Skill.id).where(Skill.owner_id.is_(None)).limit(1)):
        for name, desc, content in BUILTIN_SKILLS:
            db.add(Skill(owner_id=None, name=name, description=desc, content=content, origin="builtin"))
    db.commit()


def iso(d):
    return d.isoformat() + "Z" if d else None


# ── Personas ─────────────────────────────────────────────────────────────────


def persona_public(p: Persona) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "system_prompt": p.system_prompt,
            "avatar_url": p.avatar_url, "builtin": p.owner_id is None}


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=10000)


@router.get("/personas")
def list_personas(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Persona).where(or_(Persona.owner_id.is_(None), Persona.owner_id == user.id))
                      .order_by(Persona.owner_id.is_not(None), Persona.name)).all()
    return {"personas": [persona_public(p) for p in rows]}


@router.post("/personas", status_code=201)
def create_persona(body: PersonaBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = Persona(owner_id=user.id, name=body.name.strip(), description=body.description, system_prompt=body.system_prompt)
    db.add(p)
    db.commit()
    return {"persona": persona_public(p)}


def _own_persona(db: Session, user: User, pid: str) -> Persona:
    p = db.get(Persona, pid)
    if p is None or p.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Persona not found.")
    return p


@router.put("/personas/{pid}")
def update_persona(pid: str, body: PersonaBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _own_persona(db, user, pid)
    p.name, p.description, p.system_prompt = body.name.strip(), body.description, body.system_prompt
    db.commit()
    return {"persona": persona_public(p)}


@router.delete("/personas/{pid}")
def delete_persona(pid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(_own_persona(db, user, pid))
    db.commit()
    return {"ok": True}


# ── Skills ───────────────────────────────────────────────────────────────────


def skill_public(s: Skill, full: bool = False) -> dict:
    out = {"id": s.id, "name": s.name, "description": s.description, "origin": s.origin, "is_enabled": s.is_enabled,
           "community_skill_id": s.community_skill_id, "builtin": s.owner_id is None, "updated_at": iso(s.updated_at)}
    if full:
        out["content"], out["files"] = s.content, s.files or {}
    return out


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class SkillBody(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=100000)
    files: dict[str, str] = {}
    is_enabled: bool = True


def own_skill_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count()).select_from(Skill).where(Skill.owner_id == user.id)) or 0


@router.get("/skills")
def list_skills(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Skill).where(or_(Skill.owner_id.is_(None), Skill.owner_id == user.id))
                      .order_by(Skill.owner_id.is_not(None), Skill.name)).all()
    ent = billing.entitlements(db, user)
    return {"skills": [skill_public(s) for s in rows],
            "allowance": {"limit": ent.get("max_skills"), "used": own_skill_count(db, user)}}


@router.post("/skills", status_code=201)
def create_skill(body: SkillBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    name = body.name.strip().lower()
    if not SKILL_NAME.match(name):
        raise HTTPException(status_code=422, detail="Skill name: lowercase letters, numbers and dashes.")
    billing.check_max(db, user, "max_skills", own_skill_count(db, user))
    if db.scalar(select(Skill.id).where(Skill.owner_id == user.id, Skill.name == name)):
        raise HTTPException(status_code=409, detail="You already have a skill with this name.")
    files = {k[:200]: v[:100000] for k, v in list(body.files.items())[:20]}
    s = Skill(owner_id=user.id, name=name, description=body.description, content=body.content, files=files,
              is_enabled=body.is_enabled, origin="own")
    db.add(s)
    db.commit()
    return {"skill": skill_public(s, True)}


@router.get("/skills/{sid}")
def get_skill(sid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = db.get(Skill, sid)
    if s is None or (s.owner_id and s.owner_id != user.id):
        raise HTTPException(status_code=404, detail="Skill not found.")
    return {"skill": skill_public(s, True)}


def _own_skill(db: Session, user: User, sid: str) -> Skill:
    s = db.get(Skill, sid)
    if s is None or s.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return s


@router.put("/skills/{sid}")
def update_skill(sid: str, body: SkillBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _own_skill(db, user, sid)
    s.description, s.content, s.is_enabled = body.description, body.content, body.is_enabled
    s.files = {k[:200]: v[:100000] for k, v in list(body.files.items())[:20]}
    db.commit()
    return {"skill": skill_public(s, True)}


@router.delete("/skills/{sid}")
def delete_skill(sid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(_own_skill(db, user, sid))
    db.commit()
    return {"ok": True}


# ── Partners ─────────────────────────────────────────────────────────────────


def partner_public(p: Partner, db: Session, user: User) -> dict:
    owner = db.get(User, p.owner_id) if p.owner_id else None
    shared = []
    if p.owner_id == user.id:
        for s in db.scalars(select(PartnerShare).where(PartnerShare.partner_id == p.id)):
            u = db.get(User, s.user_id)
            shared.append({"user_id": s.user_id, "username": u.username if u else None})
    return {"id": p.id, "name": p.name, "description": p.description, "avatar_url": p.avatar_url, "persona_id": p.persona_id,
            "system_prompt": p.system_prompt if p.owner_id == user.id else "", "model": p.model, "settings": p.settings or {},
            "mine": p.owner_id == user.id, "owner": owner.username if owner else None, "shared_with": shared}


class PartnerBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    persona_id: str | None = None
    system_prompt: str = Field(default="", max_length=10000)
    model: str = Field(default="", max_length=120)
    settings: dict = {}


@router.get("/partners")
def list_partners(db: Session = Depends(get_db), user: User = Depends(current_user)):
    shared = select(PartnerShare.partner_id).where(PartnerShare.user_id == user.id)
    rows = db.scalars(select(Partner).where(or_(Partner.owner_id == user.id, Partner.id.in_(shared))).order_by(Partner.name)).all()
    ent = billing.entitlements(db, user)
    mine = sum(1 for p in rows if p.owner_id == user.id)
    return {"partners": [partner_public(p, db, user) for p in rows],
            "allowance": {"limit": ent.get("max_partners"), "used": mine}}


def _check_partner_refs(db: Session, user: User, body: PartnerBody) -> None:
    if body.persona_id:
        per = db.get(Persona, body.persona_id)
        if per is None or (per.owner_id and per.owner_id != user.id):
            raise HTTPException(status_code=404, detail="Persona not found.")
    if body.model:
        llm.resolve(db, user, body.model)  # must exist and be allowed by the plan


@router.post("/partners", status_code=201)
def create_partner(body: PartnerBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n = db.scalar(select(func.count()).select_from(Partner).where(Partner.owner_id == user.id)) or 0
    billing.check_max(db, user, "max_partners", n)
    _check_partner_refs(db, user, body)
    p = Partner(owner_id=user.id, name=body.name.strip(), description=body.description, persona_id=body.persona_id,
                system_prompt=body.system_prompt, model=body.model.strip(), settings=body.settings)
    db.add(p)
    db.commit()
    return {"partner": partner_public(p, db, user)}


def _own_partner(db: Session, user: User, pid: str) -> Partner:
    p = db.get(Partner, pid)
    if p is None or p.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Partner not found.")
    return p


@router.put("/partners/{pid}")
def update_partner(pid: str, body: PartnerBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _own_partner(db, user, pid)
    _check_partner_refs(db, user, body)
    p.name, p.description, p.persona_id = body.name.strip(), body.description, body.persona_id
    p.system_prompt, p.model, p.settings = body.system_prompt, body.model.strip(), body.settings
    db.commit()
    return {"partner": partner_public(p, db, user)}


@router.delete("/partners/{pid}")
def delete_partner(pid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(_own_partner(db, user, pid))
    db.commit()
    return {"ok": True}


def are_friends(db: Session, a: str, b: str) -> bool:
    x, y = sorted((a, b))
    f = db.get(Friendship, (x, y))
    return f is not None and f.status == "accepted"


class ShareBody(BaseModel):
    user_id: str


@router.post("/partners/{pid}/share")
def share_partner(pid: str, body: ShareBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Friends may use (chat with) your partner; they never see or change its instructions."""
    p = _own_partner(db, user, pid)
    if not are_friends(db, user.id, body.user_id):
        raise HTTPException(status_code=409, detail="You can only share with friends.")
    if db.get(PartnerShare, (p.id, body.user_id)) is None:
        db.add(PartnerShare(partner_id=p.id, user_id=body.user_id, shared_by=user.id))
        from ..services.notify import notify

        notify(db, body.user_id, "partner_shared", f"{user.username} shared the partner '{p.name}' with you.",
               actor_id=user.id, target_type="partner", target_id=p.id)
    db.commit()
    return {"partner": partner_public(p, db, user)}


@router.delete("/partners/{pid}/share/{user_id}")
def unshare_partner(pid: str, user_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.get(Partner, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="Partner not found.")
    # The owner removes anyone; a friend can remove the partner from their own list.
    if p.owner_id != user.id and user_id != user.id:
        raise HTTPException(status_code=404, detail="Partner not found.")
    s = db.get(PartnerShare, (pid, user_id))
    if s is not None:
        db.delete(s)
    db.commit()
    return {"ok": True}


# ── Memory ───────────────────────────────────────────────────────────────────


def memory_public(m: Memory) -> dict:
    return {"id": m.id, "kind": m.kind, "content": m.content, "importance": m.importance,
            "source_session_id": m.source_session_id, "created_at": iso(m.created_at), "updated_at": iso(m.updated_at)}


@router.get("/memories")
def list_memories(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.deleted_at.is_(None))
                      .order_by(Memory.importance.desc(), Memory.updated_at.desc())).all()
    consent = db.get(MemorySharingConsent, user.id)
    return {"memories": [memory_public(m) for m in rows], "sharing": consent is not None}


class MemoryBody(BaseModel):
    kind: str = "fact"
    content: str = Field(min_length=1, max_length=1000)
    importance: int = Field(default=2, ge=1, le=5)


@router.post("/memories", status_code=201)
def add_memory(body: MemoryBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.kind not in ("fact", "preference", "goal", "difficulty"):
        raise HTTPException(status_code=422, detail="Unknown kind.")
    m = Memory(user_id=user.id, kind=body.kind, content=body.content.strip(), importance=body.importance)
    db.add(m)
    db.commit()
    return {"memory": memory_public(m)}


class ConsentBody(BaseModel):
    share: bool


@router.put("/memories/sharing")
def set_sharing(body: ConsentBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(MemorySharingConsent, user.id)
    if body.share and c is None:
        db.add(MemorySharingConsent(user_id=user.id, pseudo_id=secrets.token_hex(12)))
        db.flush()
        _share_insights(db, user)
    elif not body.share and c is not None:
        db.query(MemoryInsight).filter(MemoryInsight.pseudo_id == c.pseudo_id).delete()
        db.delete(c)
    db.commit()
    return {"sharing": body.share}


@router.put("/memories/{mid}")
def update_memory(mid: str, body: MemoryBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = db.get(Memory, mid)
    if m is None or m.user_id != user.id or m.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    m.kind, m.content, m.importance = body.kind, body.content.strip(), body.importance
    db.commit()
    return {"memory": memory_public(m)}


@router.delete("/memories/{mid}")
def delete_memory(mid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = db.get(Memory, mid)
    if m is None or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.delete("/memories")
def forget_everything(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.query(Memory).filter(Memory.user_id == user.id).delete()
    db.commit()
    return {"ok": True}


def _share_insights(db: Session, user: User) -> None:
    """Counts only: which kinds of goals/difficulties and which modes people use - no text, no user id."""
    c = db.get(MemorySharingConsent, user.id)
    if c is None:
        return
    period = utcnow().strftime("%Y-%m")
    db.query(MemoryInsight).filter(MemoryInsight.pseudo_id == c.pseudo_id, MemoryInsight.period == period).delete()
    counts: dict[tuple[str, str], int] = {}
    for kind, n in db.execute(select(Memory.kind, func.count()).where(Memory.user_id == user.id, Memory.deleted_at.is_(None))
                              .group_by(Memory.kind)).all():
        counts[("preference", f"memory:{kind}")] = n
    for mode, n in db.execute(select(ChatSession.mode, func.count()).where(ChatSession.user_id == user.id)
                              .group_by(ChatSession.mode)).all():
        counts[("activity", f"mode:{mode}"[:48])] = n
    msgs = db.scalar(select(func.count()).select_from(ChatMessage).join(ChatSession, ChatSession.id == ChatMessage.session_id)
                     .where(ChatSession.user_id == user.id, ChatMessage.role == "user")) or 0
    counts[("activity", "questions")] = msgs
    for (kind, label), n in counts.items():
        db.add(MemoryInsight(pseudo_id=c.pseudo_id, kind=kind, label=label, period=period, count=int(n)))
    c.last_shared_at = utcnow()


@router.get("/admin/insights")
def insights(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.execute(select(MemoryInsight.kind, MemoryInsight.label, MemoryInsight.period, func.count(func.distinct(MemoryInsight.pseudo_id)),
                             func.sum(MemoryInsight.count)).group_by(MemoryInsight.kind, MemoryInsight.label, MemoryInsight.period)
                      .order_by(MemoryInsight.period.desc(), func.sum(MemoryInsight.count).desc())).all()
    people = db.scalar(select(func.count()).select_from(MemorySharingConsent)) or 0
    return {"people_sharing": people,
            "insights": [{"kind": k, "label": label, "period": p, "people": int(n), "total": int(t or 0)} for k, label, p, n, t in rows]}
