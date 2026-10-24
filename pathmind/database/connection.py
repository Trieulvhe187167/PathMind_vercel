"""Database engine, session management, and lifecycle."""

from collections.abc import Generator
from contextlib import contextmanager
import logging
import os
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from pathmind.services.path_service import get_path_service

logger = logging.getLogger(__name__)

Base = declarative_base()

_DATABASE_URL = os.environ.get("DATABASE_URL")

if not _DATABASE_URL:
    db_path = get_path_service().user_data_dir / "pathmind.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _DATABASE_URL = f"sqlite:///{db_path}"

# PostgreSQL only: keep PathMind's tables in their own schema (``DATABASE_SCHEMA``),
# so the database can be shared with other apps without table-name clashes.
_DATABASE_SCHEMA = os.environ.get("DATABASE_SCHEMA", "").strip()
if _DATABASE_SCHEMA and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", _DATABASE_SCHEMA):
    raise ValueError("DATABASE_SCHEMA must be a lowercase identifier (letters, digits, _).")

# SQLite requires check_same_thread=False for multi-threaded FastAPI workers
if _DATABASE_URL.startswith("sqlite"):
    _connect_args: dict = {"check_same_thread": False}
elif _DATABASE_SCHEMA and _DATABASE_URL.startswith("postgresql"):
    _connect_args = {"options": f"-csearch_path={_DATABASE_SCHEMA}"}
else:
    _connect_args = {}

engine = create_engine(
    _DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for yielding database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager for standalone DB sessions outside FastAPI route injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add columns that exist on the models but not in the DB.

    ``Base.metadata.create_all`` only creates *missing tables*; it never alters an
    existing one. Databases created by an earlier build therefore lack the newer
    subscription/payment columns. Every added column is nullable, so a plain
    ``ALTER TABLE ... ADD COLUMN`` is safe on both SQLite and PostgreSQL.
    For anything more involved, switch to Alembic.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable:
                    logger.warning(
                        "Skipping NOT NULL column %s.%s — add it with a real migration.",
                        table.name,
                        column.name,
                    )
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                )
                logger.info("DB migration: added column %s.%s", table.name, column.name)


def init_database() -> None:
    """Create tables, add missing columns, and seed initial defaults."""
    from pathmind.database import models  # noqa: F401  (register models on Base)
    from pathmind.database.init_db import seed_initial_data

    if _DATABASE_SCHEMA and engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_DATABASE_SCHEMA}"'))
    Base.metadata.create_all(bind=engine)
    try:
        _add_missing_columns()
    except Exception as exc:  # pragma: no cover - never block startup on this
        logger.warning("DB column migration failed: %s", exc)
    seed_initial_data()
    from pathmind.database.maintenance import run_startup_repairs

    run_startup_repairs(engine, SessionLocal)
