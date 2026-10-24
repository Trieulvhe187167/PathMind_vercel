"""Plan entitlements: paid-only surfaces, skill/partner limits, monthly allowances."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.multi_user.context import set_current_user, user_from_token_payload


def _user(uid: str, role: str = "user"):
    return user_from_token_payload(SimpleNamespace(user_id=uid, username=uid, role=role))


@pytest.fixture()
def db(monkeypatch):
    import pathmind.database.connection as connection
    import pathmind.database.init_db as init_db
    from pathmind.services import entitlements

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(connection, "SessionLocal", factory)
    monkeypatch.setattr(init_db, "SessionLocal", factory)
    init_db.seed_initial_data()
    entitlements.invalidate()
    yield factory
    entitlements.invalidate()


@pytest.fixture()
def no_resources(monkeypatch):
    """summary() also counts skills/partners on disk; keep those tests off it."""
    from pathmind.services import entitlements

    monkeypatch.setattr(entitlements, "own_skill_count", lambda: 0)
    monkeypatch.setattr(entitlements, "own_partner_count", lambda uid: 0)


def _subscribe(factory, user_id: str, plan_id: str) -> None:
    from pathmind.database.models import Subscription

    s = factory()
    s.add(
        Subscription(
            user_id=user_id,
            plan_id=plan_id,
            status="active",
            current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=30),
            billing_interval="monthly",
            source="test",
            created_at=datetime.utcnow(),
        )
    )
    s.commit()
    s.close()


def test_defaults_match_the_confirmed_spec():
    from pathmind.services.entitlements import default_entitlements, normalize

    free, plus, pro, unl = (default_entitlements(p) for p in ("free", "plus", "pro", "enterprise"))
    assert not free["community"] and not free["space_extras"] and not free["reading"]
    assert (free["max_skills"], free["max_partners"], free["mastery_per_month"], free["books_per_month"]) == (0, 1, 1, 1)
    assert free["allowance_period"] == "lifetime" and plus["allowance_period"] == "month"
    assert plus["community"] and plus["reading"] and plus["watching"] and plus["space_extras"]
    assert (plus["max_skills"], plus["max_partners"], plus["mastery_per_month"], plus["books_per_month"]) == (5, 3, 10, 10)
    assert (pro["max_skills"], pro["max_partners"], pro["mastery_per_month"], pro["books_per_month"]) == (20, 10, None, None)
    assert all(unl[k] is None for k in ("max_skills", "max_partners", "mastery_per_month", "books_per_month"))
    # Unknown admin-created plans: free-like when free, unlimited when paid.
    assert default_entitlements("team", 19.0)["max_skills"] is None
    assert default_entitlements("promo", 0)["community"] is False
    # Stored values override, blanks mean unlimited, junk keys are dropped.
    merged = normalize({"max_skills": "7", "books_per_month": "", "bogus": 1}, "plus", 4.99)
    assert merged["max_skills"] == 7 and merged["books_per_month"] is None and "bogus" not in merged


def test_plan_resolution_admin_and_upgrade_hints(db):
    from pathmind.services import entitlements

    _subscribe(db, "u_pro", "pro")
    set_current_user(_user("u_free"))
    assert entitlements.current_entitlements()["community"] is False
    with pytest.raises(HTTPException) as exc:
        entitlements.require_feature("community")
    assert exc.value.status_code == 402 and "Plus" in exc.value.detail

    set_current_user(_user("u_pro"))
    assert entitlements.current_entitlements()["max_skills"] == 20
    with pytest.raises(HTTPException) as exc:
        entitlements.check_count_limit("max_skills", 20)
    assert exc.value.status_code == 402 and "Unlimited Campus" in exc.value.detail
    entitlements.check_count_limit("max_skills", 19)  # still room

    set_current_user(_user("u_admin", "admin"))
    assert entitlements.current_entitlements()["max_partners"] is None
    entitlements.require_feature("community")


def test_monthly_allowance_counts_and_blocks(db):
    from pathmind.services import entitlements

    _subscribe(db, "u_plus", "plus")
    set_current_user(_user("u_plus"))
    for _ in range(10):
        entitlements.check_monthly("books_per_month")
        entitlements.record_monthly("books_per_month")
    assert entitlements.monthly_used("books_per_month", "u_plus") == 10
    with pytest.raises(HTTPException) as exc:
        entitlements.check_monthly("books_per_month")
    assert exc.value.status_code == 402
    entitlements.check_monthly("mastery_per_month")  # separate counter



def test_free_trial_is_one_of_each_for_good(db):
    """Free: one path and one book in total — a later month doesn't refill it."""
    from pathmind.database.models import FeatureUsage
    from pathmind.services import entitlements

    set_current_user(_user("u_free"))
    entitlements.check_monthly("books_per_month")
    entitlements.record_monthly("books_per_month")
    with pytest.raises(HTTPException) as exc:
        entitlements.check_monthly("books_per_month")
    assert exc.value.status_code == 402 and "trial" in exc.value.detail
    entitlements.check_monthly("mastery_per_month")  # path trial still unused

    s = db()
    s.query(FeatureUsage).update({FeatureUsage.period: "2020-01"})  # pretend it was long ago
    s.commit()
    s.close()
    with pytest.raises(HTTPException):
        entitlements.check_monthly("books_per_month")
    assert entitlements.summary()["usage"]["books_this_month"] == 1


