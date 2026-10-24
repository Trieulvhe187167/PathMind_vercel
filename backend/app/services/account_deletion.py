"""Self-service account deletion.

Personal data is removed: every row that belongs to the person (chats, knowledge bases, notes,
files, partners, memories, friends, sessions...). Accounting records stay (payments, subscriptions,
usage and the audit trail must keep a user id), so the ``users`` row is kept but anonymised.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from ..db import Base
from ..models import StoredFile, User, utcnow
from . import audit, storage

KEEP = {"users", "payments", "subscriptions", "usage_daily", "usage_by_model", "llm_calls", "feature_usage",
        "audit_log"}


def delete_account(db: Session, data_dir: Path, user: User, *, ip: str = "") -> None:
    uid = user.id
    for f in db.scalars(select(StoredFile).where(StoredFile.owner_id == uid, StoredFile.deleted_at.is_(None))):
        storage.delete_file(db, data_dir, f)
    db.flush()
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in KEEP:
            continue
        cascade_cols, null_cols = [], []
        for fk in table.foreign_keys:
            if fk.column.table.name == "users" and fk.column.name == "id":
                (cascade_cols if (fk.ondelete or "").upper() == "CASCADE" else null_cols).append(fk.parent)
        if cascade_cols:
            db.execute(delete(table).where(or_(*[c == uid for c in cascade_cols])))
        for c in null_cols:
            if c.nullable:
                db.execute(update(table).where(c == uid).values({c.name: None}))
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    user.username = f"deleted~{uid[:8]}~{stamp}"
    user.email = None
    user.full_name = ""
    user.avatar_url = ""
    user.password_hash = None
    user.is_active = False
    user.must_change_password = False
    user.token_version += 1
    user.deleted_at = utcnow()
    audit.record(db, uid, "user.self_delete", target_type="user", target_id=uid, ip=ip)
