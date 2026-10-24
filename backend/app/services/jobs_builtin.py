"""What the built-in scheduled jobs do (see tasks.BUILTIN_JOBS)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Notification, Payment, ReviewState, RewardLedger, ScheduledJob, Subscription, User, UserBadge,
    VerifiedDocument, utcnow,
)
from .app_settings import get_setting
from .notify import notify
from .tasks import job_action


def _notified_today(db: Session, user_id: str, kind: str, target_id: str = "") -> bool:
    since = utcnow() - timedelta(hours=20)
    stmt = select(Notification.id).where(Notification.user_id == user_id, Notification.kind == kind,
                                         Notification.created_at >= since)
    if target_id:
        stmt = stmt.where(Notification.target_id == target_id)
    return db.scalar(stmt.limit(1)) is not None


@job_action("study_reminder")
def study_reminder(db: Session, job: ScheduledJob) -> str:
    now = utcnow()
    rows = db.execute(select(ReviewState.user_id, func.count()).where(ReviewState.due_at <= now)
                      .group_by(ReviewState.user_id)).all()
    sent = 0
    for user_id, n in rows:
        if not _notified_today(db, user_id, "study_reminder"):
            notify(db, user_id, "study_reminder", f"{n} practice question(s) are due for review today.",
                   target_type="practice")
            sent += 1
    db.commit()
    return f"reminded {sent} learner(s)"


@job_action("review_due")
def review_due(db: Session, job: ScheduledJob) -> str:
    now = utcnow()
    docs = db.scalars(select(VerifiedDocument).where(VerifiedDocument.status == "verified",
                                                     VerifiedDocument.review_due_at <= now)).all()
    if not docs:
        return "nothing due"
    retire = bool(get_setting(db, "knowledge").get("retire_when_overdue"))
    rnd = db.scalars(select(User.id).where(User.role == "rnd", User.is_active.is_(True))).all()
    for d in docs:
        if retire:
            from .knowledge_tree import retire_document

            retire_document(db, d, actor_id=None, note="review date passed")
        for uid in rnd:
            if not _notified_today(db, uid, "document_review_due", d.id):
                notify(db, uid, "document_review_due", f"Verified document due for review: {d.title}",
                       target_type="verified_document", target_id=d.id)
    db.commit()
    return f"{len(docs)} document(s) due"


@job_action("billing_housekeeping")
def billing_housekeeping(db: Session, job: ScheduledJob) -> str:
    now = utcnow()
    hours = int(get_setting(db, "billing").get("pending_payment_hours") or 24)
    stale = db.scalars(select(Payment).where(Payment.status == "pending",
                                             Payment.created_at < now - timedelta(hours=hours))).all()
    for p in stale:
        p.status = "expired"
    ended = db.scalars(select(Subscription).where(Subscription.status.in_(("active", "canceled")),
                                                  Subscription.period_end.is_not(None), Subscription.period_end <= now)).all()
    for s in ended:
        s.status = "expired"
        notify(db, s.user_id, "plan_expired", f"Your {s.plan_id} plan has ended.", target_type="subscription",
               target_id=s.id)
    db.commit()
    return f"expired {len(stale)} payment(s), {len(ended)} plan(s)"


@job_action("top_contributors")
def top_contributors(db: Session, job: ScheduledJob) -> str:
    now = utcnow()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (first - timedelta(days=1)).replace(day=1)
    badge = f"top_contributor_{prev_start:%Y_%m}"
    rows = db.execute(select(RewardLedger.user_id, func.sum(RewardLedger.points)).where(
        RewardLedger.points > 0, RewardLedger.created_at >= prev_start, RewardLedger.created_at < first)
        .group_by(RewardLedger.user_id).order_by(func.sum(RewardLedger.points).desc()).limit(3)).all()
    for user_id, _pts in rows:
        if db.get(UserBadge, (user_id, badge)) is None:
            db.add(UserBadge(user_id=user_id, badge=badge))
            notify(db, user_id, "badge_awarded", "You were a top contributor last month!", target_type="badge")
    db.commit()
    return f"awarded {len(rows)} badge(s)"


def register(app: FastAPI) -> None:
    """Importing this module registers the actions; kept as a call for clarity in main."""
    return None
