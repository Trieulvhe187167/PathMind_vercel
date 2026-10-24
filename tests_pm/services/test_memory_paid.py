"""Memory as a paid, per-account surface: private runs, own settings, credit pre-check."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from pathmind.multi_user.context import set_current_user, user_from_token_payload
from pathmind.services.memory.consolidator.runs import RunBusyError, RunManager


def _user(uid: str, role: str = "user"):
    return user_from_token_payload(SimpleNamespace(user_id=uid, username=uid, role=role))


def _run(coro):
    return asyncio.run(coro)


async def _idle(on_event):  # finishes at once
    return None


async def _blocked(on_event):
    await asyncio.sleep(30)


def test_two_accounts_can_run_the_same_document_at_once():
    async def go():
        mgr = RunManager()
        a = await mgr.start(layer="L3", key="profile", mode="update", runner=_blocked, owner_id="a")
        b = await mgr.start(layer="L3", key="profile", mode="update", runner=_blocked, owner_id="b")
        assert a.id != b.id
        with pytest.raises(RunBusyError):
            await mgr.start(layer="L3", key="profile", mode="update", runner=_blocked, owner_id="a")
        await mgr.cancel(a.id, "a")
        await mgr.cancel(b.id, "b")

    _run(go())


def test_a_run_is_invisible_to_other_accounts():
    async def go():
        mgr = RunManager()
        a = await mgr.start(layer="L3", key="profile", mode="update", runner=_blocked, owner_id="a")
        assert mgr.get(a.id, "a") is a
        assert mgr.get(a.id, "b") is None
        assert [r.id for r in mgr.list_for(owner_id="a")] == [a.id]
        assert mgr.list_for(owner_id="b") == []
        assert await mgr.cancel(a.id, "b") is False
        assert a.active
        with pytest.raises(KeyError):
            await mgr.undo_last(a.id, "b")
        assert await mgr.cancel(a.id, "a") is True

    _run(go())


def test_finished_run_frees_the_slot_for_its_owner_only():
    async def go():
        mgr = RunManager()
        r = await mgr.start(layer="L2", key="chat", mode="audit", runner=_idle, owner_id="a")
        for _ in range(50):
            if not r.active:
                break
            await asyncio.sleep(0.02)
        assert not r.active
        assert mgr.active_for("L2", "chat", "a") is None
        again = await mgr.start(layer="L2", key="chat", mode="audit", runner=_idle, owner_id="a")
        assert again.id != r.id

    _run(go())


@pytest.fixture()
def own_memory(tmp_path, monkeypatch):
    import pathmind.services.memory.paths as paths
    import pathmind.services.memory.settings as ms

    root = {"dir": tmp_path / "alice"}
    monkeypatch.setattr(paths, "memory_root", lambda: root["dir"])
    monkeypatch.setattr(ms.ConfigManager, "load_config", lambda self: {})
    return root


def test_each_account_keeps_its_own_memory_settings(own_memory, tmp_path):
    import pathmind.services.memory.settings as ms

    base = ms.load_memory_settings()
    saved = ms.save_memory_settings({"update": {"l2_budget": 7}})
    assert saved.update.l2_budget == 7
    assert (tmp_path / "alice" / "settings.json").exists()
    own_memory["dir"] = tmp_path / "bob"
    assert ms.load_memory_settings().update.l2_budget == base.update.l2_budget
    own_memory["dir"] = tmp_path / "alice"
    assert ms.load_memory_settings().update.l2_budget == 7


def test_a_broken_override_file_falls_back_to_defaults(own_memory, tmp_path):
    import pathmind.services.memory.settings as ms

    d = tmp_path / "alice"
    d.mkdir()
    (d / "settings.json").write_text("{not json", encoding="utf-8")
    assert ms.load_memory_settings().update.l2_budget == ms.UpdateSettings().l2_budget


def test_saving_user_settings_does_not_write_the_deployment_config(own_memory, monkeypatch):
    import pathmind.services.memory.settings as ms

    calls = []
    monkeypatch.setattr(ms.ConfigManager, "save_config", lambda self, c: calls.append(c))
    ms.save_memory_settings({"dedup": {"iterations": 2}})
    assert calls == []
    ms.save_memory_settings({"dedup": {"iterations": 2}}, scope="global")
    assert len(calls) == 1


def test_ai_run_is_refused_with_402_when_the_balance_is_empty(monkeypatch):
    import pathmind.api.routers.memory as mem
    import pathmind.services.quota_service as qs

    set_current_user(_user("u_memory"))
    monkeypatch.setattr(qs, "check_turn_allowed", lambda uid, db, role=None: (False, "no credits"))
    with pytest.raises(HTTPException) as exc:
        mem._require_credits()
    assert exc.value.status_code == 402


def test_credit_precheck_never_blocks_on_a_lookup_failure(monkeypatch):
    import pathmind.api.routers.memory as mem
    import pathmind.services.quota_service as qs

    set_current_user(_user("u_memory"))

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(qs, "check_turn_allowed", boom)
    mem._require_credits()  # does not raise


def test_owner_id_is_the_signed_in_account():
    import pathmind.api.routers.memory as mem

    set_current_user(_user("u_owner"))
    assert mem._owner_id() == "u_owner"
