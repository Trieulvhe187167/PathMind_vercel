"""Administrators: system settings, object storage, background tasks and scheduled jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import BackgroundTask, ScheduledJob, StorageBackend, StoredFile, User, utcnow
from ..services import audit, mailer, secretbox, storage, tasks
from ..services.app_settings import DEFAULTS, public_setting, set_setting
from .auth import _client_ip

router = APIRouter()

EDITABLE = ("general", "registration", "smtp", "google_oauth", "billing", "vnpay", "rewards", "ai", "knowledge")


# ── Settings ──────────────────────────────────────────────────────────────────


@router.get("/settings")
def all_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"settings": {k: public_setting(db, k) for k in EDITABLE}}


@router.get("/settings/{key}")
def one_setting(key: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if key not in EDITABLE:
        raise HTTPException(status_code=404, detail="Unknown settings group.")
    return {"settings": public_setting(db, key)}


@router.put("/settings/{key}")
def update_setting(key: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    if key not in EDITABLE:
        raise HTTPException(status_code=404, detail="Unknown settings group.")
    if key == "general" and isinstance(body.get("public_url"), str):
        url = body["public_url"].strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="The public address must start with http:// or https://")
        body["public_url"] = url
    set_setting(db, key, body, updated_by=admin.id)
    changed = sorted(k for k in body if k in DEFAULTS[key] or k + "_enc" in DEFAULTS[key])
    audit.record(db, admin.id, f"settings.{key}", target_type="app_setting", target_id=key,
                 data={"fields": changed}, ip=_client_ip(request))
    db.commit()
    return {"settings": public_setting(db, key)}


class TestMail(BaseModel):
    to: str = Field(min_length=3, max_length=255)


@router.post("/settings/smtp/test")
def test_smtp(body: TestMail, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not mailer.configured(db) and mailer.OUTBOX is None:
        raise HTTPException(status_code=409, detail="Fill in the SMTP server and sender address first.")
    try:
        mailer.send(db, body.to, "PathMind test e-mail", "If you can read this, e-mail from PathMind works.")
    except mailer.MailError as exc:
        raise HTTPException(status_code=502, detail=f"Sending failed: {exc}") from exc
    return {"ok": True}


# ── Object storage ────────────────────────────────────────────────────────────


def storage_public(b: StorageBackend, db: Session) -> dict:
    count = db.scalar(select(StoredFile.id).where(StoredFile.storage_backend_id == b.id).limit(1))
    return {
        "id": b.id, "name": b.name, "kind": b.kind, "endpoint_url": b.endpoint_url, "region": b.region,
        "bucket": b.bucket, "key_prefix": b.key_prefix, "access_key_id": b.access_key_id,
        "secret_key_set": bool(b.secret_key_encrypted), "path_style": b.path_style,
        "signed_url_seconds": b.signed_url_seconds, "is_default": b.is_default, "is_active": b.is_active,
        "status": b.status, "last_error": b.last_error,
        "last_checked_at": b.last_checked_at.isoformat() + "Z" if b.last_checked_at else None,
        "has_files": bool(count),
    }


class StorageBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint_url: str = Field(default="", max_length=500)
    region: str = Field(default="", max_length=64)
    bucket: str = Field(min_length=1, max_length=255)
    key_prefix: str = Field(default="", max_length=200)
    access_key_id: str = Field(default="", max_length=255)
    secret_key: str | None = None  # None = keep, "" = clear
    path_style: bool = False
    signed_url_seconds: int = Field(default=300, ge=30, le=86400)
    is_active: bool = True


def _apply_storage(b: StorageBackend, body: StorageBody) -> None:
    url = body.endpoint_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Endpoint must start with https:// (or http:// on a local network).")
    prefix = body.key_prefix.strip().lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    b.name, b.endpoint_url, b.region, b.bucket = body.name.strip(), url, body.region.strip(), body.bucket.strip()
    b.key_prefix, b.access_key_id, b.path_style = prefix, body.access_key_id.strip(), body.path_style
    b.signed_url_seconds, b.is_active = body.signed_url_seconds, body.is_active
    if body.secret_key is not None:
        b.secret_key_encrypted = secretbox.encrypt(body.secret_key) if body.secret_key else ""
    b.status = "untested"
    if not b.is_active:
        b.is_default = False


@router.get("/storage")
def list_storage(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(StorageBackend).order_by(StorageBackend.created_at)).all()
    return {"backends": [storage_public(b, db) for b in rows],
            "default": next((b.id for b in rows if b.is_default and b.is_active), None)}


@router.post("/storage", status_code=201)
def create_storage(body: StorageBody, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    if db.scalar(select(StorageBackend.id).where(StorageBackend.name == body.name.strip())):
        raise HTTPException(status_code=409, detail="A storage with this name exists.")
    b = StorageBackend(kind="s3", created_by=admin.id, bucket=body.bucket)
    _apply_storage(b, body)
    db.add(b)
    db.flush()
    audit.record(db, admin.id, "storage.create", target_type="storage", target_id=b.id,
                 data={"name": b.name, "bucket": b.bucket}, ip=_client_ip(request))
    db.commit()
    return {"backend": storage_public(b, db)}


@router.put("/storage/{backend_id}")
def update_storage(backend_id: str, body: StorageBody, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    b = db.get(StorageBackend, backend_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    has_files = db.scalar(select(StoredFile.id).where(StoredFile.storage_backend_id == b.id).limit(1))
    if has_files and (body.bucket.strip() != b.bucket or body.endpoint_url.strip().rstrip("/") != b.endpoint_url):
        raise HTTPException(status_code=409, detail="Files are stored here, so the bucket and endpoint cannot change. Add a new storage instead.")
    _apply_storage(b, body)
    audit.record(db, admin.id, "storage.update", target_type="storage", target_id=b.id, data={"name": b.name},
                 ip=_client_ip(request))
    db.commit()
    return {"backend": storage_public(b, db)}


@router.post("/storage/{backend_id}/test")
def test_storage(backend_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    b = db.get(StorageBackend, backend_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    try:
        storage.client_for(b).check()
        b.status, b.last_error = "ok", ""
    except Exception as exc:  # noqa: BLE001 - shown to the admin
        b.status, b.last_error = "error", str(exc)[:500]
    b.last_checked_at = utcnow()
    db.commit()
    return {"backend": storage_public(b, db)}


class DefaultBody(BaseModel):
    backend_id: str | None = None  # None = keep new files on the server disk


@router.put("/storage-default")
def set_default(body: DefaultBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = None
    if body.backend_id:
        target = db.get(StorageBackend, body.backend_id)
        if target is None or not target.is_active:
            raise HTTPException(status_code=404, detail="Storage not found or not active.")
        if target.status != "ok":
            raise HTTPException(status_code=409, detail="Test the connection successfully before using this storage.")
    db.execute(update(StorageBackend).values(is_default=False))
    db.flush()
    if target is not None:
        target.is_default = True
    audit.record(db, admin.id, "storage.default", target_type="storage", target_id=body.backend_id or "disk",
                 ip=_client_ip(request))
    db.commit()
    return list_storage(db, admin)


@router.delete("/storage/{backend_id}")
def delete_storage(backend_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    b = db.get(StorageBackend, backend_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    if db.scalar(select(StoredFile.id).where(StoredFile.storage_backend_id == b.id).limit(1)):
        raise HTTPException(status_code=409, detail="Files are stored here. Turn it off (inactive) instead of deleting it.")
    audit.record(db, admin.id, "storage.delete", target_type="storage", target_id=b.id, data={"name": b.name},
                 ip=_client_ip(request))
    db.delete(b)
    db.commit()
    return {"ok": True}


# ── Background tasks and scheduled jobs ─────────────────────────────────────────


@router.get("/tasks")
def list_tasks(status: str = "", db: Session = Depends(get_db), _: User = Depends(require_admin)):
    stmt = select(BackgroundTask).order_by(BackgroundTask.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(BackgroundTask.status == status)
    return {"tasks": [tasks.public(t) for t in db.scalars(stmt)]}


def job_public(j: ScheduledJob) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {"id": j.id, "name": j.name, "cron": j.cron, "timezone": j.timezone, "action": j.action,
            "is_enabled": j.is_enabled, "next_run_at": iso(j.next_run_at), "last_run_at": iso(j.last_run_at),
            "last_status": j.last_status}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(ScheduledJob).where(ScheduledJob.owner_id.is_(None)).order_by(ScheduledJob.name)).all()
    return {"jobs": [job_public(j) for j in rows]}


class JobPatch(BaseModel):
    is_enabled: bool | None = None
    cron: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=64)


@router.patch("/jobs/{job_id}")
def patch_job(job_id: str, body: JobPatch, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    j = db.get(ScheduledJob, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if body.cron is not None:
        try:
            tasks.next_run(body.cron, utcnow())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        j.cron = body.cron.strip()
    if body.timezone is not None:
        j.timezone = body.timezone.strip() or "Asia/Ho_Chi_Minh"
    if body.is_enabled is not None:
        j.is_enabled = body.is_enabled
    tasks.schedule_next(j, utcnow())
    db.commit()
    return {"job": job_public(j)}


@router.post("/jobs/{job_id}/run")
def run_job_now(job_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    j = db.get(ScheduledJob, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    msg = tasks.run_job(db, j)
    j.last_run_at, j.last_status = utcnow(), "ok"
    db.commit()
    return {"job": job_public(j), "message": msg}
