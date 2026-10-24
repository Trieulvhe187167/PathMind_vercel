"""Plan entitlements: which features a plan unlocks and how many things it allows.

Stored per plan in ``subscription_plans.entitlements`` (JSON, editable in
``/admin/plans``). Keys:

``community``         Community (forum + skill hub)
``space_extras``      Learning Space → Notebooks, Question Bank, Skills
``courses``           Courses (study-course containers)
``memory``            Memory workbench (view/edit the three memory layers, run AI consolidation)
``reading``           Personalized Learning → Reading
``watching``          Personalized Learning → Watching (video learning)
``contribute``        Educator contributions: own knowledge branch + recommending it to R&D
``max_skills``        own skills (created, uploaded, added from Community/EduHub)
``max_partners``      partners the user created
``mastery_per_month`` new mastery (learning) paths per calendar month (UTC)
``books_per_month``   new AI books per calendar month (UTC)
``allowance_period``  ``"month"`` (the two allowances above reset on the 1st, UTC) or
                      ``"lifetime"`` (a one-time trial: Free gets 1 path + 1 book in total)

For limits ``None`` means unlimited and ``0`` means not available. Practice,
chat history and the Knowledge Center are open on every plan. Admins are
never limited. Downgrading never deletes anything: existing skills/partners
stay, only new ones are blocked until the user is back under the limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import threading
import time
from typing import Any

from fastapi import HTTPException, Request

from pathmind.services.i18n import t as _t

logger = logging.getLogger(__name__)

FEATURE_KEYS = (
    "community",
    "space_extras",
    "courses",
    "memory",
    "reading",
    "watching",
    # Educator contributions: personal knowledge branch edits and recommending
    # a branch to R&D. Paid plans only (the gate that replaces role approval).
    "contribute",
)
LIMIT_KEYS = ("max_skills", "max_partners", "mastery_per_month", "books_per_month")
MONTHLY_KEYS = {"mastery_per_month": "mastery", "books_per_month": "books"}
PERIODS = ("month", "lifetime")

UNLIMITED: dict[str, Any] = {
    "community": True,
    "space_extras": True,
    "courses": True,
    "memory": True,
    "reading": True,
    "watching": True,
    "contribute": True,
    "max_skills": None,
    "max_partners": None,
    "mastery_per_month": None,
    "books_per_month": None,
    "allowance_period": "month",
}

FREE: dict[str, Any] = {
    "community": False,
    "space_extras": False,
    "courses": False,
    "memory": False,
    "reading": False,
    "watching": False,
    "contribute": False,
    "max_skills": 0,
    "max_partners": 1,
    # A one-time trial rather than a monthly allowance.
    "mastery_per_month": 1,
    "books_per_month": 1,
    "allowance_period": "lifetime",
}

PLAN_DEFAULTS: dict[str, dict[str, Any]] = {
    "free": FREE,
    "plus": {
        "community": True,
        "space_extras": True,
        "courses": True,
        "memory": True,
        "reading": True,
        "watching": True,
        "contribute": True,
        "max_skills": 5,
        "max_partners": 3,
        "mastery_per_month": 10,
        "books_per_month": 10,
        "allowance_period": "month",
    },
    "pro": {
        "community": True,
        "space_extras": True,
        "courses": True,
        "memory": True,
        "reading": True,
        "watching": True,
        "contribute": True,
        "max_skills": 20,
        "max_partners": 10,
        "mastery_per_month": None,
        "books_per_month": None,
        "allowance_period": "month",
    },
    "enterprise": UNLIMITED,
}


def default_entitlements(plan_id: str, price_monthly: float | None = 0.0) -> dict[str, Any]:
    """Shipped defaults: known plan ids, else free-like (free plans) or unlimited."""
    if plan_id in PLAN_DEFAULTS:
        return dict(PLAN_DEFAULTS[plan_id])
    return dict(FREE if not (price_monthly or 0) else UNLIMITED)


_INVALID = object()


def _limit(value: Any) -> Any:
    """``None`` = unlimited; a junk value is ``_INVALID`` (never "unlimited")."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return _INVALID
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return _INVALID


def normalize(raw: Any, plan_id: str = "", price_monthly: float | None = 0.0) -> dict[str, Any]:
    """Merge stored values over the defaults; unknown keys are dropped."""
    base = default_entitlements(plan_id, price_monthly)
    if isinstance(raw, dict):
        for key in FEATURE_KEYS:
            if key in raw:
                base[key] = bool(raw[key])
        for key in LIMIT_KEYS:
            if key in raw:
                value = _limit(raw[key])
                if value is not _INVALID:  # keep the default rather than open the plan up
                    base[key] = value
        if raw.get("allowance_period") in PERIODS:
            base["allowance_period"] = raw["allowance_period"]
    return base