def test_first_release_free_plan_migrates_to_trial(db):
    import pathmind.database.init_db as init_db
    from pathmind.database.models import SubscriptionPlan

    s = db()
    plan = s.get(SubscriptionPlan, "free")
    plan.entitlements = {**plan.entitlements, "mastery_per_month": 0, "books_per_month": 0}
    plan.entitlements.pop("allowance_period", None)
    s.commit()
    s.close()
    init_db.seed_initial_data()
    s = db()
    ent = s.get(SubscriptionPlan, "free").entitlements
    s.close()
    assert (ent["mastery_per_month"], ent["books_per_month"], ent["allowance_period"]) == (1, 1, "lifetime")


def test_summary_shape(db, monkeypatch):
    from pathmind.services import entitlements

    monkeypatch.setattr(entitlements, "own_skill_count", lambda: 2)
    monkeypatch.setattr(entitlements, "own_partner_count", lambda uid: 1)
    set_current_user(_user("u_free"))
    data = entitlements.summary()
    assert data["plan_id"] == "free"
    assert data["features"]["community"] is False
    assert data["limits"]["max_partners"] == 1
    assert data["usage"] == {"skills": 2, "partners": 1, "mastery_this_month": 0, "books_this_month": 0}
    assert data["allowance_period"] == "lifetime"
    assert data["upgrade_to"]["books_per_month"] == "Plus"
    assert data["upgrade_to"]["community"] == "Plus"


def test_guards_leave_quiz_plumbing_open(db):
    from pathmind.services import entitlements

    app = FastAPI()

    async def as_free():
        set_current_user(_user("u_free"))

    @app.api_route("/api/question-notebook/{rest:path}", methods=["GET", "POST", "PATCH"],
                   dependencies=[Depends(as_free), Depends(entitlements.question_bank_guard)])
    async def qb(rest: str, request: Request):
        return {"ok": True}

    @app.api_route("/api/notebooks{rest:path}", methods=["GET", "POST"],
                   dependencies=[Depends(as_free), Depends(entitlements.notebook_guard)])
    async def nb(rest: str):
        return {"ok": True}

    @app.get("/api/reading/{rest:path}", dependencies=[Depends(as_free), Depends(entitlements.reading_guard)])
    async def rd(rest: str):
        return {"ok": True}

    c = TestClient(app)
    assert c.post("/api/question-notebook/entries/upsert").status_code == 200
    assert c.get("/api/question-notebook/entries/lookup/by-question").status_code == 200
    assert c.patch("/api/question-notebook/entries/12").status_code == 200
    assert c.get("/api/question-notebook/entries").status_code == 402
    assert c.get("/api/question-notebook/stats").status_code == 402
    assert c.get("/api/notebooks").status_code == 200
    assert c.post("/api/notebooks").status_code == 402
    assert c.get("/api/notebooks/abc").status_code == 402
    assert c.get("/api/reading/workspaces/index").status_code == 200
    assert c.get("/api/reading/workspaces").status_code == 402


def test_plan_serialization_lists_entitlements(db):
    from pathmind.database.models import SubscriptionPlan
    from pathmind.services.quota_service import plan_to_dict

    s = db()
    plus = plan_to_dict(s.get(SubscriptionPlan, "plus"), "en")
    s.close()
    assert plus["entitlements"]["max_skills"] == 5
    joined = " | ".join(plus["features"])
    assert "Up to 5 skills" in joined and "Up to 3 partners" in joined and "10 mastery paths" in joined


# ---------------------------------------------------------------------------
# Reserve / release: the allowance is taken before the slow creation starts
# ---------------------------------------------------------------------------


def test_reserve_blocks_the_second_parallel_creation(db):
    """One free-trial book must not turn into several when requests overlap."""
    from pathmind.services import entitlements

    set_current_user(_user("u_free"))
    entitlements.reserve_monthly("books_per_month")  # request 1 passes the gate...
    with pytest.raises(HTTPException) as exc:  # ...request 2 arrives before 1 finished
        entitlements.reserve_monthly("books_per_month")
    assert exc.value.status_code == 402 and "trial" in exc.value.detail
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 1


