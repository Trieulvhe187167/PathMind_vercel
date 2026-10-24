"""Append-only audit trail for sensitive actions (store uploads/downloads, merges...)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from pathmind.database.models import AuditLog


def record(
    db: Session,
    actor_id: str,
    action: str,
    *,
    target_type: str = "",
    target_id: str = "",
    data: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditLog:
    row = AuditLog(
        actor_id=str(actor_id or ""),
        action=action,
        target_type=target_type,
        target_id=str(target_id or ""),
        data=data or {},
    )
    db.add(row)
    if commit:
        db.commit()
    return row


def list_recent(
    db: Session, *, limit: int = 100, action_prefix: str = "", actor_id: str = ""
) -> list[dict[str, Any]]:
    q = db.query(AuditLog)
    if action_prefix:
        q = q.filter(AuditLog.action.like(action_prefix + "%"))
    if actor_id:
        q = q.filter(AuditLog.actor_id == actor_id)
    rows = q.order_by(AuditLog.id.desc()).limit(max(1, min(limit, 500))).all()
    return [
        {
            "id": r.id,
            "actor_id": r.actor_id,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "data": r.data or {},
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]
