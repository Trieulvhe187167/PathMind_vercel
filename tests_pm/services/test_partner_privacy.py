"""Admins can't read other people's Partner chats; owners keep their shared pool."""

from __future__ import annotations

from pathlib import Path

import pytest

from pathmind.multi_user.context import reset_current_user, set_current_user
from pathmind.multi_user.models import CurrentUser, UserScope

OWNERS = {"users-bot": "u_owner", "admin-bot": "", "root-bot": "u_admin"}


def _user(uid: str, role: str = "user") -> CurrentUser:
    return CurrentUser(uid, uid, role, UserScope("user", uid, Path("/tmp") / uid))


ADMIN = _user("u_admin", "admin")
OWNER = _user("u_owner")
OTHER = _user("u_other")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import pathmind.multi_user.partner_access as access
    import pathmind.services.partners.interaction as interaction

    monkeypatch.setattr(access, "partner_owner_id", lambda pid: OWNERS.get(pid, ""))

    def shared_dir(pid):
        path = tmp_path / pid / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_dir(pid, uid):
        path = tmp_path / pid / "users" / uid / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(interaction, "get_partner_sessions_dir", shared_dir)
    monkeypatch.setattr(interaction, "get_partner_user_sessions_dir", user_dir)
    interaction._STORES.clear()
    yield tmp_path
    interaction._STORES.clear()


def test_admin_scope_is_private_on_someone_elses_partner(env):
    from pathmind.services.partners.interaction import personal_actor_id, session_sessions_dir

    assert personal_actor_id(ADMIN, "users-bot") == "u_admin"  # someone else's partner
    assert personal_actor_id(ADMIN, "admin-bot") is None  # admin-managed: shared pool
    assert personal_actor_id(ADMIN, "root-bot") is None  # the admin's own partner
    assert personal_actor_id(ADMIN) is None  # no partner given: unchanged legacy behaviour
    assert personal_actor_id(OWNER, "users-bot") == "u_owner"
    assert personal_actor_id(None, "users-bot") is None

    assert session_sessions_dir("users-bot", ADMIN) == env / "users-bot" / "users" / "u_admin" / "sessions"
    assert session_sessions_dir("admin-bot", ADMIN) == env / "admin-bot" / "sessions"


def test_shared_pool_visibility(env):
    from pathmind.multi_user.partner_access import can_see_shared_partner_threads

    assert can_see_shared_partner_threads("users-bot", OWNER)
    assert not can_see_shared_partner_threads("users-bot", ADMIN)
    assert not can_see_shared_partner_threads("users-bot", OTHER)
    assert can_see_shared_partner_threads("admin-bot", ADMIN)
    assert not can_see_shared_partner_threads("admin-bot", OTHER)


def test_history_endpoint_hides_other_peoples_chats(env, monkeypatch):
    import asyncio

    from pathmind.api.routers import partners as router
    from pathmind.services.partners.interaction import session_store_for
    from pathmind.services.partners.manager import PartnerManager

    session_store_for("users-bot", OWNER).append("web:a", "user", "owner secret")
    session_store_for("users-bot", None).append("tg:1", "user", "unlinked telegram")

    manager = PartnerManager.__new__(PartnerManager)
    monkeypatch.setattr(router, "get_partner_manager", lambda: manager)

    def history_as(user):
        token = set_current_user(user)
        try:
            return [m["content"] for m in asyncio.run(router.get_partner_history("users-bot"))]
        finally:
            reset_current_user(token)

    assert history_as(ADMIN) == []
    assert sorted(history_as(OWNER)) == ["owner secret", "unlinked telegram"]

    # The admin's own chat with the user's partner is private too.
    token = set_current_user(ADMIN)
    try:
        manager.session_store("users-bot").append("web:b", "user", "admin note")
    finally:
        reset_current_user(token)
    assert history_as(ADMIN) == ["admin note"]
    assert "admin note" not in history_as(OWNER)

    sessions_as_admin = manager.session_store("users-bot", actor=ADMIN).list_sessions()
    assert len(sessions_as_admin) == 1  # only the admin's own thread


def test_live_turn_keys_are_per_person(monkeypatch):
    import pathmind.multi_user.partner_access as access
    from pathmind.services.partners.manager import PartnerManager

    monkeypatch.setattr(access, "partner_owner_id", lambda pid: OWNERS.get(pid, ""))
    keys = {}
    for user in (ADMIN, OWNER, OTHER):
        token = set_current_user(user)
        try:
            keys[user.id] = PartnerManager._live_turn_key("users-bot", "web:x")
        finally:
            reset_current_user(token)
    assert len(set(keys.values())) == 3
