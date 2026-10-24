"""Startup integrity repairs for the relational database.

Idempotent and non-destructive; runs from ``init_database()`` on every start.

1. **Unique indexes** that older databases were created without
   (``create_all`` never alters existing tables). Exact duplicate rows are
   collapsed first — keeping one — so the index can be created. ``usage_logs``
   duplicates are *merged* (counters summed) rather than dropped.
2. **Identity mirror.** The identity store (``data/system/auth/users.json``)
   is the source of truth for accounts; the ``users`` table only mirrors it
   for foreign keys. Early builds seeded rows with other ids, so a real account
   could end up mirrored as ``admin#u_…``. Here every identity account gets a
   row with its real id and username; rows with no matching account are kept
   (their history stays intact) but renamed out of the way and deactivated.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

# (table, columns, index name)
_UNIQUE_INDEXES: list[tuple[str, tuple[str, ...], str]] = [
    ("friendships", ("user_id", "friend_id"), "uq_friendship_pair"),
    ("room_members", ("room_id", "user_id"), "uq_room_member"),
    ("partner_shares", ("partner_id", "shared_with_id"), "uq_partner_share_target"),
    ("usage_logs", ("user_id", "date"), "uq_usage_log_day"),
]

_USAGE_SUM_COLUMNS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "credits_used",
    "cost_usd",
)


def _index_names(inspector, table: str) -> set[str]:
    names = {ix["name"] for ix in inspector.get_indexes(table)}
    names |= {uc["name"] for uc in inspector.get_unique_constraints(table) if uc.get("name")}
    return names


def ensure_unique_indexes(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, cols, name in _UNIQUE_INDEXES:
        if table not in tables or name in _index_names(inspector, table):
            continue
        col_list = ", ".join(cols)
        with engine.begin() as conn:
            dupes = conn.execute(
                text(
                    f"SELECT {col_list}, COUNT(*) AS n FROM {table} "
                    f"GROUP BY {col_list} HAVING COUNT(*) > 1"
                )
            ).fetchall()
            for row in dupes:
                where = " AND ".join(f"{c} = :{c}" for c in cols)
                params = {c: row[i] for i, c in enumerate(cols)}
                ids = [
                    r[0]
                    for r in conn.execute(
                        text(f"SELECT id FROM {table} WHERE {where} ORDER BY id"), params
                    )
                ]
                keep, extra = ids[0], ids[1:]
                if table == "usage_logs":
                    present = {c["name"] for c in inspector.get_columns(table)}
                    sums = [c for c in _USAGE_SUM_COLUMNS if c in present]
                    if sums:
                        totals = conn.execute(
                            text(
                                "SELECT "
                                + ", ".join(f"COALESCE(SUM({c}), 0)" for c in sums)
                                + f" FROM {table} WHERE {where}"
                            ),
                            params,
                        ).fetchone()
                        conn.execute(
                            text(
                                f"UPDATE {table} SET "
                                + ", ".join(f"{c} = :v{i}" for i, c in enumerate(sums))
                                + " WHERE id = :keep"
                            ),
                            {**{f"v{i}": totals[i] for i in range(len(sums))}, "keep": keep},
                        )
                for rid in extra:
                    conn.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": rid})
                logger.warning(
                    "DB repair: collapsed %d duplicate row(s) in %s for %s",
                    len(extra),
                    table,
                    params,
                )
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({col_list})"))
            logger.info("DB repair: ensured unique index %s on %s(%s)", name, table, col_list)


def reconcile_identity_mirror(session_factory) -> None:
    try:
        from pathmind.services.auth import list_users

        accounts = [u for u in list_users() if u.get("id") and u.get("username")]
    except Exception as exc:  # auth store unavailable → nothing to reconcile
        logger.debug("Identity mirror skipped: %s", exc)
        return
    if not accounts:
        return

    from pathmind.database.models import User

    by_id = {str(u["id"]): u for u in accounts}
    db = session_factory()
    try:
        rows = {row.id: row for row in db.query(User).all()}
        # 1. Orphans release the usernames/emails real accounts need.
        for row in rows.values():
            if row.id in by_id:
                continue
            suffix = f"~orphan~{row.id[:8]}"
            if not str(row.username).endswith(suffix):
                row.username = f"{row.username}{suffix}"[:100]
                row.email = None
                row.is_active = False
                logger.warning("DB repair: deactivated orphan user row %s", row.id)
        db.flush()
        # 2. Every account gets a row with its real id, username and profile.
        for uid, acct in by_id.items():
            username = str(acct["username"])
            email = (acct.get("email") or None) and str(acct["email"]).lower()
            role = str(acct.get("role") or "user")
            row = rows.get(uid)
            if row is None:
                row = User(
                    id=uid,
                    username=username,
                    password_hash="managed-by-auth-store",
                    role=role if role in {"admin", "rnd", "educator", "user"} else "user",
                )
                db.add(row)
            if row.username != username:
                logger.info("DB repair: user %s username %r -> %r", uid, row.username, username)
                row.username = username
            if email and row.email != email:
                row.email = email
            if acct.get("full_name") and row.full_name != acct["full_name"]:
                row.full_name = acct["full_name"]
            if "email_verified" in acct:
                row.email_verified = bool(acct["email_verified"])
            row.is_active = not bool(acct.get("disabled", False))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Identity mirror reconcile failed: %s", exc)
    finally:
        db.close()


def drop_retired_columns(engine) -> None:
    """Columns a model no longer declares; dropped so inserts never trip over them."""
    from sqlalchemy import inspect, text

    retired = {"knowledge_labels": ["layer"]}
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in retired.items():
            if table not in tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for column in columns:
                if column in present:
                    conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))
                    logger.info("DB migration: dropped column %s.%s", table, column)


def run_startup_repairs(engine, session_factory) -> None:
    for step in (
        lambda: ensure_unique_indexes(engine),
        lambda: reconcile_identity_mirror(session_factory),
        lambda: drop_retired_columns(engine),
    ):
        try:
            step()
        except Exception as exc:  # never block startup
            logger.warning("DB repair step failed: %s", exc)
