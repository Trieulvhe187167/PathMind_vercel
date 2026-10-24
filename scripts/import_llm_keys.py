"""Bring LLM providers and their API keys from an older install into this one.

Usage (from the project root):

    python scripts/import_llm_keys.py --catalog path/to/old/model_catalog.json
    python scripts/import_llm_keys.py --rewrite-db          # the backend/ rewrite's providers

``--catalog`` reads a model catalog of an earlier PathMind/DeepTutor install
(plain or encrypted with this install's ``PATHMIND_SECRETS_KEY``).
``--rewrite-db`` reads ``public.ai_providers`` / ``public.ai_models`` from
``DATABASE_URL`` (the separate backend/ rewrite) and decrypts the keys with
``data/secret.key`` (or ``PATHMIND_SECRETS_KEY`` as that app used it).

Connections and profiles are merged by id, so running it twice adds nothing.
The catalog is written through ``ModelCatalogService``, which encrypts every
secret on disk. Keys are never printed.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _merge(current: dict[str, Any], old: dict[str, Any], *, activate: bool) -> list[str]:
    """Add the old connections and service profiles that are not there yet."""
    added: list[str] = []
    have = {c.get("id") for c in current.setdefault("connections", [])}
    for conn in old.get("connections") or []:
        if conn.get("id") not in have:
            current["connections"].append(copy.deepcopy(conn))
            added.append(f"connection {conn.get('name')} ({conn.get('provider')})")
    for name, service in (old.get("services") or {}).items():
        target = current.setdefault("services", {}).setdefault(name, {"profiles": []})
        ids = {p.get("id") for p in target.setdefault("profiles", [])}
        for profile in service.get("profiles") or []:
            if profile.get("id") not in ids:
                target["profiles"].append(copy.deepcopy(profile))
                models = ", ".join(m.get("model") or m.get("name") or "" for m in profile.get("models") or [])
                added.append(f"{name} profile {profile.get('name')}: {models}")
        if activate and service.get("active_profile_id") and not target.get("active_profile_id"):
            target["active_profile_id"] = service.get("active_profile_id")
            if "active_model_id" in service:
                target["active_model_id"] = service.get("active_model_id")
    return added


def _decrypt_rewrite(token: str, raw_key: str) -> str:
    """The backend/ rewrite's ``enc:v1`` format (HMAC keystream, encrypt-then-MAC)."""
    master = hashlib.sha256(("pathmind-secretbox:" + raw_key).encode("utf-8")).digest()
    enc = hmac.new(master, b"enc", hashlib.sha256).digest()
    mac = hmac.new(master, b"mac", hashlib.sha256).digest()
    raw = base64.urlsafe_b64decode(token[len("enc:v1:"):].encode("ascii"))
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + ct, hashlib.sha256).digest()):
        raise ValueError("wrong key for the rewrite's secrets")
    stream = bytearray()
    counter = 0
    while len(stream) < len(ct):
        stream += hmac.new(enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")


def _rewrite_catalog(database_url: str) -> dict[str, Any]:
    """Turn the rewrite's providers into catalog connections + LLM profiles (OpenAI-compatible)."""
    from sqlalchemy import create_engine, text

    raw_key = os.getenv("PATHMIND_SECRETS_KEY", "").strip()
    if not raw_key:
        key_file = ROOT / "data" / "secret.key"
        if not key_file.exists():
            raise SystemExit("data/secret.key (the rewrite's key) was not found.")
        raw_key = key_file.read_text(encoding="utf-8").strip()
    engine = create_engine(database_url)
    out: dict[str, Any] = {"connections": [], "services": {"llm": {"profiles": []}}}
    with engine.connect() as conn:
        providers = conn.execute(text(
            "select id, name, kind, base_url, api_key_encrypted from public.ai_providers where is_active"
        )).mappings().all()
        models = conn.execute(text(
            "select provider_id, model_name, display_name, is_default from public.ai_models "
            "where is_active and purpose = 'chat'"
        )).mappings().all()
    for p in providers:
        key = _decrypt_rewrite(p["api_key_encrypted"], raw_key) if p["api_key_encrypted"] else ""
        cid, pid = f"conn-rewrite-{p['id']}", f"llm-profile-rewrite-{p['id']}"
        binding = "custom" if p["base_url"] else str(p["kind"] or "openai")
        ref = {"connection_id": cid, "binding": binding, "default_base_url": p["base_url"] or ""}
        out["connections"].append({
            "id": cid, "name": p["name"], "provider": binding, "source_service": "llm",
            "api_key": key, "base_url": p["base_url"] or "", "api_version": "", "api_format": "auto",
            "extra_headers": {},
        })
        mine = [m for m in models if m["provider_id"] == p["id"]]
        out["services"]["llm"]["profiles"].append({
            "id": pid, "name": p["name"], "binding": binding, "provider_ref": ref,
            "base_url": p["base_url"] or "", "api_version": "", "extra_headers": {},
            "api_format": "auto", "wire_api": "auto",
            "models": [
                {"id": f"llm-model-rewrite-{p['id']}-{i}", "model": m["model_name"],
                 "name": m["display_name"] or m["model_name"], "provider_ref": ref}
                for i, m in enumerate(mine)
            ],
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", help="an older model_catalog.json to take providers and keys from")
    parser.add_argument("--rewrite-db", action="store_true", help="also take the backend/ rewrite's providers")
    parser.add_argument("--no-activate", action="store_true", help="keep the current active model")
    args = parser.parse_args()
    if not args.catalog and not args.rewrite_db:
        parser.error("give --catalog and/or --rewrite-db")

    from pathmind.services.config.model_catalog import ModelCatalogService
    from pathmind.services.path_service import get_path_service

    service = ModelCatalogService.get_instance(get_path_service().get_settings_file("model_catalog"))
    catalog = service.load()
    added: list[str] = []
    if args.catalog:
        old = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        added += _merge(catalog, old, activate=not args.no_activate)
    if args.rewrite_db:
        url = os.getenv("DATABASE_URL", "")
        if not url.startswith("postgresql"):
            parser.error("--rewrite-db needs DATABASE_URL pointing at the rewrite's PostgreSQL database")
        added += _merge(catalog, _rewrite_catalog(url), activate=False)
    service.save(catalog)
    for line in added:
        print("added", line)
    print("nothing new" if not added else f"{len(added)} item(s) added; keys are stored encrypted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
