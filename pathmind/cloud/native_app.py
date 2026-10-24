"""Private backend entrypoint on a mounted Vercel Drive.

The gateway secret authenticates the transport. The existing app still
authenticates each user and enforces authorization for API and WebSocket calls.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path

_secret = os.environ.get("PATHMIND_GATEWAY_SECRET", "")
if len(_secret) < 32:
    raise RuntimeError("PATHMIND_GATEWAY_SECRET is required")
if os.environ.get("PATHMIND_HOME") != "/runtime" or not Path("/runtime").is_mount():
    raise RuntimeError("The persistent Vercel Drive must be mounted at /runtime")

os.environ["AUTH_ENABLED"] = "true"
os.environ["PATHMIND_AUTH_ENABLED"] = "true"
os.environ["AUTH_COOKIE_SECURE"] = "true"

from pathmind.api.main import app as _application  # noqa: E402


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await _application(scope, receive, send)
        return
    supplied = dict(scope.get("headers", [])).get(b"x-pathmind-gateway", b"")
    if not hmac.compare_digest(supplied, _secret.encode()):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail":"Forbidden"}'})
        return

    if scope["type"] == "http" and scope["path"] == "/api/cloud/owner-status":
        from pathmind.multi_user.identity import load_users

        has_owner = any(user.get("role") == "admin" and not user.get("disabled") for user in load_users().values())
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps({"has_owner": has_owner}).encode()})
        return

    async def send_with_revision(message):
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), (b"x-pathmind-revision", os.environ.get("PATHMIND_SOURCE_REVISION", "").encode())]}
        await send(message)

    await _application(scope, receive, send_with_revision)
