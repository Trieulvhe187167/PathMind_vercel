"""Settings, read from environment variables once at start-up.

PATHMIND_DATA_DIR      where the database and secret key live (default: <repo>/data)
DATABASE_URL           SQLAlchemy URL (default: sqlite in the data dir)
PATHMIND_SECRET_KEY    signing key for session tokens (default: generated into data/secret.key)
SESSION_HOURS          how long a sign-in lasts (default 168 = 7 days)
COOKIE_SECURE          "true" behind HTTPS so the session cookie is Secure
CORS_ORIGINS           comma separated, only needed when the web app is on another origin
LOGIN_MAX_FAILURES     failed sign-ins allowed per IP+account in LOGIN_LOCK_MINUTES (default 5 / 15)
PATHMIND_SECRETS_KEY   key for encrypting stored secrets (API keys...); default derived from the secret key
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    secret_key: str
    session_hours: int = 168
    cookie_secure: bool = False
    cors_origins: list[str] = field(default_factory=list)
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    cookie_name: str = "pm_session"
    # Tests run background tasks synchronously and without the scheduler thread.
    inline_tasks: bool = False
    run_scheduler: bool = True
    # Calls to AI providers: tests use the built-in "mock" provider, so nothing leaves the machine.
    llm_timeout_seconds: float = 120.0

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def _secret(data_dir: Path) -> str:
    env = os.getenv("PATHMIND_SECRET_KEY", "").strip()
    if env:
        return env
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key, encoding="utf-8")
    return key


def load_settings() -> Settings:
    data_dir = Path(os.getenv("PATHMIND_DATA_DIR") or ROOT / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        database_url=os.getenv("DATABASE_URL") or f"sqlite:///{(data_dir / 'pathmind.db').as_posix()}",
        secret_key=_secret(data_dir),
        session_hours=max(1, _int("SESSION_HOURS", 168)),
        cookie_secure=_bool("COOKIE_SECURE"),
        cors_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()],
        login_max_failures=max(1, _int("LOGIN_MAX_FAILURES", 5)),
        login_lock_minutes=max(1, _int("LOGIN_LOCK_MINUTES", 15)),
    )