def plan_entitlements(plan: Any) -> dict[str, Any]:
    return normalize(
        getattr(plan, "entitlements", None),
        str(getattr(plan, "id", "") or ""),
        getattr(plan, "price_monthly", 0.0),
    )


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 10.0
_LOCK = threading.Lock()


def invalidate(user_id: str | None = None) -> None:
    with _LOCK:
        if user_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(user_id, None)


def _current_user():
    from pathmind.multi_user.context import get_current_user

    return get_current_user()


def _plan_for(user_id: str) -> tuple[str, str, float, dict[str, Any]]:
    """(plan id, plan name, monthly price, entitlements) with a short cache."""
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(user_id)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]["_plan"]
    from pathmind.database.connection import SessionLocal
    from pathmind.services.quota_service import get_user_plan

    db = SessionLocal()
    try:
        plan = get_user_plan(user_id, db)
        value = (
            str(plan.id),
            str(plan.name or plan.id),
            float(plan.price_monthly or 0.0),
            plan_entitlements(plan),
        )
    except Exception as exc:  # DB trouble: fail closed to the free defaults
        logger.warning("Entitlements lookup failed for %s: %s", user_id, exc)
        # Not cached: a paying user must not stay locked out after a DB blip.
        return ("free", "Free", 0.0, dict(FREE))
    finally:
        db.close()
    with _LOCK:
        _CACHE[user_id] = (now, {"_plan": value})
    return value


def current_entitlements(user: Any = None) -> dict[str, Any]:
    user = user or _current_user()
    if getattr(user, "is_admin", False) or getattr(user, "is_rnd", False):
        return dict(UNLIMITED)
    return dict(_plan_for(user.id)[3])


def _upgrade_hint(feature_or_limit: str, user: Any) -> str:
    """Name of the cheapest active plan that improves this entitlement."""
    from pathmind.database.connection import SessionLocal
    from pathmind.database.models import SubscriptionPlan

    current_id, _name, current_price, current = _plan_for(user.id)
    db = SessionLocal()
    try:
        plans = (
            db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.price_monthly.asc())
            .all()
        )
        for plan in plans:
            if plan.id == current_id or (plan.price_monthly or 0) <= current_price:
                continue
            ent = plan_entitlements(plan)
            if feature_or_limit in FEATURE_KEYS:
                if ent[feature_or_limit]:
                    return plan.name
            else:
                mine, theirs = current.get(feature_or_limit), ent.get(feature_or_limit)
                if theirs is None and mine is not None:
                    return plan.name
                if theirs is not None and mine is not None and theirs > mine:
                    return plan.name
    except Exception:  # pragma: no cover
        return ""
    finally:
        db.close()
    return ""


def _denied(message_key: str, user: Any, entitlement: str, **kwargs: Any) -> HTTPException:
    plan = _upgrade_hint(entitlement, user)
    message = _t(message_key, **kwargs)
    if plan:
        message = f"{message} {_t('plans.upgrade_to', plan=plan)}"
    return HTTPException(status_code=402, detail=message)


def require_feature(feature: str, user: Any = None) -> None:
    """Raise 402 unless the user's plan unlocks *feature*."""
    user = user or _current_user()
    if current_entitlements(user).get(feature):
        return
    raise _denied(f"plans.feature_{feature}", user, feature)


def feature_dependency(feature: str):
    """FastAPI dependency form of :func:`require_feature`."""

    def _dep() -> None:
        require_feature(feature)

    return _dep


# Notebooks: the list stays readable (it answers ``[]`` when locked, so pickers
# in chat/partners degrade quietly); everything else needs ``space_extras``.
_NOTEBOOK_OPEN = re.compile(r"^/api/notebooks(/health)?/?$")
# Question bank: chat quizzes and Practice persist answers through these, so
# they stay open on every plan. Browsing/organising the bank is gated.
_QUESTION_BANK_OPEN = (
    ("POST", re.compile(r"^/api/question-notebook/entries/upsert/?$")),
    ("GET", re.compile(r"^/api/question-notebook/entries/lookup/by-question/?$")),
    ("GET", re.compile(r"^/api/question-notebook/entries/\d+/?$")),
    ("PATCH", re.compile(r"^/api/question-notebook/entries/\d+/?$")),
)


