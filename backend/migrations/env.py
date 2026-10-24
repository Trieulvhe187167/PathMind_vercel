"""Alembic environment: target = every model in app.models, URL = DATABASE_URL."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers every table)
from app.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see local.env.bat).")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
