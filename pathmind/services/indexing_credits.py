"""Plan credits for knowledge-base indexing.

Indexing a knowledge base calls paid APIs in the background:

* the **embedding model** for every chunk (all engines that embed locally);
* for LightRAG, the **indexing LLM** for entity / relation extraction — many
  calls per document.

LLM calls are already charged by ``CallMeasurement`` (``services/llm/metrics``)
to the user in context; the background tasks run under the KB owner's
context, so LightRAG extraction is billed like chat. Embedding calls do not go
through the LLM layer, so :func:`charge_embedding` bills them here, using the
same price list (``model_prices``) and credit formula.

:func:`indexing_scope` marks work that indexes documents. Inside it,
:func:`ensure_indexing_credits` stops the job once the owner's daily/monthly
credits are used up (admins are never limited). Retrieval at chat time is not
inside the scope: the chat turn itself is already gated by
``check_turn_allowed``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Price row used for embedding models missing from the price list. The general
# "*" row ($2 / $10 per 1M) is an LLM safety net and would overcharge
# embeddings ~10-100×.
EMBEDDING_FALLBACK_PRICE_ID = "*embedding"
_EMBEDDING_FALLBACK_INPUT_PER_M = 0.15

# Characters per token when the provider does not report usage (same ratio as
# ``CallMeasurement``).
_CHARS_PER_TOKEN = 3.5

# One quota lookup per user per this many seconds while a job is indexing.
_CHECK_TTL_SECONDS = 5.0

_indexing_active: ContextVar[bool] = ContextVar("pathmind_indexing_active", default=False)
_cache_lock = threading.Lock()
_check_cache: dict[str, tuple[float, bool, str]] = {}


class IndexingCreditsExhausted(RuntimeError):
    """The KB owner ran out of plan credits while documents were being indexed."""

    code = "billing.quota_exhausted"
    retryable = False


@contextmanager
def indexing_scope() -> Iterator[None]:
    """Mark the current task (and threads that copy its context) as indexing."""
    token = _indexing_active.set(True)
    try:
        yield
    finally:
        _indexing_active.reset(token)


def in_indexing_scope() -> bool:
    return _indexing_active.get()


def _current_user():
    try:
        from pathmind.multi_user.context import get_current_user_or_none

        user = get_current_user_or_none()
    except Exception:
        return None
    if user is None or not getattr(user, "id", ""):
        return None
    return user


def check_indexing_credits(*, estimate: int = 1, use_cache: bool = False) -> tuple[bool, str]:
    """Whether the current user may start (or keep) indexing.

    Admins and callers without a user context are always allowed. Database
    errors fail open, like the chat gate.
    """
    user = _current_user()
    if user is None or getattr(user, "is_admin", False):
        return True, ""
    now = time.monotonic()
    if use_cache:
        with _cache_lock:
            cached = _check_cache.get(user.id)
        if cached and now - cached[0] < _CHECK_TTL_SECONDS:
            return cached[1], cached[2]
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.services.quota_service import check_token_quota

        with get_db_session() as db:
            allowed, reason = check_token_quota(
                user.id, max(1, int(estimate)), db, role=getattr(user, "role", None)
            )
    except Exception as exc:
        logger.warning("Indexing credit check skipped: %s", exc)
        return True, ""
    with _cache_lock:
        _check_cache[user.id] = (now, allowed, reason)
    return allowed, reason


def ensure_indexing_credits() -> None:
    """Raise :class:`IndexingCreditsExhausted` when an indexing job ran out of credits.

    No-op outside :func:`indexing_scope`.
    """
    if not in_indexing_scope():
        return
    allowed, reason = check_indexing_credits(use_cache=True)
    if not allowed:
        raise IndexingCreditsExhausted(reason)


def forget_cached_checks() -> None:
    """Drop cached quota answers (tests, or after an admin changes a plan)."""
    with _cache_lock:
        _check_cache.clear()


def _reported_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    for key in (
        "prompt_tokens",
        "total_tokens",
        "input_tokens",
        "promptTokenCount",
        "totalTokenCount",
        "prompt_eval_count",
    ):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return 0


def embedding_tokens(texts: list[Any], usage: Any = None) -> tuple[int, bool]:
    """Token count of one embedding request: ``(tokens, estimated)``."""
    reported = _reported_tokens(usage)
    if reported:
        return reported, False
    chars = sum(len(t) if isinstance(t, str) else len(str(t or "")) for t in texts or [])
    return int(chars / _CHARS_PER_TOKEN), True


def _embedding_price(model: str, db):
    from pathmind.services import model_pricing

    price = model_pricing.price_for(model, db=db)
    if price.id != model_pricing.FALLBACK_PRICE_ID:
        return price
    rows = model_pricing._load_rows(db)
    return rows.get(EMBEDDING_FALLBACK_PRICE_ID) or model_pricing.Price(
        EMBEDDING_FALLBACK_PRICE_ID,
        "Embedding models not in the price list",
        _EMBEDDING_FALLBACK_INPUT_PER_M,
        _EMBEDDING_FALLBACK_INPUT_PER_M,
        0.0,
    )


def charge_embedding(
    *,
    model: str,
    binding: str,
    texts: list[Any],
    usage: Any = None,
    is_local: bool = False,
) -> tuple[int, float]:
    """Charge one embedding request to the signed-in user.

    Local providers (Ollama, vLLM, …) cost the deployment nothing and are not
    charged. Never raises into the embedding path. Returns ``(credits, usd)``.
    """
    if is_local:
        return 0, 0.0
    user = _current_user()
    if user is None:
        return 0, 0.0
    tokens, estimated = embedding_tokens(texts, usage)
    if tokens <= 0:
        return 0, 0.0
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.services.quota_service import record_llm_call

        with get_db_session() as db:
            record = {
                "prompt_tokens": tokens,
                "completion_tokens": 0,
                "model": model,
                "provider": binding,
                "estimated": estimated,
            }
            return record_llm_call(user.id, record, db, price=_embedding_price(model, db))
    except Exception:
        logger.warning("Unable to charge embedding usage to user", exc_info=True)
        return 0, 0.0


__all__ = [
    "EMBEDDING_FALLBACK_PRICE_ID",
    "IndexingCreditsExhausted",
    "charge_embedding",
    "check_indexing_credits",
    "embedding_tokens",
    "ensure_indexing_credits",
    "forget_cached_checks",
    "in_indexing_scope",
    "indexing_scope",
]
