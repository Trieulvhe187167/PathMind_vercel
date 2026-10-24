"""Upload and download files. Who may read a file depends on what it is attached to."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import (
    Contribution, ContributionFile, KnowledgeBase, KnowledgeBaseAccess, KnowledgeBaseDocument, StoredFile,
    User,
)
from ..services import storage
from ..services.billing import check_upload

router = APIRouter()

MAX_UPLOAD = 200 * 1024 * 1024  # hard ceiling; plans usually allow less


def public_file(f: StoredFile) -> dict:
    return {
        "id": f.id, "filename": f.filename, "mime_type": f.mime_type, "size_bytes": f.size_bytes,
        "purpose": f.purpose, "created_at": f.created_at.isoformat() + "Z" if f.created_at else None,
        "in_object_storage": bool(f.storage_backend_id),
    }


def can_read(db: Session, user: User, f: StoredFile) -> bool:
    if f.deleted_at is not None:
        return False
    if f.owner_id == user.id or user.role == "admin":
        return True
    if f.purpose == "avatar":
        return True
    if user.role == "rnd" and f.purpose in ("contribution", "verified", "tree_import", "tree_export"):
        return True
    if f.purpose == "contribution":
        # the contributor of a contribution that carries this file
        row = db.execute(
            select(Contribution.contributor_id).join(ContributionFile, ContributionFile.contribution_id == Contribution.id)
            .where(ContributionFile.file_id == f.id)
        ).first()
        return bool(row and row[0] == user.id)
    if f.purpose == "kb":
        kb_ids = db.scalars(select(KnowledgeBaseDocument.knowledge_base_id).where(KnowledgeBaseDocument.file_id == f.id)).all()
        for kb_id in kb_ids:
            kb = db.get(KnowledgeBase, kb_id)
            if kb and (kb.visibility == "public" or kb.owner_id == user.id or db.get(KnowledgeBaseAccess, (kb.id, user.id))):
                return True
    if f.purpose == "chat":
        # attachments are only for the conversation's owner (already covered by owner_id)
        return False
    return False


def read_upload(file: UploadFile, limit: int = MAX_UPLOAD) -> bytes:
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"File is larger than {limit // (1024 * 1024)} MB.")
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty.")
    return data


def store_upload(request: Request, db: Session, user: User, file: UploadFile, purpose: str) -> StoredFile:
    data = read_upload(file)
    check_upload(db, user, len(data))
    try:
        return storage.save_bytes(
            db, request.app.state.settings.data_dir, owner_id=user.id, filename=file.filename or "file",
            data=data, mime_type=file.content_type or "", purpose=purpose,
        )
    except storage.StorageError as exc:
        db.commit()  # keep the storage's error status
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("", status_code=201)
def upload(request: Request, file: UploadFile = File(...), purpose: str = Form("upload"),
           db: Session = Depends(get_db), user: User = Depends(current_user)):
    if purpose not in ("upload", "chat", "avatar", "reading"):
        raise HTTPException(status_code=422, detail="Unknown purpose.")
    row = store_upload(request, db, user, file, purpose)
    db.commit()
    return {"file": public_file(row)}


@router.get("")
def my_files(purpose: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    stmt = select(StoredFile).where(StoredFile.owner_id == user.id, StoredFile.deleted_at.is_(None))
    if purpose:
        stmt = stmt.where(StoredFile.purpose == purpose)
    rows = db.scalars(stmt.order_by(StoredFile.created_at.desc()).limit(500)).all()
    return {"files": [public_file(f) for f in rows], "used_bytes": storage.used_bytes(db, user.id)}


def _get_readable(db: Session, user: User, file_id: str) -> StoredFile:
    f = db.get(StoredFile, file_id)
    if f is None or not can_read(db, user, f):
        raise HTTPException(status_code=404, detail="File not found.")
    return f


@router.get("/{file_id}")
def file_info(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"file": public_file(_get_readable(db, user, file_id))}


@router.get("/{file_id}/download")
def download(file_id: str, request: Request, inline: bool = False, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    f = _get_readable(db, user, file_id)
    url = storage.signed_url(db, f) if not inline else None
    if url:
        return RedirectResponse(url, status_code=307)
    try:
        data = storage.read_bytes(db, request.app.state.settings.data_dir, f)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not read the file: {exc}") from exc
    from urllib.parse import quote

    disp = "inline" if inline else "attachment"
    return Response(data, media_type=f.mime_type, headers={
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(f.filename)}",
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{file_id}")
def delete(file_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    f = db.get(StoredFile, file_id)
    if f is None or f.deleted_at is not None or f.owner_id != user.id:
        raise HTTPException(status_code=404, detail="File not found.")
    if f.purpose not in ("upload", "chat", "avatar", "reading"):
        raise HTTPException(status_code=409, detail="This file belongs to something else; remove it there.")
    storage.delete_file(db, request.app.state.settings.data_dir, f)
    db.commit()
    return {"ok": True}

