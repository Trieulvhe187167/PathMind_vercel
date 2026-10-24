from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.api.routers.auth import require_educator, require_rnd, require_rnd_only
from pathmind.database.connection import get_db
from pathmind.multi_user.context import get_current_user
from pathmind.services import recommendations as rec
from pathmind.services.entitlements import feature_dependency

router = APIRouter()  # mounted at /api/recommendations


def _uid() -> str:
    return str(get_current_user().id or "")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except rec.RecError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


class SubmitBody(BaseModel):
    node_id: str
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class MergeBody(BaseModel):
    domain_id: str
    parent_id: str | None = None
    node_ids: list[str]
    adopt_documents: bool = True
    note: str = Field(default="", max_length=2000)


class RejectBody(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


# Educators (recommending to R&D is a paid-plan feature: "contribute")
@router.post("", dependencies=[Depends(require_educator), Depends(feature_dependency("contribute"))])
def submit(body: SubmitBody, db: Session = Depends(get_db)):
    return _run(rec.submit, db, _uid(), body.node_id, body.title, body.note)


@router.get("/mine", dependencies=[Depends(require_educator)])
def mine(db: Session = Depends(get_db)):
    return {"items": rec.list_mine(db, _uid())}


# R&D and administrators
@router.get("", dependencies=[Depends(require_rnd)])
def review_queue(status: str | None = None, db: Session = Depends(get_db)):
    return {"items": rec.list_all(db, status)}


@router.get("/{rec_id}", dependencies=[Depends(require_rnd)])
def get_one(rec_id: str, db: Session = Depends(get_db)):
    return _run(rec.detail, db, rec_id)


@router.get("/{rec_id}/documents/{index}", dependencies=[Depends(require_rnd)])
def download(rec_id: str, index: int, db: Session = Depends(get_db)):
    data, name = _run(rec.read_document, db, _uid(), rec_id, index)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name.replace(chr(34), "")}"'})


# Merging changes the main tree, and deciding is R&D's job: administrators only watch.
@router.post("/{rec_id}/merge", dependencies=[Depends(require_rnd_only)])
def merge(rec_id: str, body: MergeBody, db: Session = Depends(get_db)):
    return _run(rec.merge, db, _uid(), rec_id, domain_id=body.domain_id, parent_id=body.parent_id,
                node_ids=body.node_ids, adopt_documents=body.adopt_documents, note=body.note)


@router.post("/{rec_id}/reject", dependencies=[Depends(require_rnd_only)])
def reject(rec_id: str, body: RejectBody, db: Session = Depends(get_db)):
    return _run(rec.reject, db, _uid(), rec_id, body.note)