def test_release_gives_a_failed_creation_back(db):
    from pathmind.services import entitlements

    set_current_user(_user("u_free"))
    entitlements.reserve_monthly("mastery_per_month")
    entitlements.release_monthly("mastery_per_month")
    assert entitlements.monthly_used("mastery_per_month", "u_free", "lifetime") == 0
    entitlements.reserve_monthly("mastery_per_month")  # the trial is still there
    with pytest.raises(HTTPException):
        entitlements.reserve_monthly("mastery_per_month")


def test_release_never_goes_below_zero_and_touches_only_that_feature(db):
    from pathmind.services import entitlements

    set_current_user(_user("u_free"))
    entitlements.release_monthly("books_per_month")  # nothing reserved: no-op
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 0
    entitlements.reserve_monthly("books_per_month")
    entitlements.release_monthly("mastery_per_month")  # other counter
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 1


def test_reserve_counts_for_unlimited_plans_without_blocking(db):
    from pathmind.services import entitlements

    _subscribe(db, "u_pro", "pro")
    set_current_user(_user("u_pro"))
    for _ in range(25):
        entitlements.reserve_monthly("books_per_month")
    assert entitlements.monthly_used("books_per_month", "u_pro") == 25


def test_monthly_allowance_refills_next_month_for_plus_only(db):
    from pathmind.database.models import FeatureUsage
    from pathmind.services import entitlements

    _subscribe(db, "u_plus", "plus")
    set_current_user(_user("u_plus"))
    for _ in range(10):
        entitlements.reserve_monthly("books_per_month")
    with pytest.raises(HTTPException):
        entitlements.reserve_monthly("books_per_month")
    s = db()
    s.query(FeatureUsage).update({FeatureUsage.period: "2020-01"})
    s.commit()
    s.close()
    entitlements.reserve_monthly("books_per_month")  # a new month: room again
    assert entitlements.monthly_used("books_per_month", "u_plus") == 1
    assert entitlements.monthly_used("books_per_month", "u_plus", "lifetime") == 11


def test_admin_plan_can_turn_any_plan_into_a_trial(db, no_resources):
    from pathmind.database.models import SubscriptionPlan
    from pathmind.services import entitlements

    s = db()
    plan = s.get(SubscriptionPlan, "plus")
    plan.entitlements = {**plan.entitlements, "books_per_month": 2, "allowance_period": "lifetime"}
    s.commit()
    s.close()
    entitlements.invalidate()
    _subscribe(db, "u_plus", "plus")
    set_current_user(_user("u_plus"))
    entitlements.reserve_monthly("books_per_month")
    entitlements.reserve_monthly("books_per_month")
    with pytest.raises(HTTPException) as exc:
        entitlements.reserve_monthly("books_per_month")
    assert "trial" in exc.value.detail
    data = entitlements.summary()
    assert data["allowance_period"] == "lifetime" and data["usage"]["books_this_month"] == 2


# ---------------------------------------------------------------------------
# Stored values: junk must never open a plan up
# ---------------------------------------------------------------------------


def test_normalize_keeps_the_default_for_junk_limits():
    from pathmind.services.entitlements import normalize

    got = normalize({"max_skills": "abc", "max_partners": True, "books_per_month": -4}, "plus", 4.99)
    assert got["max_skills"] == 5  # default kept, not "unlimited"
    assert got["max_partners"] == 3
    assert got["books_per_month"] == 0  # negatives clamp to "not available"
    got = normalize({"max_skills": None, "max_partners": ""}, "plus", 4.99)
    assert got["max_skills"] is None and got["max_partners"] is None  # blank = unlimited


def test_normalize_ignores_a_bad_allowance_period_and_non_dicts():
    from pathmind.services.entitlements import normalize

    assert normalize({"allowance_period": "weekly"}, "free")["allowance_period"] == "lifetime"
    assert normalize({"allowance_period": "month"}, "free")["allowance_period"] == "month"
    assert normalize("garbage", "plus", 4.99)["max_skills"] == 5
    assert normalize(None, "pro", 9.99)["max_skills"] == 20


def test_defaults_are_copies_not_shared_state():
    from pathmind.services.entitlements import PLAN_DEFAULTS, default_entitlements

    got = default_entitlements("free")
    got["max_partners"] = 99
    assert PLAN_DEFAULTS["free"]["max_partners"] == 1


