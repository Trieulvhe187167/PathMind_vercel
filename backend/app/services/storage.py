"""Storage gateway: where uploaded file bytes live.

If the admin configured an S3-compatible storage and marked it default (``storage_backends``), new
files go there; otherwise they go to the server disk under ``<data_dir>/files``. Each ``files`` row
remembers which storage holds its bytes, so switching storage never breaks older files.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
import re
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import StorageBackend, StoredFile, utcnow
from . import secretbox
from .s3 import S3Client, S3Config

# Tests swap in an httpx.MockTransport here to fake an S3 service.
S3_TRANSPORT: httpx.BaseTransport | None = None

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageError(RuntimeError):
    pass


def safe_name(name: str) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE.sub("_", base).strip("._") or "file"
    return cleaned[-120:]


def guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(filename)[0] or fallback


def client_for(backend: StorageBackend) -> S3Client:
    return S3Client(
        S3Config(
            bucket=backend.bucket, access_key=backend.access_key_id,
            secret_key=secretbox.try_decrypt(backend.secret_key_encrypted), region=backend.region,
            endpoint_url=backend.endpoint_url, path_style=backend.path_style,
        ),
        transport=S3_TRANSPORT,
    )


def default_backend(db: Session) -> StorageBackend | None:
    return db.scalar(
        select(StorageBackend).where(StorageBackend.is_default.is_(True), StorageBackend.is_active.is_(True))
    )


def _local_path(data_dir: Path, key: str) -> Path:
    root = (data_dir / "files").resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise StorageError("Bad storage key.")
    return path


def save_bytes(
    db: Session, data_dir: Path, *, owner_id: str, filename: str, data: bytes,
    mime_type: str = "", purpose: str = "upload",
) -> StoredFile:
    """Store ``data`` and add (not commit) its ``files`` row."""
    now = utcnow()
    name = safe_name(filename)
    backend = default_backend(db)
    key = f"{purpose}/{now:%Y/%m}/{uuid.uuid4().hex}-{name}"
    if backend is not None:
        key = (backend.key_prefix or "") + key
        try:
            client_for(backend).put_object(key, data, mime_type or guess_mime(name))
        except Exception as exc:  # network / credentials: report, do not lose the request silently
            backend.status, backend.last_error = "error", str(exc)[:500]
            raise StorageError(f"Could not save the file to {backend.name}: {exc}") from exc
    else:
        path = _local_path(data_dir, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    row = StoredFile(
        owner_id=owner_id, filename=(filename or name)[:255], mime_type=(mime_type or guess_mime(name))[:120],
        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_key=key,
        storage_backend_id=backend.id if backend else None, purpose=purpose[:20],
    )
    db.add(row)
    db.flush()
    return row


def read_bytes(db: Session, data_dir: Path, f: StoredFile) -> bytes:
    if f.deleted_at is not None:
        raise StorageError("File was deleted.")
    if f.storage_backend_id:
        backend = db.get(StorageBackend, f.storage_backend_id)
        if backend is None:
            raise StorageError("The storage that holds this file no longer exists.")
        return client_for(backend).get_object(f.storage_key)
    path = _local_path(data_dir, f.storage_key)
    if not path.exists():
        raise StorageError("File is missing on disk.")
    return path.read_bytes()


def signed_url(db: Session, f: StoredFile) -> str | None:
    """A short-lived direct download URL when the file is in object storage, else None."""
    if not f.storage_backend_id:
        return None
    backend = db.get(StorageBackend, f.storage_backend_id)
    if backend is None:
        return None
    return client_for(backend).presign_get(f.storage_key, backend.signed_url_seconds or 300, f.filename)


def delete_file(db: Session, data_dir: Path, f: StoredFile) -> None:
    """Soft-delete the row and remove the bytes (best effort). Caller commits."""
    f.deleted_at = utcnow()
    try:
        if f.storage_backend_id:
            backend = db.get(StorageBackend, f.storage_backend_id)
            if backend is not None:
                client_for(backend).delete_object(f.storage_key)
        else:
            path = _local_path(data_dir, f.storage_key)
            if path.exists():
                path.unlink()
    except Exception:  # noqa: BLE001 - the row is gone either way; a leftover object is harmless
        pass


def used_bytes(db: Session, owner_id: str) -> int:
    return int(db.scalar(
        select(func.coalesce(func.sum(StoredFile.size_bytes), 0)).where(
            StoredFile.owner_id == owner_id, StoredFile.deleted_at.is_(None)
        )
    ) or 0)
