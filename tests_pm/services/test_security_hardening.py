"""API keys encrypted at rest, masked keys not carried to a new endpoint, login lockout."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def secret_key(monkeypatch):
    from pathmind.services import secret_box

    monkeypatch.setenv("PATHMIND_SECRETS_KEY", "unit-test-key")
    secret_box.reset_cache()
    yield
    secret_box.reset_cache()


def _catalog(api_key: str, base_url: str = "https://api.example.com/v1") -> dict:
    return {
        "version": 1,
        "connections": [
            {"id": "c1", "provider": "openai", "api_key": api_key, "base_url": base_url}
        ],
        "services": {
            "llm": {
                "active_profile_id": "p1",
                "active_model_id": None,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Main",
                        "base_url": base_url,
                        "api_key": api_key,
                        "extra_headers": {"X-Token": "hdr-secret"},
                        "models": [],
                    }
                ],
            }
        },
    }


def test_catalog_keys_are_encrypted_on_disk(tmp_path, secret_key):
    from pathmind.services.config.model_catalog import ModelCatalogService

    path = tmp_path / "model_catalog.json"
    service = ModelCatalogService(path)
    service.save(_catalog("sk-live-123"))

    raw = path.read_text(encoding="utf-8")
    assert "sk-live-123" not in raw
    assert "hdr-secret" not in raw
    assert "enc:v1:" in raw

    loaded = service.load()
    profile = loaded["services"]["llm"]["profiles"][0]
    assert profile["api_key"] == "sk-live-123"
    assert profile["extra_headers"]["X-Token"] == "hdr-secret"


def test_plaintext_catalog_is_migrated_on_load(tmp_path, secret_key):
    from pathmind.services.config.model_catalog import ModelCatalogService

    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps(_catalog("sk-plain-456")), encoding="utf-8")
    loaded = ModelCatalogService(path).load()
    assert loaded["services"]["llm"]["profiles"][0]["api_key"] == "sk-plain-456"
    assert "sk-plain-456" not in path.read_text(encoding="utf-8")


def test_wrong_key_never_destroys_stored_secrets(tmp_path, monkeypatch, secret_key):
    from pathmind.services import secret_box
    from pathmind.services.config.model_catalog import ModelCatalogService

    path = tmp_path / "model_catalog.json"
    ModelCatalogService(path).save(_catalog("sk-live-789"))
    monkeypatch.setenv("PATHMIND_SECRETS_KEY", "a-different-key")
    secret_box.reset_cache()
    service = ModelCatalogService(path)
    loaded = service.load()
    assert loaded["services"]["llm"]["profiles"][0]["api_key"].startswith("enc:v1:")
    service.save(loaded)  # a save under the wrong key keeps the ciphertext
    monkeypatch.setenv("PATHMIND_SECRETS_KEY", "unit-test-key")
    secret_box.reset_cache()
    recovered = ModelCatalogService(path).load()
    assert recovered["services"]["llm"]["profiles"][0]["api_key"] == "sk-live-789"


def test_masked_key_is_kept_for_same_host():
    from pathmind.services.config.model_catalog import (
        CATALOG_SECRET_MASK,
        restore_catalog_secrets,
    )

    current = _catalog("sk-real")
    proposed = _catalog(CATALOG_SECRET_MASK, base_url="https://api.example.com/v2")
    restored = restore_catalog_secrets(proposed, current)
    assert restored["services"]["llm"]["profiles"][0]["api_key"] == "sk-real"
    assert restored["connections"][0]["api_key"] == "sk-real"


def test_masked_key_is_dropped_when_endpoint_host_changes():
    from pathmind.services.config.model_catalog import (
        CATALOG_SECRET_MASK,
        restore_catalog_secrets,
    )

    current = _catalog("sk-real")
    proposed = _catalog(CATALOG_SECRET_MASK, base_url="https://attacker.example.net/v1")
    proposed["services"]["llm"]["profiles"][0]["extra_headers"] = {"X-Token": CATALOG_SECRET_MASK}
    restored = restore_catalog_secrets(proposed, current)
    profile = restored["services"]["llm"]["profiles"][0]
    assert profile["api_key"] == ""
    assert profile["extra_headers"]["X-Token"] == ""
    assert restored["connections"][0]["api_key"] == ""


def test_new_key_for_new_host_is_accepted():
    from pathmind.services.config.model_catalog import restore_catalog_secrets

    current = _catalog("sk-real")
    proposed = _catalog("sk-new", base_url="https://other.example.org/v1")
    restored = restore_catalog_secrets(proposed, current)
    assert restored["services"]["llm"]["profiles"][0]["api_key"] == "sk-new"


def test_login_locks_after_repeated_failures(monkeypatch):
    from pathmind.api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "_login_failures", {})
    monkeypatch.setattr(auth_router, "_login_locked_until", {})
    monkeypatch.setattr(auth_router, "authenticate", lambda username, password: None)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    with TestClient(app) as client:
        codes = [
            client.post("/api/auth/login", json={"username": "victim", "password": "x"}).status_code
            for _ in range(auth_router._LOGIN_MAX_FAILURES)
        ]
        assert codes == [401] * auth_router._LOGIN_MAX_FAILURES
        locked = client.post("/api/auth/login", json={"username": "victim", "password": "x"})
        assert locked.status_code == 429
        assert locked.json()["detail"]["code"] == "login_locked"
        # A different account from the same client is not affected.
        other = client.post("/api/auth/login", json={"username": "someone", "password": "x"})
        assert other.status_code == 401
