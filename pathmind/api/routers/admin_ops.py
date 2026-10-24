"""Administrator operations: system settings, background jobs, the full audit log."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pathmind.database.connection import get_db
from pathmind.multi_user.context import get_current_user
from pathmind.services import audit, system_settings
from pathmind.services.storage import object_store as ostore

router = APIRouter()  # mounted at /api/admin-ops (administrators only)


def _uid() -> str:
    return str(get_current_user().id or "")


# ── settings (UC99) ──
@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {"settings": system_settings.view(db)}


@router.put("/settings")
def put_settings(body: dict[str, Any], db: Session = Depends(get_db)):
    try:
        return {"settings": system_settings.update(db, _uid(), body)}
    except system_settings.SettingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


class TestMail(BaseModel):
    to: str = Field(min_length=3, max_length=255)


@router.post("/settings/test-email")
def test_email(body: TestMail, db: Session = Depends(get_db)):
    from pathmind.services import email_verification as ev

    if not ev.smtp_configured():
        raise HTTPException(status_code=400, detail="SMTP host is not set.")
    ok = ev._send_mail(body.to.strip(), "PathMind test email", "SMTP is working.")
    audit.record(db, _uid(), "settings.test_email", target_type="email", target_id=body.to.strip(), data={"ok": ok})
    if not ok:
        raise HTTPException(status_code=502, detail="The test email could not be sent. Check the SMTP settings.")
    return {"ok": True}


# ── background jobs (UC101) ──
def _job_view(job) -> dict:
    s = job.schedule
    return {
        "id": job.id,
        "name": job.name,
        "owner": job.owner.key,
        "enabled": job.enabled,
        "schedule": {"kind": s.kind, "every_seconds": s.every_seconds, "expr": s.expr, "tz": s.tz, "at_ms": s.at_ms},
        "next_run_at_ms": job.state.next_run_at_ms,
        "last_run_at_ms": job.state.last_run_at_ms,
        "last_status": job.state.last_status,
        "last_error": job.state.last_error,
        "recent": [asdict(r) for r in job.state.run_history[-5:]],
    }


def _service():
    from pathmind.services.cron import get_cron_service

    return get_cron_service()


@router.get("/jobs")
def list_jobs():
    return {"jobs": [_job_view(j) for j in _service().list_jobs()]}


class EnableBody(BaseModel):
    enabled: bool


@router.put("/jobs/{job_id}/enabled")
def set_enabled(job_id: str, body: EnableBody, db: Session = Depends(get_db)):
    from pathmind.services.cron.service import compute_next_run, _now_ms

    svc = _service()
    job = svc.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    job.enabled = body.enabled
    if body.enabled and not job.state.next_run_at_ms:
        job.state.next_run_at_ms = compute_next_run(job.schedule, _now_ms())
    svc.repository.upsert(asdict(job))
    svc._changed()
    audit.record(db, _uid(), "job.enable" if body.enabled else "job.disable", target_type="job", target_id=job_id)
    return _job_view(job)


@router.post("/jobs/{job_id}/run")
async def run_now(job_id: str, db: Session = Depends(get_db)):
    svc = _service()
    job = svc.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    audit.record(db, _uid(), "job.run", target_type="job", target_id=job_id)
    await svc._run_job(job)
    return _job_view(svc.get_job(job_id) or job)


# ── audit log (UC102) ──
@router.get("/audit")
def audit_log(action_prefix: str = "", actor_id: str = "", limit: int = 200, db: Session = Depends(get_db)):
    return {"entries": audit.list_recent(db, limit=limit, action_prefix=action_prefix, actor_id=actor_id)}


# ── storage backends (UC100) ──
class StorageBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


def _storage_view(db: Session, row) -> dict:
    cfg = {k: v for k, v in (row.config or {}).items() if k not in ostore.SECRET_FIELDS}
    secrets = {f"{k}_set": bool((row.config or {}).get(k)) for k in ostore.SECRET_FIELDS if k in ostore.FIELDS[row.kind]}
    return {
        "id": row.id, "name": row.name, "kind": row.kind, "config": {**cfg, **secrets},
        "is_default": row.is_default, "enabled": row.enabled,
        "last_test_ok": row.last_test_ok, "last_test_message": row.last_test_message,
        "last_test_at": row.last_test_at.isoformat() + "Z" if row.last_test_at else None,
        "files": ostore.count_refs(db, row.id),
    }


def _storage_error(exc: ostore.StorageError):
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/storage")
def list_storage(db: Session = Depends(get_db)):
    from pathmind.database.models import StorageBackend

    rows = db.query(StorageBackend).order_by(StorageBackend.created_at).all()
    return {
        "backends": [_storage_view(db, r) for r in rows],
        "implicit": {"path": ostore._implicit_root(), "in_use": not any(r.is_default and r.enabled for r in rows),
                     "files": ostore.count_refs(db, ostore.IMPLICIT_ID)},
    }


@router.post("/storage")
def add_storage(body: StorageBody, db: Session = Depends(get_db)):
    from pathmind.database.models import StorageBackend

    try:
        cfg = ostore.validate_config(body.kind, body.config)
    except ostore.StorageError as exc:
        raise _storage_error(exc)
    if db.query(StorageBackend).filter_by(name=body.name.strip()).first():
        raise HTTPException(status_code=409, detail="A storage with this name already exists.")
    row = StorageBackend(name=body.name.strip(), kind=body.kind, config=cfg, enabled=body.enabled)
    db.add(row)
    db.flush()
    audit.record(db, _uid(), "storage.add", target_type="storage", target_id=row.id,
                 data={"name": row.name, "kind": row.kind}, commit=False)
    db.commit()
    return _storage_view(db, row)


@router.put("/storage/{backend_id}")
def edit_storage(backend_id: str, body: StorageBody, db: Session = Depends(get_db)):
    from pathmind.database.models import StorageBackend

    row = db.get(StorageBackend, backend_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    if body.kind != row.kind:
        raise HTTPException(status_code=400, detail="The kind cannot be changed. Add a new storage instead.")
    try:
        cfg = ostore.validate_config(row.kind, body.config, previous=row.config)
    except ostore.StorageError as exc:
        raise _storage_error(exc)
    if row.is_default and not body.enabled:
        raise HTTPException(status_code=409, detail="Pick another default before disabling this storage.")
    row.name, row.config, row.enabled = body.name.strip(), cfg, body.enabled
    row.last_test_ok, row.last_test_message = None, ""
    audit.record(db, _uid(), "storage.edit", target_type="storage", target_id=row.id, data={"name": row.name}, commit=False)
    db.commit()
    return _storage_view(db, row)


@router.post("/storage/{backend_id}/test")
def test_storage(backend_id: str, db: Session = Depends(get_db)):
    from datetime import datetime

    from pathmind.database.models import StorageBackend

    row = db.get(StorageBackend, backend_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    try:
        message = ostore.build(row.kind, row.config).check()
        ok = True
    except ostore.StorageError as exc:
        message, ok = exc.message, False
    except Exception as exc:  # noqa: BLE001
        message, ok = f"{type(exc).__name__}: {exc}"[:300], False
    row.last_test_ok, row.last_test_message, row.last_test_at = ok, message[:300], datetime.utcnow()
    audit.record(db, _uid(), "storage.test", target_type="storage", target_id=row.id, data={"ok": ok}, commit=False)
    db.commit()
    return _storage_view(db, row)


@router.post("/storage/{backend_id}/default")
def make_default(backend_id: str, db: Session = Depends(get_db)):
    from pathmind.database.models import StorageBackend

    row = db.get(StorageBackend, backend_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    if not row.enabled:
        raise HTTPException(status_code=409, detail="Enable this storage first.")
    if row.last_test_ok is not True:
        raise HTTPException(status_code=409, detail="Run a successful test before using it as the default.")
    db.query(StorageBackend).update({StorageBackend.is_default: False})
    row.is_default = True
    audit.record(db, _uid(), "storage.default", target_type="storage", target_id=row.id, data={"name": row.name}, commit=False)
    db.commit()
    return _storage_view(db, row)


@router.delete("/storage/{backend_id}")
def delete_storage(backend_id: str, db: Session = Depends(get_db)):
    from pathmind.database.models import StorageBackend

    row = db.get(StorageBackend, backend_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Storage not found.")
    if row.is_default:
        raise HTTPException(status_code=409, detail="This is the default storage. Pick another default first.")
    files = ostore.count_refs(db, row.id)
    if files:
        raise HTTPException(status_code=409, detail=f"{files} stored file(s) still live here, so it cannot be removed.")
    audit.record(db, _uid(), "storage.delete", target_type="storage", target_id=row.id, data={"name": row.name}, commit=False)
    db.delete(row)
    db.commit()
    return {"ok": True}
