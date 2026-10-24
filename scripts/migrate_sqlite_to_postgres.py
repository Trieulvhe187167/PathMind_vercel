"""Copy PathMind's relational data from SQLite to PostgreSQL.

Usage (from the project root):

    python scripts/migrate_sqlite_to_postgres.py --target postgresql+psycopg://user:pass@localhost:5432/pathmind --schema pathmind_app

``--target`` defaults to ``DATABASE_URL``, ``--schema`` to ``DATABASE_SCHEMA``
and ``--source`` to ``data/user/pathmind.db``. The schema is created when it
does not exist (only the database owner's CREATE privilege is needed). Nothing is ever deleted: the SQLite file is only read, and a target
table that already holds rows is skipped (so the script can be re-run after a
partial copy). Rows PostgreSQL refuses (e.g. an orphan foreign key that SQLite
never enforced) are reported and left out.

Only the relational database moves. Files under ``data/`` (chats, KBs, the
identity store ``auth_users.json``) stay where they are.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from sqlalchemy import Integer, String, create_engine, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BATCH = 500


def _target_engine(url: str, schema: str):
    if not schema:
        return create_engine(url)
    with create_engine(url).begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})


def _clean(value, column):
    if isinstance(value, str):
        value = value.replace("\x00", "")
        length = getattr(column.type, "length", None)
        if isinstance(column.type, String) and length and len(value) > length:
            value = value[:length]
    return value


def migrate(source_url: str, target_url: str, schema: str = "") -> int:
    from pathmind.database.connection import Base
    from pathmind.database import models  # noqa: F401  (register every table)

    src = create_engine(source_url)
    dst = _target_engine(target_url, schema)
    Base.metadata.create_all(dst)

    src_tables = set(inspect(src).get_table_names())
    skipped_rows = 0
    for table in Base.metadata.sorted_tables:
        if table.name not in src_tables:
            continue
        src_cols = {c["name"] for c in inspect(src).get_columns(table.name)}
        cols = [c for c in table.columns if c.name in src_cols]
        with dst.connect() as conn:
            if conn.execute(select(table.c[cols[0].name]).limit(1)).first() is not None:
                print(f"skip {table.name}: target already has rows")
                continue
        with src.connect() as sconn:
            rows = [dict(r._mapping) for r in sconn.execute(select(*cols))]
        copied = 0
        with dst.connect() as conn:
            for start in range(0, len(rows), BATCH):
                chunk = [{k: _clean(v, table.c[k]) for k, v in r.items()} for r in rows[start:start + BATCH]]
                try:
                    with conn.begin_nested():
                        conn.execute(table.insert(), chunk)
                    copied += len(chunk)
                except SQLAlchemyError:
                    for row in chunk:  # find the offending rows one by one
                        try:
                            with conn.begin_nested():
                                conn.execute(table.insert(), [row])
                            copied += 1
                        except SQLAlchemyError as exc:
                            skipped_rows += 1
                            key = {c.name: row.get(c.name) for c in table.primary_key.columns}
                            print(f"  ! {table.name} {key}: {str(exc.orig).splitlines()[0]}")
            conn.commit()
            # Integer primary keys: move the sequence past the copied ids.
            for col in table.primary_key.columns:
                if isinstance(col.type, Integer) and col.autoincrement in (True, "auto"):
                    seq = conn.execute(
                        text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col.name}
                    ).scalar()
                    if seq:
                        conn.execute(
                            text(f'SELECT setval(:s, COALESCE((SELECT MAX("{col.name}") FROM "{table.name}"), 0) + 1, false)'),
                            {"s": seq},
                        )
                        conn.commit()
        print(f"{table.name}: {copied}/{len(rows)}")
    return skipped_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default=f"sqlite:///{ROOT / 'data' / 'user' / 'pathmind.db'}")
    parser.add_argument("--target", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--schema", default=os.environ.get("DATABASE_SCHEMA", ""))
    args = parser.parse_args()
    if not args.target.startswith("postgresql"):
        parser.error("--target (or DATABASE_URL) must be a postgresql:// URL")
    skipped = migrate(args.source, args.target, args.schema)
    print("done" + (f", {skipped} row(s) left out (see above)" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