def test_a_database_blip_locks_down_but_is_not_remembered(db, monkeypatch):
    from pathmind.services import entitlements, quota_service

    _subscribe(db, "u_pro", "pro")
    real = quota_service.get_user_plan
    calls = {"n": 0}

    def flaky(user_id, session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return real(user_id, session)

    monkeypatch.setattr(quota_service, "get_user_plan", flaky)
    set_current_user(_user("u_pro"))
    assert entitlements.current_entitlements()["community"] is False  # fails closed
    assert entitlements.current_entitlements()["community"] is True  # and recovers at once


# ---------------------------------------------------------------------------
# Upgrade hints
# ---------------------------------------------------------------------------


def test_upgrade_hints_walk_up_the_ladder(db, no_resources):
    from pathmind.services import entitlements

    _subscribe(db, "u_plus", "plus")
    _subscribe(db, "u_pro", "pro")
    _subscribe(db, "u_unl", "enterprise")

    set_current_user(_user("u_free"))
    assert entitlements.summary()["upgrade_to"]["community"] == "Plus"
    assert entitlements.summary()["upgrade_to"]["books_per_month"] == "Plus"

    set_current_user(_user("u_plus"))
    up = entitlements.summary()["upgrade_to"]
    assert "community" not in up  # already unlocked
    assert up["books_per_month"] == "Pro Learner" and up["max_skills"] == "Pro Learner"

    set_current_user(_user("u_pro"))
    up = entitlements.summary()["upgrade_to"]
    assert up["max_skills"] == "Unlimited Campus" and up["max_partners"] == "Unlimited Campus"
    assert "books_per_month" not in up  # unlimited already

    set_current_user(_user("u_unl"))
    assert entitlements.summary()["upgrade_to"] == {}


def test_upgrade_hint_skips_plans_that_are_not_on_sale(db, no_resources):
    from pathmind.database.models import SubscriptionPlan
    from pathmind.services import entitlements

    s = db()
    s.get(SubscriptionPlan, "plus").is_active = False
    s.commit()
    s.close()
    entitlements.invalidate()
    set_current_user(_user("u_free"))
    assert entitlements.summary()["upgrade_to"]["community"] == "Pro Learner"


def test_admin_summary_is_unlimited_without_upsell(db, no_resources):
    from pathmind.services import entitlements

    set_current_user(_user("root", "admin"))
    data = entitlements.summary()
    assert data["is_admin"] is True and data["plan_id"] == "admin"
    assert all(data["features"].values())
    assert all(v is None for v in data["limits"].values())
    assert data["upgrade_to"] == {}
    entitlements.reserve_monthly("books_per_month")  # never blocked
    entitlements.check_count_limit("max_partners", 10**6)


# ---------------------------------------------------------------------------
# Skills and partners
# ---------------------------------------------------------------------------


def _skills(monkeypatch, names_and_sources):
    import pathmind.services.skill as skill_pkg

    infos = [SimpleNamespace(name=n, source=src) for n, src in names_and_sources]
    monkeypatch.setattr(skill_pkg, "get_skill_service", lambda: SimpleNamespace(list_skills=lambda: infos))


def test_skill_limit_ignores_builtins_and_lets_you_overwrite_your_own(db, monkeypatch):
    from pathmind.services import entitlements

    _subscribe(db, "u_plus", "plus")
    set_current_user(_user("u_plus"))
    _skills(monkeypatch, [(f"mine{i}", "user") for i in range(5)] + [(f"b{i}", "builtin") for i in range(30)])
    assert entitlements.own_skill_count() == 5  # built-ins never count
    with pytest.raises(HTTPException) as exc:
        entitlements.check_skill_limit()
    assert exc.value.status_code == 402 and "Pro" in exc.value.detail
    entitlements.check_skill_limit(replacing="mine2")  # overwriting adds nothing
    with pytest.raises(HTTPException):
        entitlements.check_skill_limit(replacing="b3")  # a built-in name is a new skill
    with pytest.raises(HTTPException):
        entitlements.check_skill_limit(replacing="nope")


def test_free_users_cannot_add_skills_and_downgrades_only_block_new_ones(db, monkeypatch):
    from pathmind.services import entitlements

    set_current_user(_user("u_free"))
    _skills(monkeypatch, [])
    with pytest.raises(HTTPException) as exc:
        entitlements.check_skill_limit()
    assert exc.value.status_code == 402
    # A Pro user who downgraded keeps 12 skills; nothing is removed, one more is refused.
    _subscribe(db, "u_plus", "plus")
    set_current_user(_user("u_plus"))
    _skills(monkeypatch, [(f"s{i}", "user") for i in range(12)])
    assert entitlements.own_skill_count() == 12
    with pytest.raises(HTTPException):
        entitlements.check_skill_limit()


def test_skill_limit_is_off_for_unlimited_plans_and_admins(db, monkeypatch):
    from pathmind.services import entitlements

    _skills(monkeypatch, [(f"s{i}", "user") for i in range(200)])
    _subscribe(db, "u_unl", "enterprise")
    for user in (_user("u_unl"), _user("root", "admin")):
        set_current_user(user)
        entitlements.check_skill_limit()


def test_partner_count_only_counts_partners_you_own(monkeypatch):
    import pathmind.services.partners as partners_pkg
    from pathmind.services import entitlements

    listing = [
        {"id": "a", "owner_id": "u1"},
        {"id": "b", "owner_id": "u2"},
        {"id": "c", "owner_id": "u1"},
        {"id": "d"},  # admin-managed, no owner
    ]
    monkeypatch.setattr(
        partners_pkg, "get_partner_manager", lambda: SimpleNamespace(list_partners=lambda: listing)
    )
    assert entitlements.own_partner_count("u1") == 2
    assert entitlements.own_partner_count("nobody") == 0


def test_partner_limits_per_plan(db):
    from pathmind.services import entitlements

    _subscribe(db, "u_plus", "plus")
    _subscribe(db, "u_pro", "pro")
    set_current_user(_user("u_free"))
    entitlements.check_count_limit("max_partners", 0)
    with pytest.raises(HTTPException) as exc:
        entitlements.check_count_limit("max_partners", 1)
    assert "Plus" in exc.value.detail
    set_current_user(_user("u_plus"))
    entitlements.check_count_limit("max_partners", 2)
    with pytest.raises(HTTPException):
        entitlements.check_count_limit("max_partners", 3)
    set_current_user(_user("u_pro"))
    entitlements.check_count_limit("max_partners", 9)
    with pytest.raises(HTTPException):
        entitlements.check_count_limit("max_partners", 10)


# ---------------------------------------------------------------------------
# Feature switches and guards
# ---------------------------------------------------------------------------


def test_each_switch_is_independent(db):
    from pathmind.database.models import SubscriptionPlan
    from pathmind.services import entitlements

    s = db()
    plan = s.get(SubscriptionPlan, "plus")
    plan.entitlements = {**plan.entitlements, "watching": False, "community": False}
    s.commit()
    s.close()
    entitlements.invalidate()
    _subscribe(db, "u_plus", "plus")
    set_current_user(_user("u_plus"))
    assert entitlements.has_feature("reading") and entitlements.has_feature("space_extras")
    assert not entitlements.has_feature("watching") and not entitlements.has_feature("community")
    with pytest.raises(HTTPException):
        entitlements.require_feature("watching")


def test_paid_routers_answer_402_with_an_upgrade_hint_and_paid_users_pass(db):
    from pathmind.services import entitlements

    app = FastAPI()
    current = {"user": _user("u_free")}

    async def who():
        set_current_user(current["user"])

    @app.get("/api/community/posts", dependencies=[Depends(who), Depends(entitlements.feature_dependency("community"))])
    async def posts():
        return {"ok": True}

    @app.get("/api/video-learning/x", dependencies=[Depends(who), Depends(entitlements.feature_dependency("watching"))])
    async def video():
        return {"ok": True}

    c = TestClient(app)
    r = c.get("/api/community/posts")
    assert r.status_code == 402 and "Plus" in r.json()["detail"]
    assert c.get("/api/video-learning/x").status_code == 402
    _subscribe(db, "u_plus", "plus")
    current["user"] = _user("u_plus")
    assert c.get("/api/community/posts").status_code == 200
    assert c.get("/api/video-learning/x").status_code == 200


def test_notebook_and_reading_guards_only_allow_reads_of_the_index(db):
    from pathmind.services import entitlements

    app = FastAPI()

    async def as_free():
        set_current_user(_user("u_free"))

    @app.api_route("/api/notebooks{rest:path}", methods=["GET", "POST", "PUT", "DELETE"],
                   dependencies=[Depends(as_free), Depends(entitlements.notebook_guard)])
    async def nb(rest: str):
        return {"ok": True}

    @app.api_route("/api/reading/{rest:path}", methods=["GET", "POST"],
                   dependencies=[Depends(as_free), Depends(entitlements.reading_guard)])
    async def rd(rest: str):
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/api/notebooks/health").status_code == 200
    assert c.put("/api/notebooks").status_code == 402
    assert c.delete("/api/notebooks/abc").status_code == 402
    assert c.get("/api/notebooks/abc/records").status_code == 402
    assert c.post("/api/reading/workspaces/index").status_code == 402  # only GET is open
    assert c.get("/api/reading/workspaces/index").status_code == 200
    assert c.get("/api/reading/materials").status_code == 402


def test_quota_serialization_lists_trial_line_for_free(db):
    from pathmind.database.models import SubscriptionPlan
    from pathmind.services.quota_service import plan_to_dict

    s = db()
    free = plan_to_dict(s.get(SubscriptionPlan, "free"), "en")
    s.close()
    joined = " | ".join(free["features"])
    assert free["entitlements"]["allowance_period"] == "lifetime"
    assert "for free" in joined and "1 partner" in joined
    assert "10 mastery paths" not in joined



# ---------------------------------------------------------------------------
# Review round: community badge, real app wiring, creation routes end to end
# ---------------------------------------------------------------------------


def test_community_guard_leaves_the_unread_badge_open(db):
    """The sidebar polls the badge for everyone; a locked plan must not get 402s."""
    from pathmind.services import entitlements

    app = FastAPI()

    async def as_free():
        set_current_user(_user("u_free"))

    @app.api_route(
        "/api/community/{rest:path}",
        methods=["GET", "POST"],
        dependencies=[Depends(as_free), Depends(entitlements.community_guard)],
    )
    async def any_route(rest: str):
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/api/community/notifications/unread-count").status_code == 200
    assert c.get("/api/community/notifications").status_code == 402
    assert c.get("/api/community/posts").status_code == 402
    assert c.post("/api/community/notifications/unread-count").status_code == 402
    assert c.post("/api/community/notifications/read").status_code == 402


def test_real_app_puts_the_right_guard_on_each_paid_surface():
    """A guard that is written but not attached protects nothing.

    Read from main.py itself: recent FastAPI keeps included routers nested, so
    ``app.routes`` no longer lists every path.
    """
    import re
    from pathlib import Path

    import pathmind.api.main as main

    src = Path(main.__file__).read_text(encoding="utf-8")

    def deps_of(router: str) -> str:
        block = re.search(
            r"app\.include_router\(\s*" + re.escape(router) + r"\b[^)]*\)", src, re.S
        )
        assert block, f"{router} is no longer mounted"
        found = re.search(r"dependencies=(\w+)", block.group(0))
        assert found, f"{router} has no dependencies"
        return found.group(1)

    definitions = {
        "_community": "community_guard",
        "_notebooks": "notebook_guard",
        "_reading": "reading_guard",
        "_question_bank": "question_bank_guard",
        "_watching": 'feature_dependency("watching")',
    }
    for name, guard in definitions.items():
        line = re.search(rf"^{name} = .*$", src, re.M)
        assert line and guard in line.group(0), f"{name} lost {guard}"
    expectations = {
        "community.router": "_community",
        "notebook.router": "_notebooks",
        "reading.router": "_reading",
        "reading_extensions.router": "_reading",
        "question_notebook.router": "_question_bank",
        "video_learning.router": "_watching",
    }
    for router, var in expectations.items():
        assert deps_of(router) == var, f"{router} should use {var}"
    # The plan-neutral surfaces are still plain authenticated routers.
    for router in ("practice.router", "sessions.router", "knowledge.router"):
        assert deps_of(router) == "_auth", f"{router} must stay open to every plan"


class _FakeBook:
    id = "book_1"

    def model_dump(self, mode="json"):
        return {"id": "book_1"}


def _book_app(monkeypatch, user_id: str, *, fail: bool):
    from pathmind.api.routers import book as book_router

    class Engine:
        async def create_book(self, **_kwargs):
            if fail:
                raise RuntimeError("model is down")
            return _FakeBook(), _FakeBook()

    monkeypatch.setattr(book_router, "get_book_engine", lambda: Engine())
    monkeypatch.setattr(book_router, "can_create_book", lambda: True)
    app = FastAPI()

    async def who():
        set_current_user(_user(user_id))

    app.include_router(book_router.router, prefix="/api", dependencies=[Depends(who)])
    return TestClient(app, raise_server_exceptions=False)


def test_book_route_takes_the_trial_and_gives_it_back_when_the_model_fails(db, monkeypatch):
    from pathmind.services import entitlements

    body = {"user_intent": "Teach me graphs"}
    broken = _book_app(monkeypatch, "u_free", fail=True)
    assert broken.post("/api/books", json=body).status_code == 500
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 0

    ok = _book_app(monkeypatch, "u_free", fail=False)
    assert ok.post("/api/books", json=body).status_code == 200
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 1
    second = ok.post("/api/books", json=body)
    assert second.status_code == 402 and "trial" in second.json()["detail"]
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 1


def test_book_route_counts_per_month_for_plus_and_never_blocks_pro(db, monkeypatch):
    from pathmind.services import entitlements

    _subscribe(db, "u_plus", "plus")
    _subscribe(db, "u_pro", "pro")
    plus = _book_app(monkeypatch, "u_plus", fail=False)
    for _ in range(10):
        assert plus.post("/api/books", json={"user_intent": "x"}).status_code == 200
    blocked = plus.post("/api/books", json={"user_intent": "x"})
    assert blocked.status_code == 402 and "Pro Learner" in blocked.json()["detail"]
    pro = _book_app(monkeypatch, "u_pro", fail=False)
    for _ in range(12):
        assert pro.post("/api/books", json={"user_intent": "x"}).status_code == 200
    assert entitlements.monthly_used("books_per_month", "u_pro") == 12


def _mastery_app(user_id: str):
    from pathmind.api.routers import mastery_path

    app = FastAPI()

    async def who():
        set_current_user(_user(user_id))

    app.include_router(
        mastery_path.router, prefix="/api/mastery-paths", dependencies=[Depends(who)]
    )
    return TestClient(app, raise_server_exceptions=False)


def test_mastery_route_gives_the_trial_back_when_the_outline_is_rejected(db, monkeypatch):
    from pathmind.learning import topic_generation
    from pathmind.services import entitlements

    def reject(*_args, **_kwargs):
        raise topic_generation.TopicGenerationError("outline is not valid")

    monkeypatch.setattr(topic_generation, "materialize_modules", reject)
    client = _mastery_app("u_free")
    payload = {"name": "Graphs", "goal": "Learn graph theory", "modules": [{"title": "x"}]}
    for _ in range(3):  # a rejected outline never uses the trial up
        assert client.post("/api/mastery-paths/topics", json=payload).status_code == 422
        assert entitlements.monthly_used("mastery_per_month", "u_free", "lifetime") == 0


def test_mastery_draft_is_blocked_once_the_trial_is_used(db, monkeypatch):
    from pathmind.services import entitlements

    set_current_user(_user("u_free"))
    entitlements.reserve_monthly("mastery_per_month")  # the one free path was created
    client = _mastery_app("u_free")
    r = client.post("/api/mastery-paths/topics/draft", json={"name": "Graphs", "goal": "Learn graphs"})
    assert r.status_code == 402 and "trial" in r.json()["detail"]


def test_summary_counts_the_trial_across_months(db, no_resources):
    """A trial creation from an earlier month still counts for a lifetime plan."""
    from pathmind.database.models import FeatureUsage
    from pathmind.services import entitlements

    s = db()
    s.add(FeatureUsage(user_id="u_free", feature="books", period="2020-01", count=1))
    s.commit()
    s.close()
    set_current_user(_user("u_free"))
    assert entitlements.summary()["usage"]["books_this_month"] == 1
    with pytest.raises(HTTPException):
        entitlements.check_monthly("books_per_month")
    # Plus counts only the current month, so the same old row does not block it.
    _subscribe(db, "u_free", "plus")
    entitlements.invalidate()
    assert entitlements.summary()["usage"]["books_this_month"] == 0
    entitlements.check_monthly("books_per_month")


def _ws_book_client(monkeypatch, user_id: str, *, fail: bool):
    from pathmind.api.routers import auth
    from pathmind.api.routers import book as book_router

    class Engine:
        async def create_book(self, **_kwargs):
            if fail:
                raise RuntimeError("model is down")
            return _FakeBook(), _FakeBook()

    async def fake_ws_auth(_ws):
        return set_current_user(_user(user_id))

    monkeypatch.setattr(auth, "ws_require_auth", fake_ws_auth)
    monkeypatch.setattr(book_router, "get_book_engine", lambda: Engine())
    monkeypatch.setattr(book_router, "can_create_book", lambda: True)
    app = FastAPI()
    app.include_router(book_router.ws_router, prefix="/ws")
    return TestClient(app)


def test_book_socket_uses_the_trial_once_and_tells_the_client_why(db, monkeypatch):
    from pathmind.services import entitlements

    client = _ws_book_client(monkeypatch, "u_free", fail=False)
    with client.websocket_connect("/ws/books") as ws:
        ws.send_json({"type": "create", "user_intent": "Graphs"})
        assert ws.receive_json()["type"] == "create_result"
        ws.send_json({"type": "create", "user_intent": "More graphs"})
        blocked = ws.receive_json()
    assert blocked["type"] == "error" and blocked["code"] == "plan_limit"
    assert "trial" in blocked["content"]
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 1


def test_book_socket_gives_the_trial_back_when_creation_breaks(db, monkeypatch):
    from pathmind.services import entitlements

    client = _ws_book_client(monkeypatch, "u_free", fail=True)
    try:
        with client.websocket_connect("/ws/books") as ws:
            ws.send_json({"type": "create", "user_intent": "Graphs"})
            ws.receive_json()
    except Exception:  # the handler may close the socket or report the error
        pass
    assert entitlements.monthly_used("books_per_month", "u_free", "lifetime") == 0


# ---------------------------------------------------------------------------
# Courses: a paid surface whose list stays readable for every plan
# ---------------------------------------------------------------------------


def test_courses_are_paid_only_in_the_shipped_defaults():
    from pathmind.services import entitlements

    assert "courses" in entitlements.FEATURE_KEYS
    assert entitlements.PLAN_DEFAULTS["free"]["courses"] is False
    for plan in ("plus", "pro", "enterprise"):
        assert entitlements.PLAN_DEFAULTS[plan]["courses"] is True
    # A plan row saved before the switch existed picks the default up.
    stored = {k: v for k, v in entitlements.PLAN_DEFAULTS["plus"].items() if k != "courses"}
    assert entitlements.normalize(stored, "plus", 4.99)["courses"] is True
    assert entitlements.normalize(stored, "free", 0.0)["courses"] is False


def test_courses_guard_keeps_only_the_list_open(db):
    from pathmind.services import entitlements

    app = FastAPI()
    current = {"user": _user("u_free")}

    async def who():
        set_current_user(current["user"])

    @app.api_route(
        "/api/courses{rest:path}",
        methods=["GET", "POST", "PATCH", "DELETE"],
        dependencies=[Depends(who), Depends(entitlements.courses_guard)],
    )
    async def any_route(rest: str):
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/api/courses").status_code == 200
    assert c.post("/api/courses").status_code == 402
    assert c.get("/api/courses/resource-candidates").status_code == 402
    assert c.get("/api/courses/abc/state").status_code == 402
    assert c.patch("/api/courses/abc").status_code == 402
    assert c.delete("/api/courses/abc").status_code == 402
    r = c.post("/api/courses")
    assert "Plus" in r.json()["detail"]
    _subscribe(db, "u_plus", "plus")
    current["user"] = _user("u_plus")
    assert c.post("/api/courses").status_code == 200
    assert c.get("/api/courses/abc/state").status_code == 200


def test_course_list_answers_empty_and_locked_on_free_and_real_data_on_plus(db, monkeypatch):
    from pathmind.api.routers import courses as courses_router

    class Service:
        def list_courses(self):
            return [SimpleNamespace(to_dict=lambda: {"id": "c1", "name": "OS"})]

    monkeypatch.setattr(courses_router, "get_course_service", lambda: Service())
    app = FastAPI()
    current = {"user": _user("u_free")}

    async def who():
        set_current_user(current["user"])

    app.include_router(courses_router.router, prefix="/api/courses", dependencies=[Depends(who)])
    c = TestClient(app)
    free = c.get("/api/courses").json()
    assert free["courses"] == [] and free["locked"] is True and free["colors"]
    _subscribe(db, "u_plus", "plus")
    current["user"] = _user("u_plus")
    plus = c.get("/api/courses").json()
    assert [x["id"] for x in plus["courses"]] == ["c1"] and "locked" not in plus


def test_courses_show_up_in_summary_pricing_and_the_real_app(db, no_resources):
    import re
    from pathlib import Path

    import pathmind.api.main as main
    from pathmind.database.models import SubscriptionPlan
    from pathmind.services import entitlements
    from pathmind.services.quota_service import plan_to_dict

    set_current_user(_user("u_free"))
    summary = entitlements.summary()
    assert summary["features"]["courses"] is False and summary["upgrade_to"]["courses"] == "Plus"
    s = db()
    free = plan_to_dict(s.get(SubscriptionPlan, "free"))
    plus = plan_to_dict(s.get(SubscriptionPlan, "plus"))
    s.close()
    assert "Study courses" not in " | ".join(free["features"])
    assert "Study courses" in " | ".join(plus["features"])
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert re.search(r"^_courses = .*courses_guard", src, re.M)
    block = re.search(r"app\.include_router\(\s*courses\.router[^)]*\)", src, re.S)
    assert block and "dependencies=_courses" in block.group(0)


def test_memory_is_paid_from_plus_and_guards_the_memory_router():
    import re
    from pathlib import Path

    import pathmind.api.main as main
    from pathmind.services import entitlements

    assert "memory" in entitlements.FEATURE_KEYS
    assert entitlements.PLAN_DEFAULTS["free"]["memory"] is False
    for plan in ("plus", "pro", "enterprise"):
        assert entitlements.PLAN_DEFAULTS[plan]["memory"] is True
    stored = {k: v for k, v in entitlements.PLAN_DEFAULTS["plus"].items() if k != "memory"}
    assert entitlements.normalize(stored, "plus", 4.99)["memory"] is True
    assert entitlements.normalize(stored, "free", 0.0)["memory"] is False
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert re.search(r'^_memory = .*feature_dependency\("memory"\)', src, re.M)
    block = re.search(r"app\.include_router\(\s*memory\.router[^)]*\)", src, re.S)
    assert block and "dependencies=_memory" in block.group(0)
