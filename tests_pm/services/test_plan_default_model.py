"""Per-plan default model: Free users land on the cheap model when they pick none."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.init_db import DEFAULT_MODEL_PRICES, DEFAULT_PLANS
from pathmind.database.models import ModelPrice, SubscriptionPlan
from pathmind.services import model_pricing as mp
from pathmind.services import quota_service as qs


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for p in DEFAULT_PLANS:
        session.add(SubscriptionPlan(**p))
    for price in DEFAULT_MODEL_PRICES:
        session.add(ModelPrice(is_active=True, **price))
    session.commit()
    mp.invalidate_price_cache()
    yield session
    session.close()
    mp.invalidate_price_cache()


def test_free_plan_defaults_to_a_cheap_model(db):
    free = db.get(SubscriptionPlan, "free")
    assert free.default_model == "deepseek-v4-flash"
    assert qs.plan_allows_model(free, free.default_model, db)
    cheap = mp.price_for("deepseek-v4-flash", db=db).multiplier
    old = mp.price_for("gemini-3.6-flash", db=db).multiplier
    assert cheap < old / 2
    # A user without a paid subscription gets the Free default.
    assert qs.plan_default_model("u-nobody", db) == "deepseek-v4-flash"


def test_catalog_names_match_the_default_by_alias(db):
    assert qs.model_matches_name("deepseek-flash", "deepseek-v4-flash", db)
    assert qs.model_matches_name("models/gemini-3.6-flash", "gemini-3.6-flash", db)
    assert not qs.model_matches_name("gpt-6-sol", "deepseek-v4-flash", db)


def test_plan_default_option_prefers_matching_model(db, monkeypatch):
    from contextlib import contextmanager

    from pathmind.database import connection
    from pathmind.multi_user import model_access

    @contextmanager
    def fake_session():
        yield db

    monkeypatch.setattr(connection, "get_db_session", fake_session)
    options = [
        {"profile_id": "p1", "model_id": "m1", "model": "models/gemini-3.6-flash"},
        {"profile_id": "p2", "model_id": "m2", "model": "deepseek-flash"},
    ]
    chosen = model_access.plan_default_option("u-free", options)
    assert chosen is options[1]


def test_selection_resolves_to_model_name(monkeypatch):
    from pathmind.multi_user import model_access

    monkeypatch.setattr(
        model_access,
        "admin_catalog",
        lambda: {
            "services": {
                "llm": {
                    "profiles": [
                        {"id": "p2", "models": [{"id": "m2", "model": "deepseek-flash"}]}
                    ]
                }
            }
        },
    )
    assert qs.model_name_for_selection({"profile_id": "p2", "model_id": "m2"}) == "deepseek-flash"
    assert qs.model_name_for_selection({"model": "gpt-6-luna"}) == "gpt-6-luna"
    assert qs.model_name_for_selection({}) == ""
