"""Default data created at start-up when missing: plans, model prices, built-in personas and skills."""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import billing


def run(db: Session) -> None:
    from ..routers import agents  # built-in personas and skills live next to their API

    billing.seed(db)
    agents.seed(db)
