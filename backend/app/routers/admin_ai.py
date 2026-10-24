"""Administrators: AI providers, models, model prices and MCP tool servers. Plus the user-facing model list."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..deps import current_user, get_db, require_admin
from ..models import AIModel, AIProvider, MCPServer, ModelPrice, User
from ..services import audit, llm, secretbox
from .auth import _client_ip

router = APIRouter()  # /api/admin/ai
public_router = APIRouter()  # /api/ai


def provider_public(p: AIProvider) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "base_url": p.base_url,
            "api_key_set": bool(p.api_key_encrypted), "is_active": p.is_active,
            "headers": list(((p.extra or {}).get("headers") or {}).keys())}


def model_public(m: AIModel) -> dict:
    return {"id": m.id, "provider_id": m.provider_id, "model_name": m.model_name, "display_name": m.display_name,
            "purpose": m.purpose, "context_window": m.context_window, "is_default": m.is_default,
            "is_active": m.is_active}


class ProviderBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = None  # None = keep, "" = clear
    is_active: bool = True


def _apply_provider(p: AIProvider, body: ProviderBody) -> None:
    if body.kind not in llm.KINDS:
        raise HTTPException(status_code=422, detail=f"Kind must be one of: {', '.join(llm.KINDS)}.")
    url = body.base_url.strip().rstrip("/")
    if body.kind == "openai_compatible" and not url:
        raise HTTPException(status_code=422, detail="An OpenAI-compatible provider needs its base URL.")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Base URL must start with http:// or https://")
    p.name, p.kind, p.base_url, p.is_active = body.name.strip(), body.kind, url, body.is_active
    if body.api_key is not None:
        p.api_key_encrypted = secretbox.encrypt(body.api_key.strip()) if body.api_key.strip() else ""


@router.get("/providers")
def list_providers(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    ps = db.scalars(select(AIProvider).order_by(AIProvider.created_at)).all()
    ms = db.scalars(select(AIModel).order_by(AIModel.created_at)).all()
    return {"providers": [provider_public(p) for p in ps], "models": [model_public(m) for m in ms], "kinds": list(llm.KINDS)}


@router.post("/providers", status_code=201)
def create_provider(body: ProviderBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.scalar(select(AIProvider.id).where(AIProvider.name == body.name.strip())):
        raise HTTPException(status_code=409, detail="A provider with this name exists.")
    p = AIProvider()
    _apply_provider(p, body)
    db.add(p)
    db.flush()
    audit.record(db, admin.id, "ai.provider_create", target_type="ai_provider", target_id=p.id,
                 data={"name": p.name, "kind": p.kind}, ip=_client_ip(request))
    db.commit()
    return {"provider": provider_public(p)}


@router.put("/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderBody, request: Request, db: Session = Depends(get_db),
                    admin: User = Depends(require_admin)):
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider not found.")
    old_url = p.base_url
    _apply_provider(p, body)
    # A key must never follow the provider to a different server: require it again when the host changes.
    from urllib.parse import urlsplit

    if body.api_key is None and urlsplit(old_url).netloc != urlsplit(p.base_url).netloc and p.api_key_encrypted:
        p.api_key_encrypted = ""
    audit.record(db, admin.id, "ai.provider_update", target_type="ai_provider", target_id=p.id,
                 data={"name": p.name}, ip=_client_ip(request))
    db.commit()
    return {"provider": provider_public(p)}


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider not found.")
    audit.record(db, admin.id, "ai.provider_delete", target_type="ai_provider", target_id=p.id,
                 data={"name": p.name}, ip=_client_ip(request))
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    p = db.get(AIProvider, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider not found.")
    try:
        return {"ok": True, "message": llm.test_provider(p)}
    except Exception as exc:  # noqa: BLE001 - shown to the admin
        return {"ok": False, "message": str(exc)[:500]}


class ModelBody(BaseModel):
    provider_id: str
    model_name: str = Field(min_length=1, max_length=120)
    display_name: str = Field(default="", max_length=120)
    purpose: str = "chat"
    context_window: int | None = Field(default=None, ge=1)
    is_active: bool = True


def _apply_model(m: AIModel, body: ModelBody, db: Session) -> None:
    if body.purpose not in ("chat", "embedding"):
        raise HTTPException(status_code=422, detail="Purpose must be chat or embedding.")
    if db.get(AIProvider, body.provider_id) is None:
        raise HTTPException(status_code=422, detail="Unknown provider.")
    m.provider_id, m.model_name, m.display_name = body.provider_id, body.model_name.strip(), body.display_name.strip()
    m.purpose, m.context_window, m.is_active = body.purpose, body.context_window, body.is_active


@router.post("/models", status_code=201)
def create_model(body: ModelBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.scalar(select(AIModel.id).where(AIModel.provider_id == body.provider_id, AIModel.model_name == body.model_name.strip())):
        raise HTTPException(status_code=409, detail="This model is already listed for that provider.")
    m = AIModel()
    _apply_model(m, body, db)
    # The first model of a purpose becomes the default.
    m.is_default = not db.scalar(select(AIModel.id).where(AIModel.purpose == m.purpose, AIModel.is_default.is_(True)))
    db.add(m)
    db.flush()
    audit.record(db, admin.id, "ai.model_create", target_type="ai_model", target_id=m.id,
                 data={"model": m.model_name, "purpose": m.purpose}, ip=_client_ip(request))
    db.commit()
    return {"model": model_public(m)}


@router.put("/models/{model_id}")
def update_model(model_id: str, body: ModelBody, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    m = db.get(AIModel, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found.")
    _apply_model(m, body, db)
    db.commit()
    return {"model": model_public(m)}


@router.post("/models/{model_id}/default")
def make_default(model_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    m = db.get(AIModel, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found.")
    db.execute(update(AIModel).where(AIModel.purpose == m.purpose).values(is_default=False))
    m.is_default, m.is_active = True, True
    audit.record(db, admin.id, "ai.model_default", target_type="ai_model", target_id=m.id,
                 data={"model": m.model_name, "purpose": m.purpose}, ip=_client_ip(request))
    db.commit()
    return {"model": model_public(m)}


@router.delete("/models/{model_id}")
def delete_model(model_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    m = db.get(AIModel, model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found.")
    db.delete(m)
    db.commit()
    return {"ok": True}


class TryBody(BaseModel):
    model_id: str
    prompt: str = Field(default="Say hello in one short sentence.", max_length=2000)


@router.post("/try")
def try_model(body: TryBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    m = db.get(AIModel, body.model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found.")
    p = db.get(AIProvider, m.provider_id)
    try:
        if m.purpose == "embedding":
            vec = llm.embed(db, [body.prompt], user=admin)
            db.commit()
            return {"ok": True, "message": f"Embedding with {len(vec[0][0]) if vec else 0} dimensions."}
        text = llm.complete(db, user=admin, messages=[{"role": "user", "content": body.prompt}],
                            target=llm.Target(p, m), purpose="admin_test", max_tokens=200)
        db.commit()
        return {"ok": True, "message": text[:2000]}
    except Exception as exc:  # noqa: BLE001
        db.commit()
        return {"ok": False, "message": str(exc)[:500]}


# ── Prices ────────────────────────────────────────────────────────────────────


def price_public(r: ModelPrice) -> dict:
    return {"model_key": r.model_key, "display_name": r.display_name, "provider": r.provider,
            "input_per_m": float(r.input_per_m), "cached_input_per_m": float(r.cached_input_per_m) if r.cached_input_per_m is not None else None,
            "output_per_m": float(r.output_per_m), "aliases": r.aliases or [], "is_active": r.is_active, "notes": r.notes}


class PriceBody(BaseModel):
    model_key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(default="", max_length=120)
    provider: str = Field(default="", max_length=40)
    input_per_m: float = Field(ge=0)
    cached_input_per_m: float | None = Field(default=None, ge=0)
    output_per_m: float = Field(ge=0)
    aliases: list[str] = []
    is_active: bool = True
    notes: str = Field(default="", max_length=300)


@router.get("/prices")
def list_prices(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"prices": [price_public(r) for r in db.scalars(select(ModelPrice).order_by(ModelPrice.model_key))]}


@router.put("/prices")
def upsert_price(body: PriceBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    key = body.model_key.strip().lower()
    r = db.get(ModelPrice, key)
    if r is None:
        r = ModelPrice(model_key=key)
        db.add(r)
    r.display_name, r.provider = body.display_name.strip(), body.provider.strip()
    r.input_per_m, r.output_per_m = Decimal(str(body.input_per_m)), Decimal(str(body.output_per_m))
    r.cached_input_per_m = Decimal(str(body.cached_input_per_m)) if body.cached_input_per_m is not None else None
    r.aliases = [a.strip().lower() for a in body.aliases if a.strip()][:20]
    r.is_active, r.notes = body.is_active, body.notes
    audit.record(db, admin.id, "ai.price_update", target_type="model_price", target_id=key, ip=_client_ip(request))
    db.commit()
    return {"price": price_public(r)}


@router.delete("/prices/{model_key}")
def delete_price(model_key: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if model_key in ("*", "*embedding"):
        raise HTTPException(status_code=409, detail="The fallback price cannot be deleted.")
    r = db.get(ModelPrice, model_key)
    if r is None:
        raise HTTPException(status_code=404, detail="Price not found.")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ── MCP tool servers (deployment-wide) ───────────────────────────────────────


def mcp_public(s: MCPServer) -> dict:
    return {"id": s.id, "name": s.name, "transport": s.transport, "url": s.url, "command": s.command,
            "secrets_set": bool(s.secrets_encrypted), "is_enabled": s.is_enabled}


class MCPBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    transport: str = "http"
    url: str = Field(default="", max_length=500)
    command: str = Field(default="", max_length=500)
    secrets: str | None = None  # JSON of headers / env; None keeps, "" clears
    is_enabled: bool = True


@router.get("/mcp")
def list_mcp(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(MCPServer).where(MCPServer.owner_id.is_(None)).order_by(MCPServer.name)).all()
    return {"servers": [mcp_public(s) for s in rows]}


@router.post("/mcp", status_code=201)
def save_mcp(body: MCPBody, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if body.transport not in ("http", "sse", "stdio"):
        raise HTTPException(status_code=422, detail="Transport must be http, sse or stdio.")
    if body.transport != "stdio" and not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="An HTTP MCP server needs its URL.")
    s = db.scalar(select(MCPServer).where(MCPServer.owner_id.is_(None), MCPServer.name == body.name.strip()))
    if s is None:
        s = MCPServer(name=body.name.strip())
        db.add(s)
    s.transport, s.url, s.command, s.is_enabled = body.transport, body.url.strip(), body.command.strip(), body.is_enabled
    if body.secrets is not None:
        s.secrets_encrypted = secretbox.encrypt(body.secrets) if body.secrets else ""
    db.commit()
    return {"server": mcp_public(s)}


@router.delete("/mcp/{server_id}")
def delete_mcp(server_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    s = db.get(MCPServer, server_id)
    if s is None or s.owner_id is not None:
        raise HTTPException(status_code=404, detail="Server not found.")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ── For everyone: which chat models can I pick? ─────────────────────────────


@public_router.get("/models")
def my_models(db: Session = Depends(get_db), user: User = Depends(current_user)):
    models = llm.available_models(db, user, "chat")
    default = None
    if models:
        try:
            default = llm.resolve(db, user).model.id
        except HTTPException:
            default = None
    return {"models": models, "default": default}
