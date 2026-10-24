"""Create the PostgreSQL role and databases PathMind uses, then record the URLs in local.env.bat.

Run once per machine with a PostgreSQL superuser (the password is only used for this step):

    set PGPASSWORD=<postgres password>
    .venv\\Scripts\\python backend\\scripts\\create_database.py

Connection to the server uses the libpq variables PGHOST (default localhost), PGPORT (5432),
PGUSER (postgres) and PGPASSWORD. Creates, if missing:
  - role ``pathmind`` with a random password (LOGIN, no superuser),
  - database ``pathmind`` (the app) and ``pathmind_test`` (migration tests), both owned by it.
Then writes DATABASE_URL and TEST_DATABASE_URL into local.env.bat (git-ignored).
Running it again keeps an existing password if local.env.bat already has one.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import sys

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "local.env.bat"
ROLE = os.environ.get("PATHMIND_DB_ROLE", "pathmind")
DATABASES = (ROLE, f"{ROLE}_test")


def _existing_password() -> str | None:
    if not ENV_FILE.exists():
        return None
    m = re.search(rf"postgresql\+psycopg://{ROLE}:([^@]+)@", ENV_FILE.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _write_env(urls: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else ["@echo off", "rem Local settings (git-ignored)."]
    for key, value in urls.items():
        entry = f"set {key}={value}"
        for i, line in enumerate(lines):
            if line.strip().lower().startswith(f"set {key.lower()}="):
                lines[i] = entry
                break
        else:
            lines.append(entry)
    ENV_FILE.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def main() -> int:
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    password = _existing_password() or secrets.token_urlsafe(24)
    try:
        conn = psycopg.connect(dbname="postgres", host=host, port=port, user=os.environ.get("PGUSER", "postgres"), autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"Cannot connect to PostgreSQL at {host}:{port}: {exc}".strip())
        return 1
    with conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone()
        if exists:
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(ROLE), sql.Literal(password)))
            print(f"role {ROLE}: exists, password set")
        else:
            conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(ROLE), sql.Literal(password)))
            print(f"role {ROLE}: created")
        for db in DATABASES:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
                print(f"database {db}: exists")
            else:
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(db), sql.Identifier(ROLE)))
                print(f"database {db}: created")
    base = f"postgresql+psycopg://{ROLE}:{password}@{host}:{port}"
    _write_env({"DATABASE_URL": f"{base}/{DATABASES[0]}", "TEST_DATABASE_URL": f"{base}/{DATABASES[1]}"})
    print(f"wrote DATABASE_URL and TEST_DATABASE_URL to {ENV_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
