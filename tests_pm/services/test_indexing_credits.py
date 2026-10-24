"""Knowledge-base indexing is billed to plan credits; users get the default embedding."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pathmind.database.connection import Base
from pathmind.database.init_db import DEFAULT_MODEL_PRICES, DEFAULT_PLANS
from pathmind.database.models import ModelPrice, SubscriptionPlan, UsageLog, UsageModelLog
from pathmind.services import indexing_credits as ic
from pathmind.services import model_pricing as mp


def _user(role="user", uid="u-free"):
    return SimpleNamespace(id=uid, username=uid, role=role, is_admin=role == "admin")


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for p in DEFAULT_PLANS:
        session.add(SubscriptionPlan(**p))
    for price in DEFAULT_MODEL_PRICES:
        session.add(ModelPrice(is_active=True, **price))
    session.commit()
    mp.invalidate_price_cache()
    ic.forget_cached_checks()

    from pathmind.database import connection

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(connection, "get_db_session", fake_session)
    # The DB user mirror is created lazily from the identity store.
    from pathmind.services import quota_service

    monkeypatch.setattr(quota_service, "_auth_store_lookup", lambda uid: None)
    yield session
    session.close()
    mp.invalidate_price_cache()
    ic.forget_cached_checks()


def _as(monkeypatch, user):
    monkeypatch.setattr(ic, "_current_user", lambda: user)


# ---------------------------------------------------------------------------
# Embedding charges
# ---------------------------------------------------------------------------


def test_embedding_tokens_prefer_reported_usage():
    assert ic.embedding_tokens(["abc"], {"prompt_tokens": 42}) == (42, False)
    assert ic.embedding_tokens(["abc"], {"promptTokenCount": 9}) == (9, False)
    tokens, estimated = ic.embedding_tokens(["x" * 350], {})
    assert (tokens, estimated) == (100, True)


def test_embedding_is_charged_at_embedding_price(db, monkeypatch):
    _as(monkeypatch, _user())
    credits, usd = ic.charge_embedding(
        model="text-embedding-3-small",
        binding="openai",
        texts=["hello"],
        usage={"prompt_tokens": 1_000_000},
    )
    assert usd == pytest.approx(0.02)
    assert credits == mp.usd_to_credits(0.02)
    log = db.query(UsageLog).filter_by(user_id="u-free").one()
    assert log.credits_used == credits
    row = db.query(UsageModelLog).filter_by(user_id="u-free").one()
    assert row.model == "text-embedding-3-small" and row.calls == 1


def test_unknown_embedding_model_uses_embedding_fallback_not_llm_fallback(db, monkeypatch):
    _as(monkeypatch, _user())
    _, usd = ic.charge_embedding(
        model="qwen3-embedding-8b",
        binding="siliconflow",
        texts=["x"],
        usage={"total_tokens": 1_000_000},
    )
    # $0.15 / 1M, not the $2 / 1M LLM safety row.
    assert usd == pytest.approx(0.15)
    row = db.query(UsageModelLog).filter_by(user_id="u-free").one()
    assert row.price_id == ic.EMBEDDING_FALLBACK_PRICE_ID


def test_local_or_anonymous_embeddings_are_free(db, monkeypatch):
    _as(monkeypatch, _user())
    assert ic.charge_embedding(
        model="nomic-embed-text", binding="ollama", texts=["x"], usage={}, is_local=True
    ) == (0, 0.0)
    _as(monkeypatch, None)
    assert ic.charge_embedding(
        model="text-embedding-3-small", binding="openai", texts=["x" * 100]
    ) == (0, 0.0)
    assert db.query(UsageLog).count() == 0


# ---------------------------------------------------------------------------
# Stopping indexing when credits run out
# ---------------------------------------------------------------------------


def _exhaust(db, uid="u-free"):
    from pathmind.services.quota_service import _today_str, ensure_db_user

    ensure_db_user(db, uid)
    log = db.query(UsageLog).filter_by(user_id=uid, date=_today_str()).first()
    if log is None:
        log = UsageLog(
            user_id=uid,
            date=_today_str(),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            credits_used=0,
            cost_usd=0.0,
            current_storage_bytes=0,
        )
        db.add(log)
    log.credits_used = 10_000_000
    db.commit()


def test_indexing_stops_only_inside_scope_and_never_for_admins(db, monkeypatch):
    _as(monkeypatch, _user())
    ic.ensure_indexing_credits()  # plenty of credits
    _exhaust(db)
    ic.forget_cached_checks()
    ic.ensure_indexing_credits()  # outside an indexing job: no-op
    with ic.indexing_scope():
        with pytest.raises(ic.IndexingCreditsExhausted):
            ic.ensure_indexing_credits()
    allowed, reason = ic.check_indexing_credits()
    assert not allowed and reason

    _as(monkeypatch, _user(role="admin", uid="u-free"))
    ic.forget_cached_checks()
    with ic.indexing_scope():
        ic.ensure_indexing_credits()


def test_embedding_client_bills_each_batch_and_stops_when_exhausted(db, monkeypatch):
    from pathmind.services.embedding import client as emb_client
    from pathmind.services.embedding.adapters.base import EmbeddingResponse

    calls: list[int] = []

    class FakeAdapter:
        async def embed(self, request):
            calls.append(len(request.texts))
            return EmbeddingResponse(
                embeddings=[[0.1, 0.2] for _ in request.texts],
                model="text-embedding-3-small",
                dimensions=2,
                usage={"prompt_tokens": 500_000},
            )

    client = object.__new__(emb_client.EmbeddingClient)
    client.config = SimpleNamespace(
        binding="openai",
        model="text-embedding-3-small",
        dim=2,
        batch_size=2,
        batch_delay=0,
    )
    client.adapter = FakeAdapter()
    client.logger = emb_client.logging.getLogger("test")

    _as(monkeypatch, _user())
    vectors = asyncio.run(client.embed(["a", "b", "c"]))
    assert len(vectors) == 3 and calls == [2, 1]
    row = db.query(UsageModelLog).filter_by(user_id="u-free").one()
    assert row.calls == 2 and row.prompt_tokens == 1_000_000

    _exhaust(db)
    ic.forget_cached_checks()

    async def indexing():
        with ic.indexing_scope():
            return await client.embed(["d"])

    with pytest.raises(ic.IndexingCreditsExhausted):
        asyncio.run(indexing())
    assert calls == [2, 1]  # the provider was not called again
    # Retrieval at chat time is not an indexing job and still works.
    assert len(asyncio.run(client.embed(["q"]))) == 1


def test_rag_service_marks_indexing():
    import logging

    from pathmind.services.rag import service as rag_service

    seen = {}

    class Pipeline:
        async def initialize(self, kb_name, file_paths, **kwargs):
            seen["init"] = ic.in_indexing_scope()
            return True

        async def add_documents(self, kb_name, file_paths, **kwargs):
            seen["add"] = ic.in_indexing_scope()
            return True

    svc = object.__new__(rag_service.RAGService)
    svc.logger = logging.getLogger("test")
    svc._resolve_provider = lambda name: "lightrag"
    svc._get_pipeline = lambda provider: Pipeline()
    # Call the undecorated methods (skip per-KB embedding resolution).
    asyncio.run(rag_service.RAGService.initialize.__wrapped__(svc, "kb", []))
    asyncio.run(rag_service.RAGService.add_documents.__wrapped__(svc, "kb", []))
    assert seen == {"init": True, "add": True}
    assert not ic.in_indexing_scope()


# ---------------------------------------------------------------------------
# Embedding model choice and model options for regular users
# ---------------------------------------------------------------------------

_DEFAULT = {"profile_id": "emb-p", "model_id": "emb-default"}
_OTHER = {"profile_id": "emb-p", "model_id": "emb-large"}


def _wire_embedding(monkeypatch, role):
    import json

    from pathmind.api.routers import knowledge
    from pathmind.services.embedding import config as emb_config
    from pathmind.services.rag import embedding_binding

    monkeypatch.setattr(knowledge, "get_current_user", lambda: _user(role=role))
    monkeypatch.setattr(embedding_binding, "default_selection", lambda catalog=None: dict(_DEFAULT))
    monkeypatch.setattr(emb_config, "get_embedding_config", lambda selection: ("cfg", selection))
    provider = next(iter(embedding_binding.EMBEDDING_PROVIDERS))
    return knowledge, provider, json.dumps


def test_regular_user_cannot_pick_another_embedding_model(monkeypatch):
    knowledge, provider, dumps = _wire_embedding(monkeypatch, "user")
    selection, _ = knowledge._resolve_embedding_form("", provider)
    assert selection == _DEFAULT
    assert knowledge._resolve_embedding_form(dumps(_DEFAULT), provider)[0] == _DEFAULT
    with pytest.raises(HTTPException) as exc:
        knowledge._resolve_embedding_form(dumps(_OTHER), provider)
    assert exc.value.status_code == 403
    # A KB already bound to another model keeps it on re-index.
    entry = {"embedding_selection": dict(_OTHER)}
    assert knowledge._resolve_embedding_form(dumps(_OTHER), provider, entry)[0] == _OTHER


def test_admin_can_pick_any_embedding_model(monkeypatch):
    knowledge, provider, dumps = _wire_embedding(monkeypatch, "admin")
    assert knowledge._resolve_embedding_form(dumps(_OTHER), provider)[0] == _OTHER


def test_model_options_are_restricted_for_regular_users(monkeypatch):
    from pathmind.api.routers import knowledge
    from pathmind.multi_user import model_access

    out = {
        "embedding": {
            "active": dict(_DEFAULT),
            "options": [
                {**_DEFAULT, "profile_name": "E", "label": "small", "model": "s", "detail": ""},
                {**_OTHER, "profile_name": "E", "label": "large", "model": "l", "detail": ""},
            ],
        },
        "llm": {"active": {"profile_id": "a", "model_id": "b"}, "options": [{"x": 1}]},
    }
    monkeypatch.setattr(
        model_access,
        "allowed_llm_options",
        lambda: {
            "active": {"profile_id": "p-ds", "model_id": "m-ds"},
            "options": [
                {
                    "profile_id": "p-ds",
                    "model_id": "m-ds",
                    "profile_name": "DeepSeek",
                    "label": "deepseek-flash",
                    "model": "deepseek-flash",
                }
            ],
        },
    )
    restricted = knowledge._restrict_model_options_for_user(out)
    assert restricted["embedding"]["locked"] is True
    assert [o["model_id"] for o in restricted["embedding"]["options"]] == ["emb-default"]
    assert restricted["llm"]["active"] == {"profile_id": "p-ds", "model_id": "m-ds"}
    assert [o["model"] for o in restricted["llm"]["options"]] == ["deepseek-flash"]


def test_indexing_credit_precheck_refuses_when_exhausted(monkeypatch):
    from pathmind.api.routers import knowledge

    monkeypatch.setattr(ic, "check_indexing_credits", lambda **kw: (False, "No credits left"))
    with pytest.raises(HTTPException) as exc:
        knowledge._indexing_credit_precheck()
    assert exc.value.status_code == 403
    monkeypatch.setattr(ic, "check_indexing_credits", lambda **kw: (True, ""))
    knowledge._indexing_credit_precheck()
