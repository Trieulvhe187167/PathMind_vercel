"""One resumable, single-writer backend per environment on Vercel Sandbox.

The Drive preserves the application's existing filesystem semantics. Neon is
used for relational application data and a transaction-scoped boot lock, so
concurrent gateway instances cannot launch competing backend processes.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import psycopg
from vercel import sandbox
from vercel.sandbox import SandboxResources, SnapshotRetention, SnapshotSource

SOURCE = "/vercel/PathMind_vercel"
PYTHON = "/vercel/pathmind-venv/bin/python"
PORT = 8001


class BackendUnavailable(RuntimeError):
    pass


def environment_key() -> str:
    if os.environ.get("VERCEL_ENV") == "production":
        return "production"
    branch = os.environ.get("VERCEL_GIT_COMMIT_REF", "development")
    return "preview-" + hashlib.sha256(branch.encode()).hexdigest()[:12]


def revision() -> str:
    value = os.environ.get("VERCEL_GIT_COMMIT_SHA", "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise BackendUnavailable("The gateway requires an immutable Git commit SHA")
    return value


def forwarded_headers(raw: list[tuple[bytes, bytes]], secret: str) -> list[tuple[str, str]]:
    blocked = {
        b"host", b"connection", b"upgrade", b"transfer-encoding", b"keep-alive",
        b"proxy-authorization", b"proxy-authenticate", b"te", b"trailer",
        b"x-pathmind-gateway",
    }
    headers = [(key.decode("latin1"), value.decode("latin1")) for key, value in raw if key.lower() not in blocked]
    headers.append(("x-pathmind-gateway", secret))
    return headers


@asynccontextmanager
async def boot_lock(name: str):
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise BackendUnavailable("DATABASE_URL is not configured")
    key = int.from_bytes(hashlib.sha256(("pathmind-boot:" + name).encode()).digest()[:8], "big", signed=True)
    async with await psycopg.AsyncConnection.connect(url, connect_timeout=10) as connection:
        await connection.execute("SET LOCAL lock_timeout = '45s'")
        await connection.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        yield


class BackendRuntime:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), follow_redirects=False, trust_env=False)
        self._lock = asyncio.Lock()
        self._url: str | None = None
        self._checked_at = 0.0

    def invalidate(self) -> None:
        self._url = None

    @property
    def secret(self) -> str:
        value = os.environ.get("PATHMIND_GATEWAY_SECRET", "")
        if len(value) < 32:
            raise BackendUnavailable("PATHMIND_GATEWAY_SECRET is not configured")
        return value

    async def ready(self, url: str, sha: str) -> bool:
        try:
            response = await self.client.get(
                url + "/health/ready", headers={"x-pathmind-gateway": self.secret}, timeout=3,
            )
            return response.status_code == 200 and response.headers.get("x-pathmind-revision") == sha
        except httpx.HTTPError:
            return False

    async def ensure(self) -> str:
        if self._url and time.monotonic() - self._checked_at < 5:
            return self._url
        async with self._lock:
            if self._url and time.monotonic() - self._checked_at < 5:
                return self._url
            sha = revision()
            if self._url and await self.ready(self._url, sha):
                self._checked_at = time.monotonic()
                return self._url
            key = environment_key()
            name = "pathmind-" + key + "-api"
            snapshot = os.environ.get("PATHMIND_BACKEND_SNAPSHOT_ID", "")
            if not snapshot:
                raise BackendUnavailable("The backend dependency snapshot is not configured")

            # Lock includes resuming a stopped sandbox and attaching the Drive.
            # A Drive permits one writer; all API traffic for this environment
            # reaches this same backend, while preview data remains separate.
            async with boot_lock(name):
                drive, _ = await sandbox.get_or_create_drive(
                    name="pathmind-runtime-" + key, max_size_bytes=1024**3, region="sin1",
                )
                box, _ = await sandbox.get_or_create_sandbox(
                    name=name, source=SnapshotSource(type="snapshot", snapshot_id=snapshot),
                    ports=[PORT], mounts={"/runtime": drive}, persistent=True,
                    resources=SandboxResources(vcpus=2), execution_time_limit=1800, region="sin1",
                    snapshot_retention=SnapshotRetention(count=1, delete_evicted=True),
                )
                routes = [route.url.rstrip("/") for route in box.routes if route.port == PORT]
                if not routes or urlsplit(routes[0]).scheme != "https":
                    raise BackendUnavailable("The backend has no HTTPS route")
                url = routes[0]
                if not await self.ready(url, sha):
                    await self.start(box, sha, key)
                    for _ in range(60):
                        if await self.ready(url, sha):
                            break
                        await asyncio.sleep(1)
                    else:
                        raise BackendUnavailable("The backend did not become ready")
            self._url, self._checked_at = url, time.monotonic()
            return url

    async def start(self, box, sha: str, key: str) -> None:
        # Stop only the app process created by this gateway. Dependency tools
        # and unrelated processes in the environment are never selected.
        for process in await box.query_processes():
            if "pathmind.cloud.native_app:app" in process.args and process.returncode is None:
                await process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    await process.kill()
                    await process.wait()

        result = await box.run_process("git", ["-C", SOURCE, "rev-parse", "HEAD"], capture_output=True, check=True)
        if (result.stdout or "").strip() != sha:
            await box.run_process("git", ["-C", SOURCE, "fetch", "--depth=1", "origin", sha], capture_output=True, check=True)
            await box.run_process("git", ["-C", SOURCE, "checkout", "--detach", sha], capture_output=True, check=True)

        public_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") if key == "production" else os.environ.get("VERCEL_URL")
        native_env = {
            "PATHMIND_HOME": "/runtime",
            "AUTH_ENABLED": "true", "PATHMIND_AUTH_ENABLED": "true",
            "AUTH_COOKIE_SECURE": "true", "PATHMIND_TRUST_PROXY": "1",
            "BILLING_DEMO_MODE": "false",
            "PATHMIND_GATEWAY_SECRET": self.secret,
            "PATHMIND_SOURCE_REVISION": sha,
            "DATABASE_URL": os.environ.get("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"],
            "DATABASE_SCHEMA": "pathmind_" + key.replace("-", "_"),
        }
        if public_host:
            native_env["PATHMIND_PUBLIC_URL"] = "https://" + public_host
        # Explicit allow-list: the native app receives no Vercel control-plane
        # credentials, OIDC token, or gateway process environment wholesale.
        for name in ("PATHMIND_SECRETS_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TLS"):
            if value := os.environ.get(name):
                native_env[name] = value
        await box.create_process(
            PYTHON, ["-m", "uvicorn", "pathmind.cloud.native_app:app", "--host", "0.0.0.0", "--port", str(PORT), "--no-access-log"],
            cwd=SOURCE, env=native_env,
        )
