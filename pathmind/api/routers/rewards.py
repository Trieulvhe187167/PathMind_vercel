from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.api.routers.auth import require_admin, require_rnd
from pathmind.database.connection import get_db
from pathmind.multi_user.context import get_current_user
from pathmind.services import audit, rewards

router = APIRouter()  # mounted at /api/rewards


def _uid() -> str:
    return str(get_current_user().id or "")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except rewards.RewardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


class RedeemBody(BaseModel):
    kind: str
    points: int = Field(gt=0)


class GrantBody(BaseModel):
    user_id: str
    points: int
    note: str = Field(default="", max_length=300)


@router.get("/me")
def mine(db: Session = Depends(get_db)):
    uid = _uid()
    return {"balance": rewards.balance(db, uid), "ledger": rewards.ledger(db, uid, 50),
            "rules": rewards.rules(db), "bonus_credits": rewards.bonus_credits(db, uid)}


@router.post("/redeem")
def redeem(body: RedeemBody, db: Session = Depends(get_db)):
    return _run(rewards.redeem, db, _uid(), body.kind, body.points)


@router.get("/rules", dependencies=[Depends(require_rnd)])
def get_rules(db: Session = Depends(get_db)):
    return {"rules": rewards.rules(db)}


@router.put("/rules", dependencies=[Depends(require_admin)])
def put_rules(body: dict[str, Any], db: Session = Depends(get_db)):
    return {"rules": _run(rewards.set_rules, db, _uid(), body)}


@router.get("/ledger", dependencies=[Depends(require_rnd)])
def all_ledger(user_id: str | None = None, db: Session = Depends(get_db)):
    return {"entries": rewards.ledger(db, user_id, 200)}


@router.post("/grant", dependencies=[Depends(require_admin)])
def manual_grant(body: GrantBody, db: Session = Depends(get_db)):
    actor = _uid()
    _run(rewards.grant, db, body.user_id, body.points, "manual", note=body.note, actor_id=actor, commit=False)
    audit.record(db, actor, "rewards.grant", target_type="user", target_id=body.user_id,
                 data={"points": body.points, "note": body.note}, commit=False)
    db.commit()
    return {"balance": rewards.balance(db, body.user_id)}
