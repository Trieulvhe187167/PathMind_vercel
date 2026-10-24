"""Migrations against a real PostgreSQL database (skipped unless TEST_DATABASE_URL is set).

TEST_DATABASE_URL must point to an EMPTY scratch database: the test upgrades it to head,
checks every model table exists and that models and migrations agree, then downgrades to base.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app import models  # noqa: F401
from app.db import Base

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="TEST_DATABASE_URL not set")


def _config():
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes["database_url"] = URL
    return cfg


def test_upgrade_check_downgrade():
    from alembic import command

    cfg = _config()
    command.upgrade(cfg, "head")
    engine = create_engine(URL)
    try:
        tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert tables == set(Base.metadata.tables), sorted(set(Base.metadata.tables) ^ tables)
        indexes = {i["name"] for i in inspect(engine).get_indexes("users")}
        assert "uq_users_username_lower" in indexes
        command.check(cfg)  # raises if the models changed without a migration
    finally:
        command.downgrade(cfg, "base")
        left = set(inspect(engine).get_table_names()) - {"alembic_version"}
        engine.dispose()
    assert left == set()


def test_roles_and_case_insensitive_username_on_postgres():
    from alembic import command
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    cfg = _config()
    command.upgrade(cfg, "head")
    engine = create_engine(URL)
    try:
        with Session(engine) as db:
            db.add(models.User(username="Alice", password_hash="x"))
            db.commit()
            db.add(models.User(username="alice", password_hash="x"))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            u = db.query(models.User).one()
            assert u.role == "learner" and u.theme == "light"
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")
