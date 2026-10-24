"""Database engine and sessions (SQLAlchemy 2)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# Stable constraint names so Alembic migrations are reproducible on PostgreSQL.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # Enforce foreign keys (and ON DELETE CASCADE) like PostgreSQL does.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    # create_all skips indexes on tables that already exist; add any that are missing.
    # (IF NOT EXISTS, because SQLite does not report expression indexes to checkfirst.)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                conn.execute(CreateIndex(index, if_not_exists=True))


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    db = factory()
    try:
        yield db
    finally:
        db.close()
