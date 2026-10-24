"""Seed files: one JSON file makes a fresh install usable, and re-applying adds nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "pathmind-seed.example.json"


@pytest.fixture()
def env(mu_isolated_root, tmp_path, monkeypatch):
    from pathmind.database import connection
    from pathmind.database.models import Base, SubscriptionPlan
    from pathmind.services import seed
    from pathmind.services.storage import object_store

    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(connection, "engine", engine)
    monkeypatch.setattr(connection, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
    monkeypatch.setattr(seed, "seed_dir", lambda: tmp_path / "seed")
    monkeypatch.setattr(object_store, "_implicit_root", lambda: str(tmp_path / "objects"))
    with connection.SessionLocal() as db:
        db.add_all([SubscriptionPlan(id="free", name="Free", price_monthly=0, price_yearly=0, is_active=True),
                    SubscriptionPlan(id="plus", name="Plus", price_monthly=4.99, price_yearly=49, is_active=True)])
        db.commit()
    (tmp_path / "seed").mkdir()
    return tmp_path / "seed"


def _example() -> dict:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if not EXAMPLE.exists() or not data.get("accounts"):
        pytest.skip("seed example missing")
    return data


def test_example_seed_builds_a_working_install(env):
    from pathmind.database.connection import get_db_session
    from pathmind.database.models import KnowledgeDomain, KnowledgeEdge, Subscription
    from pathmind.services import seed
    from pathmind.services.auth import get_user_info

    (env / "team.json").write_text(json.dumps(_example(), ensure_ascii=False), encoding="utf-8")
    seed.apply_pending()
    assert [get_user_info(u)["role"] for u in ("admin", "rnd", "teacher", "student")] == ["admin", "rnd", "educator", "user"]
    creds = (env / "seed-accounts.txt").read_text(encoding="utf-8").splitlines()
    assert len(creds) == 4 and all(len(line.split("\t")[2]) >= 12 for line in creds)
    with get_db_session() as db:
        domains = {d.name: d.id for d in db.query(KnowledgeDomain).all()}
        assert set(domains) == {"Đại số 8", "Mathematics (sample)"}
        assert db.query(KnowledgeEdge).filter_by(domain_id=domains["Đại số 8"]).count() == 8
        teacher = get_user_info("teacher")["id"]
        assert db.query(Subscription).filter_by(user_id=teacher, status="active").one().plan_id == "plus"
    # an unchanged file is not applied again; a forced re-apply adds nothing
    assert seed.apply_file(env / "team.json") is None
    again = seed.apply_file(env / "team.json", force=True)
    assert not again["accounts"] and not again["domains"] and not again["labels"]


def test_llm_providers_from_a_seed_are_stored_encrypted(env, monkeypatch, tmp_path):
    from pathmind.services import seed
    from pathmind.services.config import model_catalog

    path = tmp_path / "catalog.json"
    service = model_catalog.ModelCatalogService(path)
    monkeypatch.setattr(model_catalog.ModelCatalogService, "get_instance", classmethod(lambda cls, p=None: service))
    report = seed.apply_seed({"llm_providers": [
        {"name": "Proxy", "binding": "custom", "base_url": "https://example.test/v1", "api_key": "sk-test-123",
         "models": ["vendor/model-a"], "default": True},
    ]})
    assert report["llm_providers"] == ["Proxy"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["connections"][0]["api_key"].startswith("enc:v1:")
    loaded = service.load()
    assert loaded["connections"][0]["api_key"] == "sk-test-123"
    assert loaded["services"]["llm"]["active_profile_id"].startswith("llm-profile-seed-")
    assert seed.apply_seed({"llm_providers": [{"name": "Proxy", "api_key": "x", "models": ["m"]}]})["llm_providers"] == []
