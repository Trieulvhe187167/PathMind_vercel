"""RAG pipeline factory.

Selects a KB's index/retrieve engine by provider name. Two pipelines ship (D2):

* ``graphrag`` — local knowledge-graph retrieval (microsoft/graphrag) for the
                 curated main tree; optional dependency
                 ``pip install 'pathmind[graphrag]'``.
* ``lightrag`` — graph + vector retrieval (HKUDS/LightRAG, multimodal) for
                 documents users add over time; optional dependency
                 ``pip install 'pathmind[rag-lightrag]'``.

A KB is bound to one provider at creation time; later adds and retrieval always
go through that same pipeline (enforced upstream in the knowledge router).
Knowledge bases created with a retired engine are treated as ``lightrag`` KBs
that need a re-index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_PROVIDER = "lightrag"
GRAPHRAG_PROVIDER = "graphrag"
LIGHTRAG_PROVIDER = "lightrag"

# Providers the factory can instantiate. Unknown / retired strings fall back to
# the default (LightRAG) with a re-index hint upstream.
KNOWN_PROVIDERS = frozenset({GRAPHRAG_PROVIDER, LIGHTRAG_PROVIDER})

# D2 (graph retrieval only): these are the only engines a *new* knowledge base may use.
# GraphRAG serves the curated main tree (global, community-level answers); LightRAG
# serves documents users add over time (cheaper incremental indexing, multimodal).
NEW_KB_PROVIDERS = (GRAPHRAG_PROVIDER, LIGHTRAG_PROVIDER)


def default_provider_for_role(role: Optional[str]) -> str:
    """GraphRAG for R&D/administrators (main tree); LightRAG for everyone else."""
    return GRAPHRAG_PROVIDER if str(role or "") in {"admin", "rnd"} else LIGHTRAG_PROVIDER


# Cached pipeline instances keyed by (kb_base_dir, provider).
_PIPELINE_CACHE: Dict[Tuple[Optional[str], str], Any] = {}


def normalize_provider_name(name: Optional[str] = None) -> str:
    """Return a known provider name, falling back to the default.

    Unknown / removed provider strings collapse to the default so a stale config
    never selects a pipeline that no longer exists.
    """
    candidate = (name or "").strip().lower()
    return candidate if candidate in KNOWN_PROVIDERS else DEFAULT_PROVIDER


def provider_uses_embedding_versions(provider: Optional[str]) -> bool:
    """Whether this provider's index versions are keyed by embedding signature.

    GraphRAG and LightRAG write synthetic provider signatures and are not marked
    stale merely because the active embedding profile changed.
    """
    return False


def version_matches_provider(entry: dict[str, Any], provider: Optional[str]) -> bool:
    """Return True when a version-list entry belongs to ``provider``."""
    resolved = normalize_provider_name(provider)
    entry_provider = str(entry.get("provider") or "").strip().lower()
    signature = str(entry.get("signature") or "").strip().lower()
    return entry_provider == resolved or signature == resolved


def has_ready_provider_index(kb_dir: str | Path, provider: Optional[str]) -> bool:
    """Return whether ``kb_dir`` has a ready index for ``provider``."""
    from .index_probe import has_ready_provider_index as _has_ready_provider_index

    return _has_ready_provider_index(kb_dir, provider)


def version_has_provider_output(entry: dict[str, Any], provider: Optional[str]) -> bool:
    """Return True when a version entry is ready and has real provider output."""
    from .index_probe import inspect_provider_version

    return inspect_provider_version(entry, provider).ready


def provider_failure_summary(
    kb_dir: str | Path,
    provider: Optional[str],
    *,
    limit: int = 3,
) -> str:
    """Return a short provider-specific failure summary, when available."""
    from .index_probe import provider_failure_summary as _provider_failure_summary

    return _provider_failure_summary(kb_dir, provider, limit=limit)


def _build_pipeline(provider: str, kb_base_dir: Optional[str], **kwargs: Any):
    if kb_base_dir is not None:
        kwargs.setdefault("kb_base_dir", kb_base_dir)
    if provider == GRAPHRAG_PROVIDER:
        from .pipelines.graphrag.pipeline import GraphRagPipeline

        return GraphRagPipeline(**kwargs)

    from .pipelines.lightrag.pipeline import LightRagPipeline

    return LightRagPipeline(**kwargs)


def get_pipeline(
    name: str = DEFAULT_PROVIDER,
    kb_base_dir: Optional[str] = None,
    **kwargs: Any,
):
    """Return a pipeline instance for ``name`` (cached when no custom kwargs)."""
    provider = normalize_provider_name(name)

    if kwargs:
        # Custom kwargs (e.g. an injected client/loader): build a fresh instance
        # and skip the cache so overrides are honoured.
        return _build_pipeline(provider, kb_base_dir, **kwargs)

    cache_key = (kb_base_dir, provider)
    if cache_key not in _PIPELINE_CACHE:
        _PIPELINE_CACHE[cache_key] = _build_pipeline(provider, kb_base_dir)
    return _PIPELINE_CACHE[cache_key]


def list_pipelines() -> List[Dict[str, Any]]:
    """Describe the available pipelines for the UI provider picker."""
    try:
        from .pipelines.graphrag import config as graphrag_config

        graphrag_ready = graphrag_config.is_graphrag_available()
        graphrag_modes = list(graphrag_config.SUPPORTED_MODES)
        graphrag_default_mode = graphrag_config.DEFAULT_MODE
    except Exception:
        graphrag_ready, graphrag_modes, graphrag_default_mode = False, [], ""

    try:
        from .pipelines.lightrag import config as lightrag_config

        lightrag_ready = lightrag_config.is_lightrag_available()
        lightrag_modes = list(lightrag_config.SUPPORTED_MODES)
        lightrag_default_mode = lightrag_config.DEFAULT_MODE
    except Exception:
        lightrag_ready, lightrag_modes, lightrag_default_mode = False, [], ""

    pipelines = [
        {
            "id": GRAPHRAG_PROVIDER,
            "name": "GraphRAG",
            "description": "Knowledge-graph retrieval with community reports (global/local/drift/basic). Best for the curated main tree. Needs `pip install 'pathmind[graphrag]'`; indexing is LLM-heavy.",
            "configured": graphrag_ready,
            "requires_api_key": False,
            "modes": graphrag_modes,
            "default_mode": graphrag_default_mode,
        },
        {
            "id": LIGHTRAG_PROVIDER,
            "name": "LightRAG",
            "description": "Graph + vector retrieval with multimodal parsing (naive/local/global/hybrid/mix). Best for documents added over time. Needs `pip install 'pathmind[rag-lightrag]'`.",
            "configured": lightrag_ready,
            "requires_api_key": False,
            "modes": lightrag_modes,
            "default_mode": lightrag_default_mode,
        },
    ]
    return pipelines


__all__ = [
    "DEFAULT_PROVIDER",
    "GRAPHRAG_PROVIDER",
    "LIGHTRAG_PROVIDER",
    "KNOWN_PROVIDERS",
    "NEW_KB_PROVIDERS",
    "default_provider_for_role",
    "get_pipeline",
    "has_ready_provider_index",
    "list_pipelines",
    "normalize_provider_name",
    "provider_failure_summary",
    "provider_uses_embedding_versions",
    "version_has_provider_output",
    "version_matches_provider",
]
