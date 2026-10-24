from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.api.routers.auth import require_rnd
from pathmind.database.connection import get_db
from pathmind.database.models import AnswerRating
from pathmind.multi_user.context import get_current_user

router = APIRouter()  # mounted at /api/answer-ratings


def _uid() -> str:
    return str(get_current_user().id or "")


class RatingBody(BaseModel):
    session_id: str = Field(default="", max_length=64)
    message_id: str = Field(min_length=1, max_length=64)
    verdict: str
    reason: str = Field(default="", max_length=500)
    excerpt: str = Field(default="", max_length=300)


@router.put("")
def rate(body: RatingBody, db: Session = Depends(get_db)):
    if body.verdict not in ("right", "wrong"):
        raise HTTPException(status_code=400, detail="Verdict must be right or wrong.")
    row = db.query(AnswerRating).filter_by(user_id=_uid(), message_id=body.message_id).first()
    if row is None:
        row = AnswerRating(user_id=_uid(), message_id=body.message_id)
        db.add(row)
    row.session_id, row.verdict = body.session_id, body.verdict
    row.reason = body.reason.strip() if body.verdict == "wrong" else ""
    row.excerpt = body.excerpt[:300]
    db.commit()
    return {"verdict": row.verdict, "reason": row.reason}


@router.get("/session/{session_id}")
def for_session(session_id: str, db: Session = Depends(get_db)):
    rows = db.query(AnswerRating).filter_by(user_id=_uid(), session_id=session_id).all()
    return {"ratings": {r.message_id: {"verdict": r.verdict, "reason": r.reason} for r in rows}}


@router.delete("/{message_id}")
def clear(message_id: str, db: Session = Depends(get_db)):
    db.query(AnswerRating).filter_by(user_id=_uid(), message_id=message_id).delete()
    db.commit()
    return {"ok": True}


@router.get("/wrong", dependencies=[Depends(require_rnd)])
def recent_wrong(limit: int = 50, db: Session = Depends(get_db)):
    rows = (db.query(AnswerRating).filter_by(verdict="wrong")
            .order_by(AnswerRating.id.desc()).limit(max(1, min(limit, 200))).all())
    return {"items": [{"id": r.id, "reason": r.reason, "excerpt": r.excerpt,
                       "created_at": r.created_at.isoformat() + "Z" if r.created_at else None} for r in rows]}
