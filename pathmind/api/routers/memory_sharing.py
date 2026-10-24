"""Opt-in sharing of anonymised memory aggregates (see ``services.memory_insights``).

Open to every signed-in account regardless of plan: switching sharing off and
erasing what was shared must never depend on a subscription.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pathmind.api.routers.auth import require_admin
from pathmind.database.connection import get_db
from pathmind.multi_user.context import get_current_user
from pathmind.services import memory_insights as mi

router = APIRouter()


class SharingToggle(BaseModel):
    enabled: bool


def _uid() -> str:
    return str(get_current_user().id or "")


@router.get("")
def get_status(db: Session = Depends(get_db)):
    return mi.status(db, _uid())


@router.put("")
def set_sharing(body: SharingToggle, db: Session = Depends(get_db)):
    uid = _uid()
    if body.enabled:
        mi.enable(db, uid)
        return {**mi.status(db, uid), "deleted": 0}
    deleted = mi.withdraw(db, uid)
    return {**mi.status(db, uid), "deleted": deleted}


@router.get("/preview")
def preview():
    return mi.build_preview()


@router.post("/share")
def share(db: Session = Depends(get_db)):
    try:
        return mi.share_now(db, _uid())
    except PermissionError:
        raise HTTPException(status_code=409, detail="Memory sharing is off.")


@router.delete("/data")
def delete_data(db: Session = Depends(get_db)):
    return {"deleted": mi.delete_shared_data(db, _uid())}


@router.get("/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats(period: str | None = Query(default=None), db: Session = Depends(get_db)):
    try:
        return mi.aggregate(db, period)
    except ValueError:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
