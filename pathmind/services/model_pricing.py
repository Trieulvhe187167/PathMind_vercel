"""Model price list → API cost (USD) and credits.

Credits make quotas fair across models: one credit is the cost of one token
of a model whose *blended* price equals ``credit_base_usd_per_m()`` (default
$0.25 per 1M tokens — the cheapest mainstream tier). A turn on a model that is
5× more expensive therefore consumes 5× the credits, so a plan's worst-case
API cost is bounded no matter which model the user picks.

Blended price = 80% input + 20% output, which matches PathMind's traffic
(large system prompt / RAG context, short answers). It is only used for the
*displayed* multiplier and pre-turn estimates; the credits actually charged are
computed from the real input / cached / output token counts of every call.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FALLBACK_PRICE_ID = "*"
INPUT_SHARE = 0.8
_CACHE_TTL_SECONDS = 60.0


def credit_base_usd_per_m() -> float:
    try:
        value = float(os.environ.get("BILLING_CREDIT_USD_PER_MTOK", "0.25"))
        return value if value > 0 else 0.25
    except ValueError:
        return 0.25


@dataclass(frozen=True)
class Price:
    id: str
    display_name: str
    input_per_m: float
    cached_input_per_m: float
    output_per_m: float
    free: bool = False

    @property
    def blended_per_m(self) -> float:
        return INPUT_SHARE * self.input_per_m + (1 - INPUT_SHARE) * self.output_per_m

    @property
    def multiplier(self) -> float:
        return self.blended_per_m / credit_base_usd_per_m()


_FALLBACK = Price(FALLBACK_PRICE_ID, "Unknown model", 2.0, 0.2, 10.0)
_FREE = Price("own-key", "Own subscription / API key", 0.0, 0.0, 0.0, free=True)

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "rows": None}


def normalize_model_name(name: str | None) -> str:
    """``models/gemini-3.6-flash`` → ``gemini-3.6-flash`` (lower-case)."""
    return (name or "").strip().lower().rsplit("/", 1)[-1]


def invalidate_price_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["rows"] = None


def _load_rows(db: Session | None) -> dict[str, Price]:
    now = time.monotonic()
    with _cache_lock:
        if _cache["rows"] is not None and now - _cache["at"] < _CACHE_TTL_SECONDS:
            return _cache["rows"]

    from pathmind.database.models import ModelPrice

    def _read(session: Session) -> dict[str, Price]:
        rows: dict[str, Price] = {}
        for row in session.query(ModelPrice).all():
            if row.is_active is False:
                continue
            input_price = float(row.input_per_m or 0.0)
            price = Price(
                id=row.id,
                display_name=row.display_name or row.id,
                input_per_m=input_price,
                cached_input_per_m=(
                    float(row.cached_input_per_m)
                    if row.cached_input_per_m is not None
                    else input_price * 0.25
                ),
                output_per_m=float(row.output_per_m or 0.0),
            )
            rows[normalize_model_name(row.id) if row.id != FALLBACK_PRICE_ID else row.id] = price
            for alias in row.aliases or []:
                rows.setdefault(normalize_model_name(alias), price)
        return rows

    try:
        if db is not None:
            rows = _read(db)
        else:
            from pathmind.database.connection import get_db_session

            with get_db_session() as session:
                rows = _read(session)
    except Exception as exc:  # DB not ready: never break an LLM call over pricing
        logger.warning("Model price list unavailable: %s", exc)
        rows = {}
    with _cache_lock:
        _cache["rows"] = rows
        _cache["at"] = now
    return rows


def price_for(model: str | None, provider: str | None = None, db: Session | None = None) -> Price:
    """Resolve the price of a model name as reported by the LLM layer."""
    if "codex" in (provider or "").lower():
        # Owner-bound OAuth login (the user's own ChatGPT subscription):
        # the deployment pays nothing for these calls.
        return _FREE
    rows = _load_rows(db)
    name = normalize_model_name(model)
    if name and name in rows:
        return rows[name]
    if name:
        best = ""
        for key in rows:
            if key != FALLBACK_PRICE_ID and name.startswith(key) and len(key) > len(best):
                best = key
        if best:
            return rows[best]
    return rows.get(FALLBACK_PRICE_ID, _FALLBACK)


def call_cost_usd(price: Price, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    cached = max(0, min(int(cached_tokens or 0), int(prompt_tokens or 0)))
    fresh = max(0, int(prompt_tokens or 0) - cached)
    return (
        fresh * price.input_per_m
        + cached * price.cached_input_per_m
        + max(0, int(completion_tokens or 0)) * price.output_per_m
    ) / 1_000_000


def usd_to_credits(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, int(round(cost_usd * 1_000_000 / credit_base_usd_per_m())))


def credits_to_usd(credits: int | float) -> float:
    return float(credits or 0) * credit_base_usd_per_m() / 1_000_000


# Rough credit budget of one turn per capability, in *base* tokens (before the
# model multiplier). Used only for the pre-turn check so that an expensive job
# is refused up-front instead of overshooting the quota by a large margin.
CAPABILITY_TOKEN_ESTIMATE: dict[str, int] = {
    "chat": 4_000,
    "mastery_path": 6_000,
    "deep_question": 20_000,
    "visualize": 15_000,
    "deep_solve": 30_000,
    "math_animator": 40_000,
    "deep_research": 60_000,
}


def estimate_turn_credits(capability: str | None, model: str | None, db: Session | None = None) -> int:
    base = CAPABILITY_TOKEN_ESTIMATE.get(capability or "chat", 4_000)
    return int(base * price_for(model, db=db).multiplier)
