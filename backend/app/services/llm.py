"""Talking to AI providers: chat (streamed), embeddings, model choice and metering.

Provider kinds: openai, openai_compatible (DeepSeek, OpenRouter, vLLM, LM Studio...), anthropic,
gemini, ollama, and mock (built in, no network - for tests and for trying the app without keys).
Every call is metered into ``llm_calls`` / usage roll-ups and charged to the user's credits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Iterator

from fastapi import HTTPException
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AIModel, AIProvider, User
from . import billing, secretbox

DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "ollama": "http://127.0.0.1:11434",
    "deepseek": "https://api.deepseek.com/v1",
}
KINDS = ("openai", "openai_compatible", "anthropic", "gemini", "ollama", "mock")

# Tests (or demos) can replace how the mock provider answers: fn(messages, purpose) -> text.
MOCK_HANDLER: Callable[[list[dict], str], str] | None = None
HTTP_TRANSPORT: httpx.BaseTransport | None = None  # tests may fake providers
TIMEOUT = 120.0


class LLMError(RuntimeError):
    pass


class NotConfigured(HTTPException):
    def __init__(self, what: str = "chat"):
        super().__init__(status_code=503, detail={
            "code": "ai_not_configured",
            "message": f"No AI {what} model is set up yet. An administrator can add one in AI configuration.",
        })


@dataclass
class Target:
    provider: AIProvider
    model: AIModel

    @property
    def name(self) -> str:
        return self.model.model_name

    @property
    def kind(self) -> str:
        return self.provider.kind


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    cached: int = 0


@dataclass
class StreamState:
    """Filled while streaming; read after the iterator is exhausted."""

    text: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    error: str = ""


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


# ── Choosing a model ─────────────────────────────────────────────────────────


def _active_models(db: Session, purpose: str) -> list[tuple[AIModel, AIProvider]]:
    rows = db.execute(
        select(AIModel, AIProvider).join(AIProvider, AIProvider.id == AIModel.provider_id)
        .where(AIModel.is_active.is_(True), AIProvider.is_active.is_(True), AIModel.purpose == purpose)
        .order_by(AIModel.is_default.desc(), AIModel.created_at)
    ).all()
    return [(m, p) for m, p in rows]


def available_models(db: Session, user: User | None, purpose: str = "chat") -> list[dict]:
    out = []
    for m, p in _active_models(db, purpose):
        if user is not None and purpose == "chat" and not billing.model_allowed(db, user, m.model_name):
            continue
        out.append({"id": m.id, "model": m.model_name, "name": m.display_name or m.model_name,
                    "provider": p.name, "kind": p.kind, "is_default": m.is_default})
    return out


def resolve(db: Session, user: User | None, requested: str | None = None, purpose: str = "chat") -> Target:
    models = _active_models(db, purpose)
    if not models:
        raise NotConfigured("embedding" if purpose == "embedding" else purpose)
    if requested:
        for m, p in models:
            if requested in (m.id, m.model_name):
                if user is not None and purpose == "chat" and not billing.model_allowed(db, user, m.model_name):
                    raise billing.plan_required(db, "model", "Your plan does not include this model.")
                return Target(p, m)
        raise HTTPException(status_code=422, detail="That model is not available.")
    if user is not None and purpose == "chat" and not billing.is_unlimited(user):
        plan, _ = billing.current_plan(db, user)
        if plan is not None and plan.default_model:
            for m, p in models:
                if m.model_name == plan.default_model:
                    return Target(p, m)
        for m, p in models:  # the default if allowed, else the first allowed model
            if billing.model_allowed(db, user, m.model_name):
                return Target(p, m)
        raise billing.plan_required(db, "model", "Your plan does not include any configured model.")
    m, p = models[0]
    return Target(p, m)


def embedding_target(db: Session) -> Target | None:
    models = _active_models(db, "embedding")
    return Target(models[0][1], models[0][0]) if models else None


# ── Provider requests ────────────────────────────────────────────────────────


def _key(p: AIProvider) -> str:
    return secretbox.try_decrypt(p.api_key_encrypted)


def _base(p: AIProvider) -> str:
    return (p.base_url or DEFAULT_URLS.get(p.kind, "")).rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=15.0), transport=HTTP_TRANSPORT)


def _sse_lines(resp: httpx.Response) -> Iterator[dict]:
    for line in resp.iter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code >= 400:
        try:
            body = resp.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # noqa: BLE001
            body = ""
        raise LLMError(f"{provider} returned {resp.status_code}: {body}")


def _mock_reply(messages: list[dict], purpose: str) -> str:
    if MOCK_HANDLER is not None:
        return MOCK_HANDLER(messages, purpose)
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    system = " ".join(m["content"] for m in messages if m["role"] == "system")
    if "Respond with JSON" in system or "Respond with JSON" in last:
        return "{}"
    cite = " [1]" if "[1]" in system else ""
    return f"(mock answer) You asked: {last[:300]}{cite}"


def _stream_raw(t: Target, messages: list[dict], temperature: float, max_tokens: int | None,
                json_mode: bool, state: StreamState, purpose: str) -> Iterator[str]:
    kind, p, model = t.kind, t.provider, t.name
    if kind == "mock":
        text = _mock_reply(messages, purpose)
        for word in re.split(r"(\s+)", text):
            if word:
                yield word
        state.usage = Usage(sum(estimate_tokens(m["content"]) for m in messages), estimate_tokens(text))
        return

    if kind in ("openai", "openai_compatible"):
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True,
                                "temperature": temperature, "stream_options": {"include_usage": True}}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_mode and kind == "openai":
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {_key(p)}"} if _key(p) else {}
        headers.update((p.extra or {}).get("headers", {}) or {})
        with _client() as c, c.stream("POST", _base(p) + "/chat/completions", json=body, headers=headers) as r:
            _raise_for(r, p.name)
            for ev in _sse_lines(r):
                if ev.get("usage"):
                    u = ev["usage"]
                    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or u.get("prompt_cache_hit_tokens", 0)
                    state.usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), cached or 0)
                for ch in ev.get("choices") or []:
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        yield delta
        return

    if kind == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        body = {"model": model, "messages": msgs, "stream": True, "max_tokens": max_tokens or 4096,
                "temperature": temperature}
        if system:
            body["system"] = system
        headers = {"x-api-key": _key(p), "anthropic-version": "2023-06-01"}
        with _client() as c, c.stream("POST", _base(p) + "/messages", json=body, headers=headers) as r:
            _raise_for(r, p.name)
            for ev in _sse_lines(r):
                typ = ev.get("type")
                if typ == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    state.usage.prompt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    state.usage.cached = u.get("cache_read_input_tokens", 0)
                elif typ == "content_block_delta":
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        yield d["text"]
                elif typ == "message_delta":
                    state.usage.completion = (ev.get("usage") or {}).get("output_tokens", state.usage.completion)
        return

    if kind == "gemini":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] in ("user", "assistant")]
        body = {"contents": contents, "generationConfig": {"temperature": temperature}}
        if max_tokens:
            body["generationConfig"]["maxOutputTokens"] = max_tokens
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{_base(p)}/models/{model}:streamGenerateContent?alt=sse"
        with _client() as c, c.stream("POST", url, json=body, headers={"x-goog-api-key": _key(p)}) as r:
            _raise_for(r, p.name)
            for ev in _sse_lines(r):
                u = ev.get("usageMetadata")
                if u:
                    state.usage = Usage(u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0),
                                        u.get("cachedContentTokenCount", 0))
                for cand in ev.get("candidates") or []:
                    for part in (cand.get("content") or {}).get("parts") or []:
                        if part.get("text"):
                            yield part["text"]
        return

    if kind == "ollama":
        body = {"model": model, "messages": messages, "stream": True, "options": {"temperature": temperature}}
        if json_mode:
            body["format"] = "json"
        with _client() as c, c.stream("POST", _base(p) + "/api/chat", json=body) as r:
            _raise_for(r, p.name)
            for line in r.iter_lines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = (ev.get("message") or {}).get("content")
                if piece:
                    yield piece
                if ev.get("done"):
                    state.usage = Usage(ev.get("prompt_eval_count", 0), ev.get("eval_count", 0))
        return

    raise LLMError(f"Unknown provider kind: {kind}")


def stream(db: Session, *, user: User | None, messages: list[dict], model: str | None = None,
           purpose: str = "chat", temperature: float | None = None, max_tokens: int | None = None,
           json_mode: bool = False, chat_session_id: str | None = None, target: Target | None = None,
           state: StreamState | None = None) -> Iterator[str]:
    """Yield text pieces; meters the call when done (caller commits). Raises before the first piece
    when the user has no credits left or no model is configured."""
    t = target or resolve(db, user, model)
    if user is not None:
        billing.check_credits(db, user)
    st = state if state is not None else StreamState()
    temp = 0.4 if temperature is None else temperature
    started = time.monotonic()
    pieces: list[str] = []
    status, err = "ok", ""
    try:
        for piece in _stream_raw(t, messages, temp, max_tokens, json_mode, st, purpose):
            pieces.append(piece)
            yield piece
    except (httpx.HTTPError, LLMError) as exc:
        status, err = "error", str(exc)
        st.error = err
        raise LLMError(f"The AI provider failed: {exc}") from exc
    finally:
        st.text = "".join(pieces)
        st.model = t.name
        if not st.usage.prompt and status == "ok":
            st.usage.prompt = sum(estimate_tokens(m["content"]) for m in messages)
        if not st.usage.completion and status == "ok":
            st.usage.completion = estimate_tokens(st.text)
        billing.record_call(
            db, user_id=user.id if user else None, model=t.name, provider=t.provider.name, purpose=purpose,
            prompt=st.usage.prompt, cached=st.usage.cached, completion=st.usage.completion,
            latency_ms=int((time.monotonic() - started) * 1000), status=status, error=err,
            chat_session_id=chat_session_id,
        )


def complete(db: Session, **kwargs: Any) -> str:
    return "".join(stream(db, **kwargs))


def complete_json(db: Session, *, fallback: Any = None, **kwargs: Any) -> Any:
    """Ask for JSON and parse it (tolerating ```json fences and text around the object)."""
    kwargs.setdefault("json_mode", True)
    text = complete(db, **kwargs)
    return parse_json(text, fallback)


def parse_json(text: str, fallback: Any = None) -> Any:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    for candidate in (t, _outer(t, "{", "}"), _outer(t, "[", "]")):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return fallback


def _outer(t: str, a: str, b: str) -> str:
    i, j = t.find(a), t.rfind(b)
    return t[i: j + 1] if 0 <= i < j else ""


# ── Embeddings ──────────────────────────────────────────────────────────────


def _mock_vector(text: str, dims: int = 64) -> list[float]:
    v = [0.0] * dims
    for word in re.findall(r"\w+", text.lower()):
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        v[h % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def embed(db: Session, texts: list[str], *, user: User | None = None, purpose: str = "embedding") -> tuple[list[list[float]], str] | None:
    """Vectors for ``texts`` with the default embedding model, or None when none is configured."""
    t = embedding_target(db)
    if t is None or not texts:
        return None
    p, model = t.provider, t.name
    started = time.monotonic()
    tokens = sum(estimate_tokens(x) for x in texts)
    vectors: list[list[float]] = []
    try:
        if p.kind == "mock":
            vectors = [_mock_vector(x) for x in texts]
        elif p.kind in ("openai", "openai_compatible"):
            with _client() as c:
                for i in range(0, len(texts), 64):
                    r = c.post(_base(p) + "/embeddings", json={"model": model, "input": texts[i:i + 64]},
                               headers={"Authorization": f"Bearer {_key(p)}"})
                    _raise_for(r, p.name)
                    js = r.json()
                    vectors += [d["embedding"] for d in sorted(js["data"], key=lambda d: d.get("index", 0))]
                    tokens = (js.get("usage") or {}).get("prompt_tokens", tokens)
        elif p.kind == "gemini":
            with _client() as c:
                for i in range(0, len(texts), 64):
                    reqs = [{"model": f"models/{model}", "content": {"parts": [{"text": x}]}} for x in texts[i:i + 64]]
                    r = c.post(f"{_base(p)}/models/{model}:batchEmbedContents", json={"requests": reqs},
                               headers={"x-goog-api-key": _key(p)})
                    _raise_for(r, p.name)
                    vectors += [e["values"] for e in r.json()["embeddings"]]
        elif p.kind == "ollama":
            with _client() as c:
                r = c.post(_base(p) + "/api/embed", json={"model": model, "input": texts})
                _raise_for(r, p.name)
                vectors = r.json()["embeddings"]
        else:
            raise LLMError(f"{p.kind} has no embedding API.")
    except (httpx.HTTPError, LLMError, KeyError, ValueError) as exc:
        billing.record_call(db, user_id=user.id if user else None, model=model, provider=p.name, purpose=purpose,
                            prompt=0, cached=0, completion=0, status="error", error=str(exc))
        raise LLMError(f"Embedding failed: {exc}") from exc
    billing.record_call(db, user_id=user.id if user else None, model=model, provider=p.name, purpose=purpose,
                        prompt=tokens, cached=0, completion=0,
                        latency_ms=int((time.monotonic() - started) * 1000))
    return vectors, model


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def test_provider(p: AIProvider, model: str = "") -> str:
    """Cheap connectivity check used by the admin page."""
    if p.kind == "mock":
        return "Mock provider is always available."
    with _client() as c:
        if p.kind in ("openai", "openai_compatible"):
            r = c.get(_base(p) + "/models", headers={"Authorization": f"Bearer {_key(p)}"})
        elif p.kind == "anthropic":
            r = c.get(_base(p) + "/models", headers={"x-api-key": _key(p), "anthropic-version": "2023-06-01"})
        elif p.kind == "gemini":
            r = c.get(_base(p) + "/models", headers={"x-goog-api-key": _key(p)})
        elif p.kind == "ollama":
            r = c.get(_base(p) + "/api/tags")
        else:
            raise LLMError("Unknown provider kind.")
    if r.status_code >= 400:
        raise LLMError(f"{r.status_code}: {r.text[:300]}")
    try:
        js = r.json()
        names = [m.get("id") or m.get("name") for m in (js.get("data") or js.get("models") or [])]
        return f"Connected. {len(names)} models visible." + (f" e.g. {', '.join(str(n) for n in names[:5])}" if names else "")
    except ValueError:
        return "Connected."
