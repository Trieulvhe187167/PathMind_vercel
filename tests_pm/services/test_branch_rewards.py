"""Personal branch (D9), mastery path (D7) and reward points (D3) business rules."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.models import RewardLedger, SubscriptionPlan
from pathmind.services import quota_service as q
from pathmind.services import rewards
from pathmind.services.knowledge_branch import overlay, path, service as br
from pathmind.services.knowledge_branch.service import BranchError

DOC = b"# Algebra\n\nintro\n\n## Linear equations\n\ntext\n\n## Quadratics\n\nmore\n"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    import pathmind.database.connection as connection

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(connection, "SessionLocal", factory)
    session = factory()
    for plan_id, name, price in (("free", "Free", 0), ("plus", "Plus", 4.99), ("pro", "Pro", 9.99)):
        session.add(SubscriptionPlan(id=plan_id, name=name, price_monthly=price, price_yearly=price * 10, is_active=True))
    session.commit()
    q.ensure_db_user(session, "u1", "u1", "user")
    session.commit()
    return session


def _ids(db, user_id="u1") -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(nodes):
        for n in nodes:
            out[n["title"]] = n["id"]
            walk(n["children"])

    walk(br.tree_of(db, user_id)["nodes"])
    return out


def test_enrichment_builds_outline_and_skips_same_file(db):
    first = overlay.enrich_from_document(db, "u1", "algebra.md", DOC, keep_original=False)
    again = overlay.enrich_from_document(db, "u1", "renamed.md", DOC, keep_original=False)
    assert first["created"] == 4
    assert again["created"] == 0 and again["skipped"] == "already_processed"
    assert {"Algebra", "Linear equations", "Quadratics"} <= set(_ids(db))


def test_auto_enrich_never_breaks_and_skips_big_files(db, tmp_path):
    good = tmp_path / "notes.md"
    good.write_bytes(DOC)
    big = tmp_path / "big.md"
    big.write_bytes(b"# x\n" + b"a" * (overlay.MAX_AUTO_BYTES + 1))
    odd = tmp_path / "image.png"
    odd.write_bytes(b"\x89PNG")
    assert overlay.auto_enrich_files("u1", [str(big), str(odd), str(tmp_path / "missing.md"), str(good)]) == 4


def test_mastery_needs_prerequisites_and_rewards_once(db):
    overlay.enrich_from_document(db, "u1", "algebra.md", DOC, keep_original=False)
    ids = _ids(db)
    lin, quad = ids["Linear equations"], ids["Quadratics"]
    br.set_prerequisites(db, "u1", quad, [lin])

    with pytest.raises(BranchError) as locked:
        path.set_mastered(db, "u1", quad, True)
    assert locked.value.status == 409
    assert next(i for i in path.mastery_path(db, "u1")["items"] if i["id"] == quad)["status"] == "locked"

    path.set_mastered(db, "u1", lin, True)
    path.set_mastered(db, "u1", quad, True)
    with pytest.raises(BranchError):  # a mastered node depends on it
        path.set_mastered(db, "u1", lin, False)
    path.set_mastered(db, "u1", quad, False)
    path.set_mastered(db, "u1", quad, True)  # re-marking earns nothing more
    assert rewards.balance(db, "u1") == 2 * rewards.DEFAULT_RULES["earn.node_mastered"]


def test_activity_points_have_a_daily_cap(db):
    cap = rewards.DEFAULT_RULES["cap.daily_activity_points"]
    given = sum(rewards.earn_once(db, "u1", "earn.node_mastered", ref=f"mastery:n{i}") for i in range(100))
    assert given == cap


def test_redeem_rules(db):
    rewards.grant(db, "u1", 100, "manual")
    with pytest.raises(rewards.RewardError):
        rewards.redeem(db, "u1", "ai_credits", rewards.DEFAULT_RULES["min.redeem"] - 1)
    with pytest.raises(rewards.RewardError):
        rewards.redeem(db, "u1", "ai_credits", 10_000)
    out = rewards.redeem(db, "u1", "plan_days", 45)  # rounds down to whole days
    assert out["days"] == 2 and out["points_spent"] == 40 and out["plan"] == "Plus"
    assert rewards.balance(db, "u1") == 60
    assert db.query(RewardLedger).filter(RewardLedger.points < 0).count() == 1


def test_redeem_never_shortens_a_permanent_plan(db):
    q.ensure_db_user(db, "u2", "u2", "user")
    q.activate_subscription(db, "u2", db.get(SubscriptionPlan, "pro"), source="admin", duration_days=0)
    db.commit()
    rewards.grant(db, "u2", 100, "manual")
    with pytest.raises(rewards.RewardError):
        rewards.redeem(db, "u2", "plan_days", 40)
    assert q.get_active_subscription("u2", db).current_period_end is None
    assert rewards.balance(db, "u2") == 100


def test_reset_keeps_a_restorable_version(db):
    overlay.enrich_from_document(db, "u1", "algebra.md", DOC, keep_original=False)
    assert overlay.reset(db, "u1") == 4
    assert br.tree_of(db, "u1")["node_count"] == 0
    versions = br.list_versions(db, "u1")
    assert versions and versions[0]["label"] == "Before reset"
