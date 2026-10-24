from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}",
        secret_key="test-secret",
        login_max_failures=3,
        inline_tasks=True,
        run_scheduler=False,
    )


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    return TestClient(app)


def make_client(app) -> TestClient:
    return TestClient(app)


def register(c: TestClient, username: str, password: str = "secret123", email: str | None = None, **extra):
    body = {"username": username, "password": password, "confirm_password": password, **extra}
    if email:
        body["email"] = email
    return c.post("/api/auth/register", json=body)
