"""Pluggable object storage for documents: a local folder or an S3-compatible bucket.

A stored object is identified by a *reference* string:

* ``"<backend_id>|<key>"``  - written through a configured backend;
* an absolute path          - legacy files written before backends existed.

Files stay in the backend they were written to. Changing the default only affects
new files; a backend that still holds files cannot be deleted.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import re
from pathlib import Path
from typing import Any, Protocol
import urllib.error
import urllib.parse
import urllib.request
import uuid

from sqlalchemy.orm import Session

from pathmind.database.models import StorageBackend

IMPLICIT_ID = "default-local"  # used when no backend is configured
TIMEOUT = 20


class StorageError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Backend(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def check(self) -> str: ...


def _safe_key(key: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", key) if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise StorageError(400, "Invalid storage key.")
    return "/".join(parts)


# ── local folder ──────────────────────────────────────────────────────────


class LocalBackend:
    def __init__(self, root: str):
        self.root = Path(root).expanduser()

    def _path(self, key: str) -> Path:
        base = self.root.resolve()
        target = (base / _safe_key(key)).resolve()
        if base != target and base not in target.parents:
            raise StorageError(400, "Invalid storage key.")
        return target

    def put(self, key: str, data: bytes, content_type: str = "") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise StorageError(404, "The stored file is missing.")
        return p.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def check(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = f".pathmind-check-{uuid.uuid4().hex}"
        self.put(probe, b"ok")
        try:
            if self.get(probe) != b"ok":
                raise StorageError(500, "Read-back mismatch.")
        finally:
            self.delete(probe)
        return f"Folder {self.root.resolve()} is writable."


# ── S3 / MinIO (AWS Signature V4, no third-party dependency) ──────────────


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class S3Backend:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 region: str = "us-east-1", prefix: str = "", path_style: bool = True):
        self.endpoint = endpoint.rstrip("/")
        self.bucket, self.access_key, self.secret_key = bucket, access_key, secret_key
        self.region = region or "us-east-1"
        self.prefix = prefix.strip("/")
        self.path_style = path_style

    def _url_and_host(self, key: str) -> tuple[str, str, str]:
        u = urllib.parse.urlsplit(self.endpoint)
        object_key = "/".join(p for p in (self.prefix, _safe_key(key) if key else "") if p)
        quoted = urllib.parse.quote(object_key, safe="/~")
        if self.path_style:
            host, path = u.netloc, f"/{self.bucket}" + (f"/{quoted}" if quoted else "")
        else:
            host, path = f"{self.bucket}.{u.netloc}", ("/" + quoted if quoted else "/")
        return f"{u.scheme}://{host}{path}", host, path

    def _request(self, method: str, key: str, body: bytes = b"", content_type: str = "",
                 query: str = "") -> bytes:
        url, host, path = self._url_and_host(key)
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        if content_type:
            headers["content-type"] = content_type
        signed = ";".join(sorted(headers))
        canonical = "\n".join([
            method, path, query,
            "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash,
        ])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = _sign(_sign(_sign(_sign(("AWS4" + self.secret_key).encode(), day), self.region), "s3"), "aws4_request")
        signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        auth = f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
        req = urllib.request.Request(url + (f"?{query}" if query else ""), data=body if method in ("PUT", "POST") else None,
                                     method=method)
        for name, value in headers.items():
            if name != "host":
                req.add_header(name, value)
        req.add_header("Authorization", auth)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - admin-configured endpoint
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise StorageError(404, "The stored file is missing.") from exc
            detail = {403: "access denied (check keys and permissions)", 301: "wrong region or endpoint"}.get(
                exc.code, f"HTTP {exc.code}")
            raise StorageError(502, f"Object storage error: {detail}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StorageError(502, f"Cannot reach the object storage: {getattr(exc, 'reason', exc)}") from exc

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._request("PUT", key, data, content_type or "application/octet-stream")

    def get(self, key: str) -> bytes:
        return self._request("GET", key)

    def delete(self, key: str) -> None:
        try:
            self._request("DELETE", key)
        except StorageError as exc:
            if exc.status != 404:
                raise

    def check(self) -> str:
        probe = f".pathmind-check-{uuid.uuid4().hex}"
        self.put(probe, b"ok", "text/plain")
        try:
            if self.get(probe) != b"ok":
                raise StorageError(502, "Read-back mismatch.")
        finally:
            self.delete(probe)
        return f"Bucket {self.bucket} accepts writes and reads."


# ── configuration and registry ────────────────────────────────────────────

SECRET_FIELDS = {"secret_key"}
FIELDS = {
    "local": {"path"},
    "s3": {"endpoint", "bucket", "access_key", "secret_key", "region", "prefix", "path_style"},
}


def validate_config(kind: str, cfg: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if kind not in FIELDS:
        raise StorageError(400, "Kind must be local or s3.")
    out = {k: cfg.get(k) for k in FIELDS[kind] if k in cfg}
    if previous:  # blank secret = keep
        for s in SECRET_FIELDS & FIELDS[kind]:
            if not out.get(s):
                out[s] = previous.get(s, "")
    if kind == "local":
        raw = str(out.get("path") or "").strip()
        p = Path(raw).expanduser()
        if not raw or not p.is_absolute() or ".." in p.parts:
            raise StorageError(400, "Use an absolute folder path without “..”.")
        return {"path": str(p)}
    endpoint = str(out.get("endpoint") or "").strip()
    u = urllib.parse.urlsplit(endpoint)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise StorageError(400, "Endpoint must start with http:// or https://.")
    bucket = str(out.get("bucket") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-_]{1,62}", bucket):
        raise StorageError(400, "Bucket name is not valid.")
    if not str(out.get("access_key") or "").strip() or not str(out.get("secret_key") or "").strip():
        raise StorageError(400, "Access key and secret key are required.")
    return {
        "endpoint": f"{u.scheme}://{u.netloc}",
        "bucket": bucket,
        "access_key": str(out["access_key"]).strip(),
        "secret_key": str(out["secret_key"]).strip(),
        "region": str(out.get("region") or "us-east-1").strip(),
        "prefix": str(out.get("prefix") or "").strip().strip("/"),
        "path_style": bool(out.get("path_style", True)),
    }


def build(kind: str, cfg: dict[str, Any]) -> Backend:
    if kind == "local":
        return LocalBackend(cfg["path"])
    return S3Backend(cfg["endpoint"], cfg["bucket"], cfg["access_key"], cfg["secret_key"],
                     cfg.get("region", "us-east-1"), cfg.get("prefix", ""), bool(cfg.get("path_style", True)))


def _implicit_root() -> str:
    from pathmind.multi_user.paths import SYSTEM_ROOT

    return str(SYSTEM_ROOT / "objects")


def _default_row(db: Session) -> StorageBackend | None:
    return (db.query(StorageBackend).filter_by(is_default=True, enabled=True).first())


def _db():
    from pathmind.database.connection import get_db_session

    return get_db_session()


def save(namespace: str, name: str, data: bytes, content_type: str = "", *, db: Session | None = None) -> str:
    """Write ``data`` to the default backend and return its reference.

    Pass the caller's ``db`` when it holds an open write transaction: a second
    session on SQLite would wait for (or, sharing the connection, roll back) it.
    """
    safe = re.sub(r"[^\w.\- ]+", "_", name)[:150] or "file"
    key = f"{namespace.strip('/')}/{uuid.uuid4().hex[:12]}-{safe}"
    if db is not None:
        return _save_with(db, key, data, content_type)
    with _db() as own:
        return _save_with(own, key, data, content_type)


def _save_with(db: Session, key: str, data: bytes, content_type: str) -> str:
    row = _default_row(db)
    if row is None:
        LocalBackend(_implicit_root()).put(key, data, content_type)
        return f"{IMPLICIT_ID}|{key}"
    build(row.kind, row.config).put(key, data, content_type)
    return f"{row.id}|{key}"


def _resolve(ref: str, db: Session | None = None) -> tuple[Backend | None, str]:
    if "|" not in ref:  # legacy absolute path
        p = Path(ref)
        return LocalBackend(str(p.parent)), p.name
    bid, key = ref.split("|", 1)
    if bid == IMPLICIT_ID:
        return LocalBackend(_implicit_root()), key
    if db is not None:
        return _backend_row(db, bid), key
    with _db() as own:
        return _backend_row(own, bid), key


def _backend_row(db: Session, bid: str) -> Backend:
    row = db.get(StorageBackend, bid)
    if row is None:
        raise StorageError(404, "The storage holding this file was removed.")
    return build(row.kind, row.config)


def load(ref: str, *, db: Session | None = None) -> bytes:
    backend, key = _resolve(ref, db)
    return backend.get(key)  # type: ignore[union-attr]


def remove(ref: str) -> None:
    try:
        backend, key = _resolve(ref)
        backend.delete(key)  # type: ignore[union-attr]
    except StorageError:
        pass  # deleting a record must not fail because the bytes are already gone


def count_refs(db: Session, backend_id: str) -> int:
    """Files that still live in ``backend_id``."""
    from pathmind.database.models import BranchDocument, BranchRecommendation, KnowledgeSource

    prefix = f"{backend_id}|"
    n = db.query(KnowledgeSource).filter(KnowledgeSource.blob_path.like(prefix + "%")).count()
    n += db.query(BranchDocument).filter(BranchDocument.ref.like(prefix + "%")).count()
    for rec in db.query(BranchRecommendation).all():
        n += sum(1 for d in (rec.documents or []) if str(d.get("ref", "")).startswith(prefix))
    return n
