"""Seed files: drop one JSON file into ``data/seed/`` and the install is ready to use.

On start-up every ``data/seed/*.json`` that was not applied yet is loaded
(``*.example.json`` is skipped). A seed can hold:

    accounts       [{username, email, full_name, role, password?, plan?, plan_days?}]
                   role: admin | rnd | educator | user. Without a password one is
                   generated and written to ``data/seed/seed-accounts.txt``.
    schema         {labels: [{name, color?, description?}], relations: [{name, description?}]}
    knowledge      [{domain, markdown}]  each a knowledge-graph domain written as a
                   document (headings + @id/@label/@rel... lines, see the Samples page)
    llm_providers  [{name, binding, base_url?, api_key, models: [..], default?}]
                   binding: openai | anthropic | gemini | deepseek | custom (OpenAI-compatible)
    reward_rules   {"earn.node_mastered": 2, ...}

Everything is idempotent: existing usernames, domains (by name), labels,
relation types and providers (by name) are left alone, so a file can be
re-applied after editing. Applied files are remembered by content hash in
``data/seed/.applied.json``. Secrets in the file (passwords, API keys) are
stored hashed/encrypted like any other; keep seed files with keys out of git
(``data/`` is ignored).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import secrets
from typing import Any

logger = logging.getLogger(__name__)

ROLES = ("admin", "rnd", "educator", "user")


def seed_dir() -> Path:
    from pathmind.multi_user.paths import ADMIN_WORKSPACE_ROOT

    return ADMIN_WORKSPACE_ROOT / "seed"


def _applied_file() -> Path:
    return seed_dir() / ".applied.json"


def _applied() -> dict[str, str]:
    try:
        return json.loads(_applied_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(name: str, digest: str) -> None:
    data = _applied()
    data[name] = digest
    _applied_file().parent.mkdir(parents=True, exist_ok=True)
    _applied_file().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_credentials(lines: list[str]) -> None:
    if not lines:
        return
    path = seed_dir() / "seed-accounts.txt"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.warning("Seed accounts created; their passwords are in %s", path)


# ── parts ───────────────────────────────────────────────────────────────────


def _accounts(items: list[dict], report: dict[str, Any]) -> str:
    """Create missing accounts; returns the id of an R&D (or admin) account for authoring."""
    from pathmind.database.connection import get_db_session
    from pathmind.database.models import SubscriptionPlan
    from pathmind.multi_user.identity import find_user_by_email
    from pathmind.services import quota_service
    from pathmind.services.auth import add_user, get_user_info

    created: list[str] = []
    author, author_is_rnd = "", False
    for item in items:
        username = str(item.get("username") or "").strip()
        role = str(item.get("role") or "user").strip()
        if not username or role not in ROLES:
            report["warnings"].append(f"account skipped: {username or '?'} (role {role})")
            continue
        info = get_user_info(username)
        email = str(item.get("email") or "").strip().lower()
        if info is None and email and find_user_by_email(email) is not None:
            report["warnings"].append(f"account skipped: {email} already belongs to another account")
            continue
        if info is None:
            password = str(item.get("password") or "") or secrets.token_urlsafe(12)
            add_user(username, password, role=role, profile={
                "full_name": item.get("full_name") or username, "email": email, "email_verified": True,
            })
            info = get_user_info(username)
            report["accounts"].append(username)
            if not item.get("password"):
                created.append(f"{username}\t{role}\t{password}")
        if info is None:
            continue
        user_id = str(info.get("id") or "")
        role = str(info.get("role") or role)
        # Knowledge is authored as R&D; an admin stands in when the seed has no R&D account.
        if role == "rnd" and not author_is_rnd:
            author, author_is_rnd = user_id, True
        elif role == "admin" and not author:
            author = user_id
        with get_db_session() as db:
            quota_service.ensure_db_user(db, user_id, username, role)
            plan_id = str(item.get("plan") or "").strip()
            if plan_id:
                plan = db.get(SubscriptionPlan, plan_id)
                current = quota_service.get_active_subscription(user_id, db)
                if plan is None:
                    report["warnings"].append(f"{username}: unknown plan {plan_id}")
                elif current is None or current.plan_id != plan_id:
                    quota_service.activate_subscription(
                        db, user_id, plan, source="admin", duration_days=int(item.get("plan_days") or 0)
                    )
            db.commit()
    _write_credentials(created)
    return author


def _schema(data: dict, author: str, report: dict[str, Any]) -> None:
    from pathmind.database.connection import get_db_session
    from pathmind.services.knowledge_tree import TreeError, graph

    with get_db_session() as db:
        have = graph.schema(db)
        labels = {x["name"].lower() for x in have["labels"]}
        rels = {x["name"] for x in have["relations"]}
        for x in data.get("labels") or []:
            if str(x.get("name") or "").lower() in labels:
                continue
            try:
                graph.save_label(db, "rnd", author, label_id=None, name=x.get("name", ""),
                                 color=x.get("color", ""), description=x.get("description", ""))
                report["labels"].append(x["name"])
            except TreeError as exc:
                report["warnings"].append(f"label {x.get('name')}: {exc.message}")
        for x in data.get("relations") or []:
            name = str(x.get("name") or "").upper().replace(" ", "_")
            if name in rels:
                continue
            try:
                graph.save_relation(db, "rnd", author, relation_id=None, name=name, description=x.get("description", ""))
                report["relations"].append(name)
            except TreeError as exc:
                report["warnings"].append(f"relation {name}: {exc.message}")


def _knowledge(items: list[dict], author: str, report: dict[str, Any]) -> None:
    from pathmind.database.connection import get_db_session
    from pathmind.database.models import KnowledgeDomain
    from pathmind.services.knowledge_tree import TreeError, document_sync

    with get_db_session() as db:
        for item in items:
            name = str(item.get("domain") or "").strip()
            markdown = str(item.get("markdown") or "")
            if not name or not markdown.strip():
                continue
            if db.query(KnowledgeDomain.id).filter(KnowledgeDomain.name == name).first():
                continue  # already there: R&D owns it from now on
            try:
                out = document_sync.sync_markdown(db, "rnd", author, domain_id=None, new_domain_name=name,
                                                  title=name, markdown=markdown)
                report["domains"].append(f"{name} ({out['created']} nodes)")
                report["warnings"] += [f"{name}: {w}" for w in out.get("warnings") or []]
            except TreeError as exc:
                report["warnings"].append(f"domain {name}: {exc.message}")


def _llm(items: list[dict], report: dict[str, Any]) -> None:
    from pathmind.services.config.model_catalog import ModelCatalogService
    from pathmind.services.path_service import get_path_service

    service = ModelCatalogService.get_instance(get_path_service().get_settings_file("model_catalog"))
    catalog = service.load()
    llm = catalog.setdefault("services", {}).setdefault("llm", {"profiles": []})
    names = {p.get("name") for p in llm.setdefault("profiles", [])}
    changed = False
    for item in items:
        name = str(item.get("name") or "").strip()
        models = [str(m) for m in item.get("models") or [] if str(m).strip()]
        if not name or not models or name in names:
            continue
        slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
        binding = str(item.get("binding") or "openai").strip()
        base_url = str(item.get("base_url") or "").strip()
        cid, pid = f"conn-seed-{slug}", f"llm-profile-seed-{slug}"
        ref = {"connection_id": cid, "binding": binding, "default_base_url": base_url}
        catalog.setdefault("connections", []).append({
            "id": cid, "name": name, "provider": binding, "source_service": "llm",
            "api_key": str(item.get("api_key") or ""), "base_url": base_url, "api_version": "",
            "api_format": "auto", "extra_headers": {},
        })
        profile = {
            "id": pid, "name": name, "binding": binding, "provider_ref": ref, "base_url": base_url,
            "api_version": "", "extra_headers": {}, "api_format": "auto", "wire_api": "auto",
            "models": [{"id": f"llm-model-seed-{slug}-{i}", "model": m, "name": m, "provider_ref": ref}
                       for i, m in enumerate(models)],
        }
        llm["profiles"].append(profile)
        if item.get("default") or not llm.get("active_profile_id"):
            llm["active_profile_id"], llm["active_model_id"] = pid, profile["models"][0]["id"]
        names.add(name)
        report["llm_providers"].append(name)
        changed = True
    if changed:
        service.save(catalog)


def _reward_rules(values: dict, report: dict[str, Any]) -> None:
    from pathmind.database.connection import get_db_session
    from pathmind.services import rewards

    with get_db_session() as db:
        try:
            rewards.set_rules(db, "seed", values)
            report["reward_rules"] = sorted(values)
        except rewards.RewardError as exc:
            report["warnings"].append(f"reward rules: {exc.message}")


# ── entry points ────────────────────────────────────────────────────────────


def apply_seed(data: dict[str, Any]) -> dict[str, Any]:
    """Apply one seed document (idempotent). Returns what was added."""
    report: dict[str, Any] = {"accounts": [], "labels": [], "relations": [], "domains": [],
                              "llm_providers": [], "warnings": []}
    author = _accounts(list(data.get("accounts") or []), report) or "seed"
    if data.get("schema"):
        _schema(dict(data["schema"]), author, report)
    if data.get("knowledge"):
        _knowledge(list(data["knowledge"]), author, report)
    if data.get("llm_providers"):
        _llm(list(data["llm_providers"]), report)
    if data.get("reward_rules"):
        _reward_rules(dict(data["reward_rules"]), report)
    return report


def apply_file(path: Path, *, force: bool = False) -> dict[str, Any] | None:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not force and _applied().get(path.name) == digest:
        return None
    report = apply_seed(json.loads(raw.decode("utf-8")))
    _remember(path.name, digest)
    return report


def apply_pending() -> None:
    """Start-up hook: load new or changed ``data/seed/*.json``. Never blocks start-up."""
    folder = seed_dir()
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(".example.json") or path.name.startswith("."):
            continue
        try:
            report = apply_file(path)
        except Exception as exc:  # noqa: BLE001 - a bad seed must not stop the server
            logger.warning("Seed %s failed: %s", path.name, exc)
            continue
        if report is not None:
            summary = {k: v for k, v in report.items() if v}
            # json.dumps keeps the log line ASCII (Windows consoles choke on Vietnamese).
            logger.info("Seed %s applied: %s", path.name, json.dumps(summary))
