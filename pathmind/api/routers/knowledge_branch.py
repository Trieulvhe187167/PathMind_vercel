"""Personal knowledge branch API (private to the signed-in user).

Every signed-in account has a branch: it grows by itself from the user's KB
uploads (outline only, no AI cost) and can be edited, reset and studied as a
mastery path. Only Educators keep original files (for recommendations to R&D).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.database.connection import get_db
from pathmind.multi_user.context import get_current_user
from pathmind.services import knowledge_branch as kb

router = APIRouter()


def _uid() -> str:
    return str(get_current_user().id or "")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except kb.BranchError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


class NodeBody(BaseModel):
    parent_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    summary: str = ""
    source_kb: str | None = None
    source_doc: str | None = None
    base_version: int | None = None


class NodePatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    base_version: int | None = None


class MoveBody(BaseModel):
    parent_id: str | None = None
    base_version: int | None = None


class PrereqBody(BaseModel):
    prerequisite_ids: list[str] = Field(default_factory=list, max_length=50)


class VersionBody(BaseModel):
    label: str = Field(default="", max_length=120)


@router.get("")
def get_branch(db: Session = Depends(get_db)):
    return _run(kb.tree_of, db, _uid())


@router.post("/nodes")
def add_node(body: NodeBody, db: Session = Depends(get_db)):
    n = _run(
        kb.create_node, db, _uid(), body.parent_id, body.title, body.summary,
        source_kb=body.source_kb, source_doc=body.source_doc, base_version=body.base_version,
    )
    return {"id": n.id}


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, body: NodePatch, db: Session = Depends(get_db)):
    n = _run(
        kb.update_node, db, _uid(), node_id, title=body.title, summary=body.summary,
        base_version=body.base_version,
    )
    return {"id": n.id}


@router.post("/nodes/{node_id}/move")
def relocate_node(node_id: str, body: MoveBody, db: Session = Depends(get_db)):
    n = _run(kb.move_node, db, _uid(), node_id, body.parent_id, base_version=body.base_version)
    return {"id": n.id}


@router.delete("/nodes/{node_id}")
def remove_node(node_id: str, base_version: int | None = None, db: Session = Depends(get_db)):
    return {"deleted": _run(kb.delete_node, db, _uid(), node_id, base_version=base_version)}


@router.put("/nodes/{node_id}/prerequisites")
def put_prerequisites(node_id: str, body: PrereqBody, db: Session = Depends(get_db)):
    n = _run(kb.set_prerequisites, db, _uid(), node_id, body.prerequisite_ids)
    return {"id": n.id, "prerequisites": list(n.prerequisites or [])}


@router.get("/versions")
def versions(db: Session = Depends(get_db)):
    return {"versions": _run(kb.list_versions, db, _uid())}


@router.post("/versions")
def make_version(body: VersionBody, db: Session = Depends(get_db)):
    v = _run(kb.save_version, db, _uid(), body.label)
    return {"id": v.id, "label": v.label}


@router.post("/versions/{version_id}/restore")
def restore(version_id: str, db: Session = Depends(get_db)):
    return {"restored_nodes": _run(kb.restore_version, db, _uid(), version_id)}


@router.post("/enrich")
async def enrich(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Grow the branch from a document's outline (no AI cost).

    Educators also keep the original (to attach to a recommendation); it counts
    against the plan's storage quota like any other upload.
    """
    from pathmind.services import quota_service

    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is larger than 25 MB.")
    user = get_current_user()
    keep = str(user.role) in ("educator", "admin")
    if keep:
        ok, reason = quota_service.check_storage_batch(_uid(), [len(data)], db, role=user.role)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
    result = _run(
        kb.overlay.enrich_from_document, db, _uid(), file.filename or "file", data, keep_original=keep
    )
    if result.get("stored_bytes"):
        quota_service.update_user_storage(_uid(), int(result["stored_bytes"]), db)
    return result


@router.post("/reset")
def reset_branch(db: Session = Depends(get_db)):
    """Wipe the overlay; a restorable version named "Before reset" is kept."""
    return {"removed": _run(kb.overlay.reset, db, _uid())}


class MasteredBody(BaseModel):
    mastered: bool = True


@router.get("/path")
def mastery_path(db: Session = Depends(get_db)):
    return _run(kb.path.mastery_path, db, _uid())


@router.put("/nodes/{node_id}/mastered")
def mark_mastered(node_id: str, body: MasteredBody, db: Session = Depends(get_db)):
    return _run(kb.path.set_mastered, db, _uid(), node_id, body.mastered)