def notebook_guard(request: Request) -> None:
    if request.method == "GET" and _NOTEBOOK_OPEN.match(request.url.path):
        return
    require_feature("space_extras")


def question_bank_guard(request: Request) -> None:
    path = request.url.path
    for method, pattern in _QUESTION_BANK_OPEN:
        if request.method == method and pattern.match(path):
            return
    require_feature("space_extras")


# Chat history labels reading conversations by collection; that stays open.
_READING_OPEN = re.compile(r"^/api/reading/workspaces/index/?$")


def reading_guard(request: Request) -> None:
    if request.method == "GET" and _READING_OPEN.match(request.url.path):
        return
    require_feature("reading")


# The sidebar badge polls this for every signed-in user; on a plan without
# Community it must answer quietly instead of failing with 402 each minute.
_COMMUNITY_OPEN = re.compile(r"^/api/community/notifications/unread-count/?$")


def community_guard(request: Request) -> None:
    if request.method == "GET" and _COMMUNITY_OPEN.match(request.url.path):
        return
    require_feature("community")


# Courses: the list stays readable (it answers an empty list when locked) because
# the chat composer and sidebars load it for everyone; everything else is gated.
_COURSES_OPEN = re.compile(r"^/api/courses/?$")


def courses_guard(request: Request) -> None:
    if request.method == "GET" and _COURSES_OPEN.match(request.url.path):
        return
    require_feature("courses")


def has_feature(feature: str, user: Any = None) -> bool:
    return bool(current_entitlements(user or _current_user()).get(feature))


def check_count_limit(key: str, current_count: int, user: Any = None) -> None:
    """Raise 402 when creating one more would exceed *key* (``max_*``)."""
    user = user or _current_user()
    limit = current_entitlements(user).get(key)
    if limit is None or current_count < limit:
        return
    raise _denied(f"plans.limit_{key}", user, key, limit=limit)


# ---------------------------------------------------------------------------
# Monthly counters
# ---------------------------------------------------------------------------


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def monthly_used(key: str, user_id: str, period: str = "month") -> int:
    """Creations counted against *key*: this month, or ever (``lifetime``)."""
    from sqlalchemy import func

    from pathmind.database.connection import SessionLocal
    from pathmind.database.models import FeatureUsage

    db = SessionLocal()
    try:
        query = db.query(func.coalesce(func.sum(FeatureUsage.count), 0)).filter(
            FeatureUsage.user_id == user_id, FeatureUsage.feature == MONTHLY_KEYS[key]
        )
        if period != "lifetime":
            query = query.filter(FeatureUsage.period == _period())
        return int(query.scalar() or 0)
    finally:
        db.close()


def allowance_period(user: Any = None) -> str:
    return str(current_entitlements(user or _current_user()).get("allowance_period") or "month")


def check_monthly(key: str, user: Any = None) -> None:
    """Raise 402 when this month's allowance for *key* is used up."""
    user = user or _current_user()
    ents = current_entitlements(user)
    limit = ents.get(key)
    if limit is None:
        return
    period = ents.get("allowance_period") or "month"
    if monthly_used(key, user.id, period) >= limit:
        suffix = "_trial" if period == "lifetime" else ""
        raise _denied(f"plans.limit_{key}{suffix}", user, key, limit=limit)


# One process serves the API, so a lock is enough to make "check, then count"
# a single step (the creation itself is awaited in between).
_RESERVE_LOCK = threading.RLock()


def record_monthly(key: str, user: Any = None) -> None:
    """Count one creation against the allowance (admins too, harmlessly)."""
    from sqlalchemy.exc import IntegrityError

    from pathmind.database.connection import SessionLocal
    from pathmind.database.models import FeatureUsage

    user = user or _current_user()
    feature = MONTHLY_KEYS[key]
    with _RESERVE_LOCK:
        db = SessionLocal()
        try:
            for _attempt in range(2):
                row = db.query(FeatureUsage).filter_by(
                    user_id=user.id, feature=feature, period=_period()
                ).first()
                if row is None:
                    db.add(FeatureUsage(user_id=user.id, feature=feature, period=_period(), count=1))
                else:
                    row.count = int(row.count or 0) + 1
                    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                try:
                    db.commit()
                    return
                except IntegrityError:  # another worker inserted the row first: add to it
                    db.rollback()
        except Exception as exc:  # never fail the user's action over the counter
            db.rollback()
            logger.warning("Could not record %s usage for %s: %s", key, user.id, exc)
        finally:
            db.close()


