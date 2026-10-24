"""Streaming HTTP and WebSocket gateway to the persistent Vercel backend."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import logging
import os

from fastapi import FastAPI, Request, WebSocket
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect

from runtime import BackendRuntime, forwarded_headers

logger = logging.getLogger("pathmind.gateway")
backend = BackendRuntime()


@asynccontextmanager
async def lifespan(app):
    yield
    await backend.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def forward_http(request: Request, path: str):
    try:
        canonical_path = "/" + path.rstrip("/")
        if canonical_path == "/api/cloud/owner-status":
            return JSONResponse({"detail": "Not found"}, status_code=404)
        base = await backend.ensure()
        if canonical_path == "/auth/callback":
            path = "api/auth/openai-codex/callback"
        if canonical_path in {"/api/cloud/bootstrap", "/api/auth/register"}:
            status = await backend.client.get(base + "/api/cloud/owner-status", headers={"x-pathmind-gateway": backend.secret})
            status.raise_for_status()
            has_owner = status.json()["has_owner"]
            expected = os.environ.get("PATHMIND_BOOTSTRAP_TOKEN", "")
            if canonical_path == "/api/cloud/bootstrap":
                supplied = request.query_params.get("token", "")
                if has_owner or len(expected) < 32 or not hmac.compare_digest(supplied, expected):
                    return JSONResponse({"detail": "Setup link is invalid or has already been used."}, status_code=403)
                result = RedirectResponse("/register", status_code=303)
                result.set_cookie("pm_cloud_setup", expected, httponly=True, secure=True, samesite="lax", max_age=900)
                result.headers["Referrer-Policy"] = "no-referrer"
                result.headers["Cache-Control"] = "no-store"
                return result
            supplied = request.cookies.get("pm_cloud_setup", "")
            if not has_owner and (len(expected) < 32 or not hmac.compare_digest(supplied, expected)):
                return JSONResponse({"detail": "The deployment owner must finish initial setup before public registration opens."}, status_code=403)
        target = base + "/" + path
        if request.url.query:
            target += "?" + request.url.query
        headers = forwarded_headers(request.headers.raw, backend.secret)
        upstream = backend.client.build_request(request.method, target, headers=headers, content=request.stream())
        response = await backend.client.send(upstream, stream=True)
    except Exception:
        backend.invalidate()
        logger.exception("Backend connection failed")
        return JSONResponse({"detail": "The backend is starting or temporarily unavailable. Please retry.", "retryable": True}, status_code=503, headers={"Retry-After": "5"})

    async def body():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    result = StreamingResponse(body(), status_code=response.status_code)
    excluded = {b"connection", b"transfer-encoding", b"keep-alive", b"content-length"}
    result.raw_headers = [(key, value) for key, value in response.headers.raw if key.lower() not in excluded]
    return result


@app.websocket("/{path:path}")
async def forward_websocket(socket: WebSocket, path: str):
    try:
        base = await backend.ensure()
        uri = "wss://" + base.removeprefix("https://") + "/" + path
        if socket.url.query:
            uri += "?" + socket.url.query
        headers = forwarded_headers(socket.headers.raw, backend.secret)
        headers = [(key, value) for key, value in headers if not key.lower().startswith("sec-websocket-") and key.lower() != "origin"]
        async with connect(uri, additional_headers=headers, origin=socket.headers.get("origin"), max_size=64 * 1024**2) as upstream:
            await socket.accept()

            async def to_backend():
                while True:
                    event = await socket.receive()
                    if event["type"] == "websocket.disconnect":
                        return
                    if event.get("bytes") is not None:
                        await upstream.send(event["bytes"])
                    elif event.get("text") is not None:
                        await upstream.send(event["text"])

            async def to_browser():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await socket.send_bytes(message)
                    else:
                        await socket.send_text(message)

            tasks = {asyncio.create_task(to_backend()), asyncio.create_task(to_browser())}
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await socket.close(code=1000)
            except RuntimeError:
                pass
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        backend.invalidate()
        logger.exception("Backend WebSocket connection failed")
        try:
            await socket.close(code=1013, reason="Backend temporarily unavailable; reconnect")
        except RuntimeError:
            pass
