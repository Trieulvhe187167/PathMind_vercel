"""Read-only system status for R&D and administrators (replaces the old system update)."""

from __future__ import annotations

from datetime import datetime, timedelta
import shutil
import time
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

_STARTED = time.time()


def collect(db: Session) -> dict[str, Any]:
    from pathmind.database.models import (
        AnswerRating, AuditLog, BranchRecommendation, KnowledgeDomain, KnowledgeNode, KnowledgeSource,
        Payment, Subscription, UsageLog,
    )

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("Database", True, "reachable")
    try:
        from pathmind.multi_user.identity import load_users

        users = load_users()
        roles: dict[str, int] = {}
        for u in users.values():
            r = str((u or {}).get("role") or "user")
            roles[r] = roles.get(r, 0) + 1
    except Exception as exc:  # noqa: BLE001
        users, roles = {}, {}
        check("Accounts", False, type(exc).__name__)
    since = datetime.utcnow() - timedelta(days=1)
    store_bytes = int(db.query(func.coalesce(func.sum(KnowledgeSource.size), 0)).scalar() or 0)
    try:
        from pathmind.multi_user.paths import ADMIN_WORKSPACE_ROOT

        du = shutil.disk_usage(str(ADMIN_WORKSPACE_ROOT))
        disk = {"free_bytes": du.free, "total_bytes": du.total}
        check("Disk space", du.free > 1_000_000_000, f"{du.free // 1_048_576} MB free")
    except Exception:  # noqa: BLE001
        disk = {}
    llm_ok = False
    try:
        from pathmind.services.quota_service import default_llm_model_name

        llm_ok = bool(default_llm_model_name())
    except Exception:  # noqa: BLE001
        pass
    check("Default AI model", llm_ok, "configured" if llm_ok else "not configured")
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "uptime_seconds": int(time.time() - _STARTED),
        "checks": checks,
        "accounts": {"total": len(users), "by_role": roles},
        "subscriptions_active": db.query(Subscription).filter(Subscription.status == "active").count(),
        "payments_pending": db.query(Payment).filter(Payment.status == "pending").count(),
        "recommendations_pending": db.query(BranchRecommendation).filter_by(status="pending").count(),
        "knowledge": {
            "domains": db.query(KnowledgeDomain).count(),
            "nodes": db.query(KnowledgeNode).count(),
            "documents": db.query(KnowledgeSource).count(),
            "document_bytes": store_bytes,
        },
        "activity_24h": {
            "store_actions": db.query(AuditLog).filter(AuditLog.created_at >= since, AuditLog.action.like("store.%")).count(),
            "credits_used_today": int(
                db.query(func.coalesce(func.sum(UsageLog.credits_used), 0))
                .filter(UsageLog.date == datetime.utcnow().strftime("%Y-%m-%d")).scalar() or 0
            ),
        },
        "answers_wrong_7d": db.query(AnswerRating)
        .filter(AnswerRating.verdict == "wrong", AnswerRating.created_at >= datetime.utcnow() - timedelta(days=7))
        .count(),
        "disk": disk,
    }
