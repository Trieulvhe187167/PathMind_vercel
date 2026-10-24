"""In-app notifications (the bell). Committed by the caller together with the action."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Notification


def notify(
    db: Session, user_id: str, kind: str, preview: str, *, actor_id: str | None = None,
    target_type: str = "", target_id: str = "", data: dict[str, Any] | None = None,
) -> None:
    if actor_id and actor_id == user_id:
        return  # never notify people about their own actions
    db.add(Notification(user_id=user_id, kind=kind, preview=preview[:300], actor_id=actor_id,
                        target_type=target_type, target_id=target_id, data=data or {}))
