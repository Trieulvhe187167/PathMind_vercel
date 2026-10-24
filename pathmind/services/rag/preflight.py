"""Per-engine environment preflight checks.

Powers the "check whether this engine can run right now" affordance on each
engine's detail page. Every check is best-effort and never raises — a failed
import or missing config becomes a failed/optional check, not an exception.

A check is ``{key, label, ok, detail, optional}``. Overall ``ok`` is true when
every *required* (non-optional) check passes.
"""

from __future__ import annotations

from typing import Any

from .factory import (
    GRAPHRAG_PROVIDER,
    LIGHTRAG_PROVIDER,
    normalize_provider_name,
)


def _check(key: str, label: str, ok: bool, detail: str = "", *, optional: bool = False) -> dict:
    return {"key": key, "label": label, "ok": bool(ok), "detail": detail, "optional": optional}


def _active_chat_model() -> tuple[str | None, str]:
    """Return ``(model, binding)`` for the active chat LLM, or ``(None, "")``."""
    try:
        from pathmind.services.config import resolve_llm_runtime_config

        cfg = resolve_llm_runtime_config()
        return getattr(cfg, "model", None), str(getattr(cfg, "binding", "") or "")
    except Exception:
        return None, ""


def _active_embedding() -> tuple[str | None, int]:
    """Return ``(model, dim)`` for the active embedding model, or ``(None, 0)``."""
    try:
        from pathmind.services.embedding import get_embedding_config

        cfg = get_embedding_config()
        return getattr(cfg, "model", None), int(getattr(cfg, "dim", 0) or 0)
    except Exception:
        return None, 0


def _graphrag_preflight() -> dict:
    try:
        from .pipelines.graphrag.config import is_graphrag_available

        installed = is_graphrag_available()
    except Exception:
        installed = False
    emb_model, emb_dim = _active_embedding()
    chat_model, _ = _active_chat_model()
    return _finalize(
        [
            _check(
                "package",
                "GraphRAG package installed",
                installed,
                "Installed." if installed else "pip install 'pathmind[graphrag]'",
            ),
            _check(
                "chat",
                "Active chat model",
                bool(chat_model),
                chat_model or "Configure one in the model catalog.",
            ),
            _check(
                "embedding",
                "Active embedding model",
                bool(emb_model) and emb_dim > 0,
                f"{emb_model} · {emb_dim}d" if emb_model else "Configure one in the model catalog.",
            ),
        ]
    )


def _lightrag_preflight() -> dict:
    package_detail = "pip install 'pathmind[rag-lightrag]'"
    try:
        from .pipelines.lightrag.config import is_lightrag_available
        from .pipelines.lightrag.engine import LIGHTRAG_VERSION, installed_version

        current_version = installed_version() if is_lightrag_available() else ""
        installed = current_version == LIGHTRAG_VERSION
        if current_version:
            package_detail = (
                f"Installed {current_version}."
                if installed
                else f"Found {current_version}; required {LIGHTRAG_VERSION}."
            )
    except Exception:
        installed = False
    emb_model, emb_dim = _active_embedding()
    chat_model, binding = _active_chat_model()
    vision_ok = False
    if chat_model:
        try:
            from pathmind.services.llm.capabilities import supports_vision

            vision_ok = supports_vision(binding, chat_model)
        except Exception:
            vision_ok = False
    return _finalize(
        [
            _check(
                "package",
                "LightRAG package installed",
                installed,
                package_detail,
            ),
            _check(
                "chat",
                "Active chat model",
                bool(chat_model),
                chat_model or "Configure one in the model catalog.",
            ),
            _check(
                "embedding",
                "Active embedding model",
                bool(emb_model) and emb_dim > 0,
                f"{emb_model} · {emb_dim}d" if emb_model else "Configure one in the model catalog.",
            ),
            _check(
                "vision",
                "Vision model for multimodal",
                vision_ok,
                "Active chat model supports vision."
                if vision_ok
                else "Active chat model has no vision — image analysis is disabled.",
                optional=True,
            ),
        ]
    )


def _finalize(checks: list[dict]) -> dict:
    ok = all(c["ok"] for c in checks if not c["optional"])
    return {"ok": ok, "checks": checks}


_PREFLIGHTS = {
    GRAPHRAG_PROVIDER: _graphrag_preflight,
    LIGHTRAG_PROVIDER: _lightrag_preflight,
}


def engine_preflight(provider: str) -> dict[str, Any]:
    """Run the requirement checks for ``provider`` and return the report."""
    return _PREFLIGHTS[normalize_provider_name(provider)]()


__all__ = ["engine_preflight"]
