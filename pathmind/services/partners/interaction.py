"""Request-local ownership for a human's interaction with a Partner.

Partner configuration and knowledge assets are shared, admin-managed resources.
Conversation history and learned preferences are relationship state, however,
and must follow the authenticated human rather than the process-wide Partner.
This module keeps that distinction in one place and exposes it to Partner-only
tools through a ContextVar that is safe across concurrent async turns.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from pathmind.multi_user.models import CurrentUser
from pathmind.multi_user.paths import (
    ensure_scope_workspace,
    get_admin_path_service,
    get_path_service_for_scope,
)
from pathmind.partners.config.paths import (
    get_partner_sessions_dir,
    get_partner_user_sessions_dir,
    get_partner_user_workspace,
)
from pathmind.services.path_service import PathService

from .scope import is_partner_user_id
from .sessions import PartnerSessionStore


def actor_for_account(user_id: str) -> CurrentUser | None:
    """Rebuild the ``CurrentUser`` for a stored account id, or None if it is gone.

    Channel traffic carries no session, so an identity established earlier (by
    linking a chat account) has to be reconstituted from the account store on
    every turn — and a user who has since been deleted must resolve to nobody
    rather than to a scope that no longer belongs to anyone.
    """
    from pathmind.multi_user.identity import get_user_by_id
    from pathmind.multi_user.paths import scope_for_user

    found = get_user_by_id(user_id)
    if found is None:
        return None
    username, record = found
    if record.get("disabled"):
        return None
    raw_role = str(record.get("role") or "user")
    role: Literal["admin", "rnd", "educator", "user"] = (
        raw_role if raw_role in ("admin", "rnd", "educator") else "user"  # type: ignore[assignment]
    )
    return CurrentUser(
        id=user_id,
        username=username,
        role=role,
        scope=scope_for_user(user_id, is_admin=role == "admin"),
    )


def _partner_owner(partner_id: str) -> str:
    try:
        from pathmind.multi_user.partner_access import partner_owner_id

        return partner_owner_id(partner_id) or ""
    except Exception:  # pragma: no cover - a missing partner reads as admin-managed
        return ""


def personal_actor_id(actor: CurrentUser | None, partner_id: str | None = None) -> str | None:
    """Account id requiring private Partner state, or ``None`` for the shared scope.

    Regular users always get private state. An admin keeps the partner's shared
    scope only on partners they operate — admin-managed ones (no owner) or their
    own. On a partner someone else owns, an admin's conversations are private
    like any user's: they never land in (or read back from) the owner's shared
    thread pool, and the owner never sees them.
    """
    if actor is None or is_partner_user_id(actor.id):
        return None
    if actor.is_admin:
        if not partner_id:
            return None
        owner = _partner_owner(partner_id)
        if not owner or owner == actor.id:
            return None
    return actor.id


# One store instance per directory, process-wide. Sharing matters: the store
# serialises writes through an instance-level lock, so two objects over the same
# directory would not exclude each other.
_STORES: dict[Path, PartnerSessionStore] = {}


def session_sessions_dir(partner_id: str, actor: CurrentUser | None) -> Path:
    """Where *actor*'s conversations with *partner_id* live.

    Each human gets their own directory under the partner; the partner's own
    top-level ``sessions/`` holds the shared, un-attributed threads — admin
    turns and channel traffic from senders who have not linked an account.
    """
    actor_id = personal_actor_id(actor, partner_id)
    if actor_id is None:
        return get_partner_sessions_dir(partner_id)
    return get_partner_user_sessions_dir(partner_id, actor_id)


def session_store_for(partner_id: str, actor: CurrentUser | None) -> PartnerSessionStore:
    """The single store that owns *actor*'s conversations with *partner_id*.

    Every reader and writer resolves through here — the runner persisting a
    turn, and the history/session endpoints reading it back — so a message is
    never written to one place and looked for in another.
    """
    directory = session_sessions_dir(partner_id, actor)
    store = _STORES.get(directory)
    if store is None:
        store = PartnerSessionStore(directory)
        _STORES[directory] = store
    return store


def forget_partner_stores(partner_id: str) -> None:
    """Drop cached stores for a deleted partner."""
    root = get_partner_sessions_dir(partner_id).parent.resolve()
    for directory in [d for d in _STORES if root in d.resolve().parents or d.resolve() == root]:
        _STORES.pop(directory, None)


@dataclass(frozen=True, slots=True)
class PartnerTurnContext:
    partner_id: str
    actor: CurrentUser | None
    store: PartnerSessionStore
    own_memory: PathService
    shared_memory: PathService
    #: Display name of the partner, for surfaces that label content with it
    #: (e.g. the question-bank session a partner records mistakes into).
    partner_name: str = ""

    @property
    def actor_id(self) -> str | None:
        return personal_actor_id(self.actor, self.partner_id)


_current_turn: ContextVar[PartnerTurnContext | None] = ContextVar(
    "pathmind_partner_turn", default=None
)


def build_partner_turn_context(
    partner_id: str,
    actor: CurrentUser | None,
    store: PartnerSessionStore,
    *,
    legacy_own_memory: PathService,
    partner_name: str = "",
) -> PartnerTurnContext:
    actor_id = personal_actor_id(actor, partner_id)
    if actor_id is None:
        return PartnerTurnContext(
            partner_id=partner_id,
            actor=actor,
            store=store,
            own_memory=legacy_own_memory,
            shared_memory=get_admin_path_service(),
            partner_name=partner_name,
        )

    assert actor is not None
    ensure_scope_workspace(actor.scope)
    private_workspace = get_partner_user_workspace(partner_id, actor_id)
    private_memory = private_workspace / "memory"
    private_memory.mkdir(parents=True, exist_ok=True)
    return PartnerTurnContext(
        partner_id=partner_id,
        actor=actor,
        store=store,
        own_memory=PathService(workspace_root=private_workspace),
        shared_memory=get_path_service_for_scope(actor.scope),
        partner_name=partner_name,
    )


def get_partner_turn_context() -> PartnerTurnContext | None:
    return _current_turn.get()


@contextmanager
def partner_turn_context(context: PartnerTurnContext) -> Iterator[None]:
    token: Token[PartnerTurnContext | None] = _current_turn.set(context)
    try:
        yield
    finally:
        _current_turn.reset(token)


__all__ = [
    "PartnerTurnContext",
    "actor_for_account",
    "build_partner_turn_context",
    "forget_partner_stores",
    "get_partner_turn_context",
    "partner_turn_context",
    "personal_actor_id",
    "session_sessions_dir",
    "session_store_for",
]
