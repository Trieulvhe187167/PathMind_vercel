"""Assemble one turn's external-provider tool surface.

This is the composition seam that used to live inline in the chat pipeline:
start the providers, work out what this caller may use, filter the pool,
build the progressive-disclosure loader, and render the manifest block. It
lives here so the policy is testable on its own and so a second consumer (or
a second provider kind) does not mean a second copy inside a pipeline.

Contract: :func:`build_tool_view` **never raises**. A provider that is down,
misconfigured, or slow degrades to "no external tools this turn" — the turn
itself must still run.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Iterable

from pathmind.core.tool_protocol import BaseTool, ToolLookup
from pathmind.runtime.providers.allowlist import Allowlist
from pathmind.runtime.providers.scope import ToolScope
from pathmind.runtime.registry.deferred_tools import (
    DeferredToolLoader,
    render_deferred_tools_manifest,
)
from pathmind.runtime.registry.scoped_registry import ScopedToolRegistry

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ProviderToolView:
    """One turn's resolved provider surface."""

    #: Registry the turn should use for lookup and dispatch (scoped view).
    registry: ToolLookup
    #: ``None`` when this turn has no external tools at all.
    loader: DeferredToolLoader | None
    #: Provider tools visible this turn — the manifest's contents, and what
    #: the context-budget chip counts as "unloaded extended tools".
    pool: tuple[BaseTool, ...]
    #: Rendered ``extended_tools`` system-prompt block ("" when suppressed).
    manifest: str

    @classmethod
    def empty(cls, registry: ToolLookup) -> "ProviderToolView":
        return cls(registry=registry, loader=None, pool=(), manifest="")

    def attach(self, tool_schemas: list[dict[str, Any]]) -> None:
        """Add already-loaded schemas and bind the turn's live schema list.

        The loader appends to this list as the model calls ``load_tools``, and
        the agent loop re-reads it every round, so tools become callable
        without rebuilding the request.
        """
        if self.loader is None:
            return
        tool_schemas.extend(self.loader.initial_schemas())
        self.loader.bind_live_schemas(tool_schemas)


async def build_tool_view(
    *,
    base_registry: ToolLookup,
    scope: ToolScope,
    language: str = "en",
    refusal_message: str = "",
    overlay_tools: Iterable[BaseTool] = (),
    preloaded_names: Iterable[str] = (),
) -> ProviderToolView:
    """Resolve the provider tools *scope* may use this turn."""
    try:
        return await _build(
            base_registry=base_registry,
            scope=scope,
            language=language,
            refusal_message=refusal_message,
            overlay_tools=tuple(overlay_tools),
            preloaded_names=frozenset(preloaded_names),
        )
    except Exception:
        logger.warning("provider tool view assembly failed; continuing without", exc_info=True)
        return ProviderToolView.empty(
            ScopedToolRegistry(base=base_registry, allowed=Allowlist.of([]))
        )


async def _build(
    *,
    base_registry: ToolLookup,
    scope: ToolScope,
    language: str,
    refusal_message: str,
    overlay_tools: tuple[BaseTool, ...],
    preloaded_names: frozenset[str],
) -> ProviderToolView:
    from pathmind.runtime.registry.session_state import load_loaded_tools

    shared_pool = list(base_registry.deferred_tools())
    if not shared_pool and not overlay_tools:
        return ProviderToolView.empty(base_registry)

    # Deferred tools are no longer fed by external servers. What is left is the
    # shared pool of the base registry, gated by the caller's whitelist (partner
    # turns) and the overlay tools bound to resources selected for this turn.
    if scope.exclusive_capability:
        allowed = Allowlist.of([])
    else:
        caller = Allowlist.of(scope.caller_whitelist) if scope.is_partner else Allowlist.unrestricted()
        allowed = caller
    # Resource-bound overlays (including PageIndex SDK tools) are authorised by
    # possession of the selected resource and live only in this turn's registry.
    allowed = allowed.widen(tool.get_definition().name for tool in overlay_tools)

    pool = tuple(
        tool
        for tool in (*shared_pool, *overlay_tools)
        if allowed.allows(tool.get_definition().name)
    )
    registry = ScopedToolRegistry(
        base=base_registry,
        overlay=[*overlay_tools],
        allowed=allowed,
        refusal_message=refusal_message,
    )
    if not pool:
        # Nothing authorised: no manifest, no loader — but keep the scoped
        # registry so dispatch still refuses names the model may invent.
        return ProviderToolView(registry=registry, loader=None, pool=(), manifest="")

    loader = DeferredToolLoader(
        registry=registry,
        session_id=scope.session_id,
        # Resource-authorised tools are preloaded: holding the resource is the
        # permission, so the model should not have to spend a `load_tools`
        # round-trip before its first retrieval.
        loaded=load_loaded_tools(scope.session_id) | set(preloaded_names),
        allowed=allowed.as_set(),
    )
    manifest = (
        ""
        if scope.exclusive_capability
        else render_deferred_tools_manifest(list(pool), language=language)
    )
    return ProviderToolView(registry=registry, loader=loader, pool=pool, manifest=manifest)


__all__ = ["ProviderToolView", "build_tool_view"]
