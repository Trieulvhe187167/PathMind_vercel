"""Minimal S3-compatible client (AWS Signature V4) on httpx: put, get, delete, head bucket, presign.

Works with AWS S3, MinIO, Cloudflare R2, Viettel / FPT Cloud and other S3-compatible services.
Only what PathMind needs, so no boto3 dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from urllib.parse import quote, urlsplit

import httpx

EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class S3Error(RuntimeError):
    pass


@dataclass
class S3Config:
    bucket: str
    access_key: str
    secret_key: str
    region: str = ""
    endpoint_url: str = ""  # empty = AWS
    path_style: bool = False
    timeout: float = 30.0

    @property
    def region_or_default(self) -> str:
        return self.region or ("auto" if "r2.cloudflarestorage.com" in self.endpoint_url else "us-east-1")


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    k = _sign(("AWS4" + secret).encode("utf-8"), date)
    k = _sign(k, region)
    k = _sign(k, "s3")
    return _sign(k, "aws4_request")


class S3Client:
    def __init__(self, cfg: S3Config, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._transport = transport

    # ── URL building ────────────────────────────────────────────────────
    def _base(self) -> tuple[str, str]:
        """(scheme://host, path prefix) for the bucket."""
        cfg = self.cfg
        if cfg.endpoint_url:
            parts = urlsplit(cfg.endpoint_url.rstrip("/"))
            scheme, host = parts.scheme or "https", parts.netloc
            if cfg.path_style:
                return f"{scheme}://{host}", f"/{cfg.bucket}"
            return f"{scheme}://{cfg.bucket}.{host}", ""
        region = cfg.region_or_default
        host = "s3.amazonaws.com" if region == "us-east-1" else f"s3.{region}.amazonaws.com"
        if cfg.path_style:
            return f"https://{host}", f"/{cfg.bucket}"
        return f"https://{cfg.bucket}.{host}", ""

    def _url(self, key: str) -> tuple[str, str, str]:
        base, prefix = self._base()
        path = prefix + "/" + quote(key.lstrip("/"), safe="/-_.~")
        if path == "":
            path = "/"
        return base, path, urlsplit(base).netloc

    # ── Signing ─────────────────────────────────────────────────────────
    def _headers(self, method: str, host: str, path: str, query: str, payload_hash: str,
                 extra: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, str]:
        now = now or datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        region = self.cfg.region_or_default
        headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        for k, v in (extra or {}).items():
            headers[k.lower()] = v
        signed = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
        canonical = "\n".join([method, path or "/", query, canonical_headers, signed, payload_hash])
        scope = f"{date}/{region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        sig = hmac.new(_signing_key(self.cfg.secret_key, date, region), to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.cfg.access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
        )
        headers.pop("host")
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.cfg.timeout, transport=self._transport)

    def _check(self, r: httpx.Response, what: str) -> None:
        if r.status_code >= 300:
            snippet = r.text[:300].replace("\n", " ")
            raise S3Error(f"{what} failed ({r.status_code}): {snippet}")

    # ── Operations ──────────────────────────────────────────────────────
    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        base, path, host = self._url(key)
        h = self._headers("PUT", host, path, "", hashlib.sha256(data).hexdigest(), {"content-type": content_type})
        with self._client() as c:
            self._check(c.put(base + path, content=data, headers=h), "Upload")

    def get_object(self, key: str) -> bytes:
        base, path, host = self._url(key)
        h = self._headers("GET", host, path, "", EMPTY_SHA)
        with self._client() as c:
            r = c.get(base + path, headers=h)
            self._check(r, "Download")
            return r.content

    def delete_object(self, key: str) -> None:
        base, path, host = self._url(key)
        h = self._headers("DELETE", host, path, "", EMPTY_SHA)
        with self._client() as c:
            r = c.delete(base + path, headers=h)
            if r.status_code not in (200, 204, 404):
                self._check(r, "Delete")

    def check(self) -> None:
        """Write, read back and delete a small object: proves the credentials and bucket work."""
        key = "pathmind-connection-test.txt"
        self.put_object(key, b"ok", "text/plain")
        if self.get_object(key) != b"ok":
            raise S3Error("Read-back returned different content.")
        self.delete_object(key)

    def presign_get(self, key: str, seconds: int = 300, filename: str = "") -> str:
        base, path, host = self._url(key)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        region = self.cfg.region_or_default
        scope = f"{date}/{region}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.cfg.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(max(1, min(seconds, 604800))),
            "X-Amz-SignedHeaders": "host",
        }
        if filename:
            params["response-content-disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
        canonical = "\n".join(["GET", path, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        sig = hmac.new(_signing_key(self.cfg.secret_key, date, region), to_sign.encode(), hashlib.sha256).hexdigest()
        return f"{base}{path}?{query}&X-Amz-Signature={sig}"
