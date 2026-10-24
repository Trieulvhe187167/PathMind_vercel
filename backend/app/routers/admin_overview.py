"""Administrators: dashboard numbers and the audit log."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..deps import get_db, require_admin
from ..models import (
    ROLES, AIModel, AuditLog, ContentReport, KnowledgeDomain, LLMCall, Payment, RoleRequest, StorageBackend, Subscription,
    User, VerifiedDocument, utcnow,
)
from ..services import mailer
from ..services.app_settings import get_setting

router = APIRouter()


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    by_role = {r: 0 for r in ROLES}
    for role, count in db.execute(select(User.role, func.count()).group_by(User.role)):
        by_role[role] = count
    total = sum(by_role.values())
    inactive = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(False))) or 0
    since = utcnow() - timedelta(days=7)
    new_7d = db.scalar(select(func.count()).select_from(User).where(User.created_at >= since)) or 0
    active_7d = db.scalar(select(func.count()).select_from(User).where(User.last_login_at >= since, User.is_active.is_(True))) or 0
    pending = db.scalar(
        select(func.count()).select_from(RoleRequest).where(RoleRequest.status == "pending")
    ) or 0
    recent = db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc()).limit(5)).all()
    month = utcnow() - timedelta(days=30)
    revenue = db.scalar(select(func.coalesce(func.sum(Payment.amount_usd), 0)).where(
        Payment.status == "completed", Payment.paid_at >= month)) or 0
    ai_cost = db.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(LLMCall.created_at >= month)) or 0
    ai_calls = db.scalar(select(func.count()).select_from(LLMCall).where(LLMCall.created_at >= since)) or 0
    ai_errors = db.scalar(select(func.count()).select_from(LLMCall).where(LLMCall.created_at >= since, LLMCall.status == "error")) or 0
    paying = db.scalar(select(func.count(func.distinct(Subscription.user_id))).where(
        Subscription.status.in_(("active", "canceled")), Subscription.plan_id != "free")) or 0
    default_storage = db.scalar(select(StorageBackend).where(StorageBackend.is_default.is_(True)))
    chat_model = db.scalar(select(AIModel).where(AIModel.purpose == "chat", AIModel.is_default.is_(True), AIModel.is_active.is_(True)))
    emb_model = db.scalar(select(AIModel).where(AIModel.purpose == "embedding", AIModel.is_active.is_(True)))
    return {
        "billing": {"revenue_30d_usd": float(revenue), "ai_cost_30d_usd": float(ai_cost), "paying_users": paying,
                    "pending_payments": db.scalar(select(func.count()).select_from(Payment).where(Payment.status == "pending")) or 0},
        "ai": {"calls_7d": ai_calls, "errors_7d": ai_errors, "chat_model": chat_model.model_name if chat_model else None,
               "embedding_model": emb_model.model_name if emb_model else None},
        "health": {
            "ai_configured": chat_model is not None,
            "email_configured": mailer.configured(db),
            "storage": {"name": default_storage.name, "status": default_storage.status} if default_storage else {"name": "server disk", "status": "ok"},
            "google": bool(get_setting(db, "google_oauth").get("enabled")),
            "payments": bool(get_setting(db, "vnpay").get("tmn_code")),
        },
        "open_reports": db.scalar(select(func.count(func.distinct(ContentReport.target_id))).where(ContentReport.status == "open")) or 0,
        "tree": {"domains": db.scalar(select(func.count()).select_from(KnowledgeDomain).where(KnowledgeDomain.status == "active")) or 0,
                 "verified_documents": db.scalar(select(func.count()).select_from(VerifiedDocument).where(VerifiedDocument.status == "verified")) or 0},
        "users": {"total": total, "inactive": inactive, "by_role": by_role, "new_7d": new_7d, "active_7d": active_7d},
        "pending_role_requests": pending,
        "recent_signups": [
            {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role, "created_at": _iso(u.created_at)}
            for u in recent
        ],
        "recent_activity": _audit_rows(db, limit=6),
    }


def _audit_rows(db: Session, *, limit: int, before_id: int | None = None, action: str = "") -> list[dict]:
    actor = aliased(User)
    target = aliased(User)
    stmt = (
        select(AuditLog, actor.username, target.username)
        .outerjoin(actor, actor.id == AuditLog.actor_id)
        .outerjoin(target, (AuditLog.target_type == "user") & (target.id == AuditLog.target_id))
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action, autoescape=True))
    return [
        {"id": a.id, "action": a.action, "actor": actor_name, "target_type": a.target_type,
         "target_id": a.target_id, "target": target_name, "data": a.data or {}, "ip": a.ip,
         "created_at": _iso(a.created_at)}
        for a, actor_name, target_name in db.execute(stmt).all()
    ]


@router.get("/audit")
def audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    action: str = Query(default="", max_length=64),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    rows = _audit_rows(db, limit=limit, before_id=before_id, action=action.strip())
    return {"entries": rows, "next_before_id": rows[-1]["id"] if len(rows) == limit else None}