def reserve_monthly(key: str, user: Any = None) -> None:
    """Check the allowance and count the creation in one step.

    Creating a book or path takes a while (LLM calls). Counting only afterwards
    let several parallel requests all pass the check, so one free-trial book
    could become many. Reserve first and :func:`release_monthly` if it fails.
    """
    user = user or _current_user()
    ents = current_entitlements(user)
    limit = ents.get(key)
    period = ents.get("allowance_period") or "month"
    blocked = False
    with _RESERVE_LOCK:
        if limit is not None and monthly_used(key, user.id, period) >= limit:
            blocked = True
        else:
            record_monthly(key, user)
    if blocked:
        suffix = "_trial" if period == "lifetime" else ""
        raise _denied(f"plans.limit_{key}{suffix}", user, key, limit=limit)


def release_monthly(key: str, user: Any = None) -> None:
    """Give back a reserved creation that did not happen."""
    from pathmind.database.connection import SessionLocal
    from pathmind.database.models import FeatureUsage

    user = user or _current_user()
    with _RESERVE_LOCK:
        db = SessionLocal()
        try:
            row = (
                db.query(FeatureUsage)
                .filter(
                    FeatureUsage.user_id == user.id,
                    FeatureUsage.feature == MONTHLY_KEYS[key],
                    FeatureUsage.count > 0,
                )
                .order_by(FeatureUsage.period.desc())
                .first()
            )
            if row is not None:
                row.count = int(row.count) - 1
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Could not release %s usage for %s: %s", key, user.id, exc)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Resource counts
# ---------------------------------------------------------------------------


def own_skill_count() -> int:
    """Skills in the caller's own layer (built-in ones never count)."""
    from pathmind.services.skill import get_skill_service

    try:
        return sum(1 for s in get_skill_service().list_skills() if s.source == "user")
    except Exception:
        return 0


def check_skill_limit(replacing: str | None = None) -> None:
    """402 when one more own skill would pass ``max_skills``.

    Overwriting a skill the user already owns (``replacing``) adds nothing.
    """
    user = _current_user()
    if current_entitlements(user).get("max_skills") is None:
        return
    if replacing:
        from pathmind.services.skill import get_skill_service

        try:
            if any(
                s.name == replacing and s.source == "user"
                for s in get_skill_service().list_skills()
            ):
                return
        except Exception:
            pass
    check_count_limit("max_skills", own_skill_count(), user)


def own_partner_count(user_id: str) -> int:
    from pathmind.services.partners import get_partner_manager

    try:
        return sum(
            1
            for p in get_partner_manager().list_partners()
            if str(p.get("owner_id") or "") == user_id
        )
    except Exception:
        return 0


def summary(user: Any = None) -> dict[str, Any]:
    """Everything the web app needs to lock, count and upsell."""
    user = user or _current_user()
    is_admin = bool(getattr(user, "is_admin", False))
    if is_admin:
        plan_id, plan_name, price = "admin", "Admin", 0.0
    else:
        plan_id, plan_name, price, _ents = _plan_for(user.id)
    ents = current_entitlements(user)
    period = ents.get("allowance_period") or "month"
    usage = {
        "skills": own_skill_count(),
        "partners": own_partner_count(user.id),
        # Named for the monthly case; on a lifetime trial they count every creation.
        "mastery_this_month": monthly_used("mastery_per_month", user.id, period),
        "books_this_month": monthly_used("books_per_month", user.id, period),
    }
    upgrades: dict[str, str] = {}
    if not is_admin:
        for key in FEATURE_KEYS:
            if not ents[key]:
                upgrades[key] = _upgrade_hint(key, user)
        for key in LIMIT_KEYS:
            if ents[key] is not None:
                upgrades[key] = _upgrade_hint(key, user)
    return {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "price_monthly": price,
        "is_admin": is_admin,
        "features": {key: bool(ents[key]) for key in FEATURE_KEYS},
        "limits": {key: ents[key] for key in LIMIT_KEYS},
        "allowance_period": period,
        "usage": usage,
        "upgrade_to": upgrades,
    }


__all__ = [
    "FEATURE_KEYS",
    "LIMIT_KEYS",
    "PLAN_DEFAULTS",
    "check_count_limit",
    "check_monthly",
    "check_skill_limit",
    "courses_guard",
    "community_guard",
    "current_entitlements",
    "default_entitlements",
    "feature_dependency",
    "has_feature",
    "notebook_guard",
    "question_bank_guard",
    "reading_guard",
    "invalidate",
    "normalize",
    "own_partner_count",
    "own_skill_count",
    "plan_entitlements",
    "record_monthly",
    "release_monthly",
    "reserve_monthly",
    "require_feature",
    "summary",
]
