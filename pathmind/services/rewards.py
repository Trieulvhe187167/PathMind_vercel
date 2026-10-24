"""Reward points: earned through activity, redeemed for plan days or AI credits (never cash).

Every change is a row in the append-only ledger. Administrators set the rules;
R&D can read the ledger. Spending (redeem, negative grants) is serialised so
two requests cannot spend the same points twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import threading
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from pathmind.database.models import BonusCredit, RewardLedger, RewardRule, SubscriptionPlan
from pathmind.services import audit

DEFAULT_RULES: dict[str, int] = {
    "earn.recommendation_merged": 100,  # per recommendation merged into the main tree
    "earn.per_node_merged": 10,  # extra per node merged
    "earn.node_mastered": 2,  # a learner masters a node of their mastery path (once per node)
    "cap.daily_activity_points": 20,  # most activity points (earn.node_mastered) per user per UTC day
    "rate.points_per_plan_day": 20,  # points needed for one plan day
    "rate.credits_per_point": 500,  # AI credits received per point
    "bonus.credit_days": 30,  # how long bought credits stay valid
    "min.redeem": 20,
}
KINDS = ("plan_days", "ai_credits")
ACTIVITY_RULES = ("earn.node_mastered",)  # capped by cap.daily_activity_points

# One process serves the API; this keeps check-then-spend atomic within it.
_SPEND_LOCK = threading.Lock()


class RewardError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def rules(db: Session) -> dict[str, int]:
    out = dict(DEFAULT_RULES)
    for r in db.query(RewardRule).all():
        if r.key in out:
            out[r.key] = int(r.value)
    return out


def set_rules(db: Session, actor_id: str, values: dict[str, Any]) -> dict[str, int]:
    for key, raw in values.items():
        if key not in DEFAULT_RULES:
            raise RewardError(400, f"Unknown rule: {key}")
        try:
            val = int(raw)
        except (TypeError, ValueError):
            raise RewardError(400, f"{key} must be a whole number.") from None
        if val < 0 or val > 1_000_000:
            raise RewardError(400, f"{key} is out of range.")
        if key.startswith("rate.") and val == 0:
            raise RewardError(400, f"{key} must be at least 1.")
        row = db.get(RewardRule, key)
        if row is None:
            row = RewardRule(key=key)
            db.add(row)
        row.value, row.updated_by, row.updated_at = val, actor_id, datetime.utcnow()
    audit.record(db, actor_id, "rewards.rules.update", target_type="rules", data=values, commit=False)
    db.commit()
    return rules(db)


def balance(db: Session, user_id: str) -> int:
    return int(db.query(func.coalesce(func.sum(RewardLedger.points), 0)).filter(RewardLedger.user_id == user_id).scalar() or 0)


def grant(
    db: Session, user_id: str, points: int, reason: str, *, ref: str = "", note: str = "",
    actor_id: str = "", commit: bool = True,
) -> RewardLedger:
    if points == 0:
        raise RewardError(400, "Points must not be zero.")
    if points < 0:
        with _SPEND_LOCK:
            if balance(db, user_id) + points < 0:
                raise RewardError(400, "That would make the balance negative.")
            row = RewardLedger(user_id=user_id, points=points, reason=reason[:60], ref=ref[:64],
                               note=note[:300], created_by=actor_id)
            db.add(row)
            db.flush()
        if commit:
            db.commit()
        return row
    row = RewardLedger(user_id=user_id, points=points, reason=reason[:60], ref=ref[:64], note=note[:300], created_by=actor_id)
    db.add(row)
    if commit:
        db.commit()
    return row


def earn_once(db: Session, user_id: str, rule: str, *, ref: str) -> int:
    """Award an activity rule once per ``ref``, within the daily cap. Returns points given."""
    r = rules(db)
    points = int(r.get(rule, 0))
    if points <= 0 or not user_id:
        return 0
    if db.query(RewardLedger.id).filter_by(user_id=user_id, ref=ref[:64]).first():
        return 0
    if rule in ACTIVITY_RULES:
        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today = int(
            db.query(func.coalesce(func.sum(RewardLedger.points), 0))
            .filter(RewardLedger.user_id == user_id, RewardLedger.reason.in_(ACTIVITY_RULES),
                    RewardLedger.created_at >= day_start)
            .scalar() or 0
        )
        points = min(points, max(0, r["cap.daily_activity_points"] - today))
        if points <= 0:
            return 0
    grant(db, user_id, points, rule, ref=ref)
    return points


def ledger(db: Session, user_id: str | None = None, limit: int = 100) -> list[dict]:
    q = db.query(RewardLedger)
    if user_id:
        q = q.filter(RewardLedger.user_id == user_id)
    return [
        {"id": r.id, "user_id": r.user_id, "points": r.points, "reason": r.reason, "ref": r.ref,
         "note": r.note, "created_at": r.created_at.isoformat() + "Z" if r.created_at else None}
        for r in q.order_by(RewardLedger.id.desc()).limit(limit).all()
    ]


def bonus_credits(db: Session, user_id: str) -> int:
    """Unexpired bought credits. Added to both the daily and monthly plan limits."""
    return int(
        db.query(func.coalesce(func.sum(BonusCredit.credits), 0))
        .filter(BonusCredit.user_id == user_id, BonusCredit.expires_at > datetime.utcnow())
        .scalar() or 0
    )


def redeem(db: Session, user_id: str, kind: str, points: int) -> dict:
    with _SPEND_LOCK:
        return _redeem(db, user_id, kind, points)


def _redeem(db: Session, user_id: str, kind: str, points: int) -> dict:
    if kind not in KINDS:
        raise RewardError(400, "Choose plan days or AI credits.")
    r = rules(db)
    if points < r["min.redeem"]:
        raise RewardError(400, f"The minimum is {r['min.redeem']} points.")
    if points > balance(db, user_id):
        raise RewardError(400, "You do not have that many points.")
    result: dict[str, Any]
    if kind == "plan_days":
        days = points // r["rate.points_per_plan_day"]
        if days < 1:
            raise RewardError(400, "Not enough points for one day.")
        points = days * r["rate.points_per_plan_day"]
        from pathmind.services import quota_service as q

        plan = q.get_user_plan(user_id, db)
        current = q.get_active_subscription(user_id, db)
        paid = (plan.price_monthly or 0) > 0 or (plan.price_yearly or 0) > 0
        if paid and current is not None and current.current_period_end is None:
            # activate_subscription would replace a never-expiring plan with a short one.
            raise RewardError(400, "Your plan does not expire, so there are no days to add.")
        if not paid:
            plan = (
                db.query(SubscriptionPlan)
                .filter(SubscriptionPlan.is_active.is_(True), SubscriptionPlan.price_monthly > 0)
                .order_by(SubscriptionPlan.price_monthly)
                .first()
            )
            if plan is None:
                raise RewardError(400, "No paid plan is available to extend.")
        q.activate_subscription(db, user_id, plan, source="reward", duration_days=days)
        result = {"kind": kind, "days": days, "plan": plan.name}
    else:
        credits = points * r["rate.credits_per_point"]
        db.add(BonusCredit(user_id=user_id, credits=credits, expires_at=datetime.utcnow() + timedelta(days=r["bonus.credit_days"])))
        result = {"kind": kind, "credits": credits, "valid_days": r["bonus.credit_days"]}
    db.add(RewardLedger(user_id=user_id, points=-points, reason=f"redeem.{kind}", ref="",
                        note=str(result)[:300], created_by=user_id))
    audit.record(db, user_id, "rewards.redeem", target_type="user", target_id=user_id, data={**result, "points": points}, commit=False)
    db.commit()
    result["points_spent"] = points
    result["balance"] = balance(db, user_id)
    try:
        from pathmind.services.entitlements import invalidate

        invalidate(user_id)
    except Exception:  # pragma: no cover
        pass
    return result
