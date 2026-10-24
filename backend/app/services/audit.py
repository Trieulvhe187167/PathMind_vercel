"""Append-only trail of administrative and security-relevant actions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog


def record(
    db: Session, actor_id: str | None, action: str, *, target_type: str = "", target_id: str = "",
    data: dict[str, Any] | None = None, ip: str = "",
) -> None:
    """Add one audit row to the session; it is committed with the change it describes."""
    db.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type, target_id=target_id,
                    data=data or {}, ip=ip))
