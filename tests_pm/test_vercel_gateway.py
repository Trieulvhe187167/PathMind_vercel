"""Gateway authorization, environment isolation and streaming proxy contracts."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "cloud-gateway"))
    spec = importlib.util.spec_from_file_location("gateway_test_app", ROOT / "cloud-gateway" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {"owner": False, "requests": []}

    class Chunks(httpx.AsyncByteStream):
        def __init__(self, data):
            self.data = data

        async def __aiter__(self):
            yield self.data[:3]
            yield self.data[3:]

    async def transport(request):
        if request.url.path == "/api/cloud/owner-status":
            return httpx.Response(200, json={"has_owner": state["owner"]})
        state["requests"].append(request)
        if request.url.path.rstrip("/") == "/api/auth/register":
            state["owner"] = True
            return httpx.Response(201, stream=Chunks(b'{"ok":true}'), headers={"content-type": "application/json"})
        return httpx.Response(200, stream=Chunks(b"proxied"), headers=[("set-cookie", "a=1; HttpOnly"), ("set-cookie", "b=2; Secure")])

    original = module.backend
    asyncio.run(original.client.aclose())
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    async def ensure():
        return "https://backend.example.invalid"

    module.backend = SimpleNamespace(ensure=ensure, secret="test-transport-secret", client=client, invalidate=lambda: None)
    monkeypatch.setenv("PATHMIND_BOOTSTRAP_TOKEN", "test-bootstrap-" + "x" * 48)
    with TestClient(module.app, base_url="https://testserver") as browser:
        yield module, browser, state


def test_proxy_preserves_cookies_and_replaces_untrusted_transport_header(gateway):
    module, browser, state = gateway
    response = browser.get("/api/example", headers={"x-pathmind-gateway": "attacker-value", "Cookie": "dt_token=test-user-token"})
    assert response.status_code == 200
    assert response.content == b"proxied"
    assert len(response.headers.get_list("set-cookie")) == 2
    assert state["requests"][0].headers.get_list("x-pathmind-gateway") == [module.backend.secret]
    assert state["requests"][0].headers["cookie"] == "dt_token=test-user-token"


@pytest.mark.parametrize("path", ["/api/auth/register", "/api/auth/register/"])
def test_first_registration_requires_owner_setup_cookie(gateway, path):
    _, browser, state = gateway
    response = browser.post(path, json={"username": "test-owner"})
    assert response.status_code == 403
    assert state["requests"] == []


def test_setup_link_is_single_use_and_registration_opens_after_owner_exists(gateway):
    _, browser, state = gateway
    token = "test-bootstrap-" + "x" * 48
    response = browser.get("/api/cloud/bootstrap", params={"token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/register"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()
    assert response.headers["referrer-policy"] == "no-referrer"
    assert browser.post("/api/auth/register", json={"username": "test-owner"}).status_code == 201
    assert state["owner"]
    assert browser.get("/api/cloud/bootstrap", params={"token": token}).status_code == 403
    browser.cookies.clear()
    assert browser.post("/api/auth/register", json={"username": "test-learner"}).status_code == 201


def test_owner_probe_is_not_public(gateway):
    _, browser, _ = gateway
    assert browser.get("/api/cloud/owner-status").status_code == 404


def test_preview_names_are_stable_and_separate_from_production(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "cloud-gateway"))
    import runtime

    monkeypatch.setenv("VERCEL_ENV", "production")
    assert runtime.environment_key() == "production"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "feature/../../production")
    key = runtime.environment_key()
    assert key.startswith("preview-") and "/" not in key
    assert runtime.environment_key() == key
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "main; shell command")
    with pytest.raises(runtime.BackendUnavailable):
        runtime.revision()


def test_native_transport_rejects_unauthorized_http_and_websockets(monkeypatch):
    secret = "test-transport-" + "x" * 48
    monkeypatch.setenv("PATHMIND_GATEWAY_SECRET", secret)
    monkeypatch.setenv("PATHMIND_HOME", "/runtime")
    monkeypatch.setenv("PATHMIND_SOURCE_REVISION", "a" * 40)
    monkeypatch.setattr(Path, "is_mount", lambda self: True)

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setitem(sys.modules, "pathmind.api.main", SimpleNamespace(app=application))
    spec = importlib.util.spec_from_file_location("native_guard_test", ROOT / "pathmind" / "cloud" / "native_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def call(kind, headers):
        messages = []

        async def send(message):
            messages.append(message)

        await module.app({"type": kind, "path": "/api/example", "headers": headers}, None, send)
        return messages

    assert asyncio.run(call("http", []))[0]["status"] == 403
    assert asyncio.run(call("websocket", [])) == [{"type": "websocket.close", "code": 1008}]
    result = asyncio.run(call("http", [(b"x-pathmind-gateway", secret.encode())]))
    assert result[0]["status"] == 200
    assert (b"x-pathmind-revision", b"a" * 40) in result[0]["headers"]


def test_old_deployment_cannot_stop_or_downgrade_current_backend(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "cloud-gateway"))
    import runtime

    calls = []

    class Box:
        async def run_process(self, command, args, **kwargs):
            calls.append(args)
            if args[-1] == "HEAD":
                return SimpleNamespace(stdout="b" * 40, returncode=0)
            if args[-1] == "--is-shallow-repository":
                return SimpleNamespace(stdout="true", returncode=0)
            return SimpleNamespace(stdout="", returncode=0)

        async def query_processes(self):
            pytest.fail("A superseded deployment must not stop a running process")

    async def check():
        service = runtime.BackendRuntime()
        try:
            with pytest.raises(runtime.BackendUnavailable, match="superseded"):
                await service.start(Box(), "a" * 40, "production")
        finally:
            await service.client.aclose()

    asyncio.run(check())
    assert any("--unshallow" in args for args in calls)
