"""FastAPI application factory.

Run from the ``backend`` folder: ``uvicorn app.main:create_app --factory --port 8001``.
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, load_settings
from .db import init_db, make_engine, make_session_factory
from .ratelimit import LoginThrottle
from .routers import ROUTERS
from .services import jobs_builtin, llm, secretbox, seed, tasks, verification

log = logging.getLogger("pathmind")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    secretbox.configure(settings.secret_key)
    verification.configure(settings.secret_key)
    llm.TIMEOUT = settings.llm_timeout_seconds
    app = FastAPI(title="PathMind API", version="0.2.0")
    engine = make_engine(settings.database_url)
    # SQLite (tests, quick local runs) gets its tables created directly. Any other database
    # (PostgreSQL) is managed by Alembic migrations: run `alembic upgrade head` (setup.bat / start.bat do).
    if settings.database_url.startswith("sqlite"):
        init_db(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.login_throttle = LoginThrottle(settings.login_max_failures, settings.login_lock_minutes * 60)
    # Serialises "count then write" account changes (first user becomes admin, last-admin guard)
    # so concurrent requests in the worker thread pool cannot both pass the check.
    app.state.accounts_lock = threading.Lock()
    app.state.tasks = tasks.TaskRunner(app.state.session_factory, inline=settings.inline_tasks)
    jobs_builtin.register(app)

    try:
        with app.state.session_factory() as db:
            seed.run(db)
            tasks.ensure_builtin_jobs(db)
    except Exception:  # noqa: BLE001 - e.g. migrations not applied yet; the app still starts
        log.exception("Seeding default data failed")

    if settings.run_scheduler:
        app.state.scheduler = tasks.Scheduler(app.state.session_factory)
        app.state.scheduler.start()

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )

    @app.get("/api/health")
    def health():
        return {"ok": True}

    for router, prefix, tag in ROUTERS:
        app.include_router(router, prefix=prefix, tags=[tag])
    return app
