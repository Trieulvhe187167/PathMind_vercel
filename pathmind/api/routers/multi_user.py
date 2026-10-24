"""Admin APIs for the optional multi-user layer."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from pathmind.api.routers.auth import require_admin
from pathmind.multi_user.audit import log_admin_action
from pathmind.multi_user.context import get_current_user
from pathmind.multi_user.grants import (
    load_grant,
    normalize_grant,
    save_grant,
    validate_grant,
)
from pathmind.multi_user.identity import (
    get_user_by_id,
    list_user_info,
)
from pathmind.multi_user.model_access import is_owner_bound
from pathmind.multi_user.paths import (
    get_admin_path_service,
    get_path_service_for_scope,
    scope_for_user,
)
from pathmind.reading import ReadingStore
from pathmind.reading.extensions import get_reading_extension_registry
from pathmind.services.config.model_catalog import ModelCatalogService

router = APIRouter()


class GrantPayload(BaseModel):
    grant: dict[str, Any]


class SkillInstallPayload(BaseModel):
    ref: str
    name: str | None = None
    force: bool = False
    allow_unverified: bool = False


def _admin_catalog_summary() -> dict[str, list[dict[str, Any]]]:
    catalog = ModelCatalogService(
        path=get_admin_path_service().get_settings_file("model_catalog")
    ).load()
    out: dict[str, list[dict[str, Any]]] = {"llm": []}
    for service, state in (catalog.get("services") or {}).items():
        if service not in out:
            continue
        for profile in state.get("profiles", []) or []:
            if is_owner_bound(profile):
                # Bound to one person's OAuth identity, so it is not assignable.
                # Listing it here would offer admins a grant the server drops.
                continue
            profile_id = str(profile.get("id") or "")
            models = []
            for model in profile.get("models", []) or []:
                from pathmind.services.config.provider_links import resolve_profile_provider

                try:
                    effective = resolve_profile_provider(catalog, service, profile, model)
                except ValueError:
                    continue
                if is_owner_bound(effective):
                    continue
                models.append(
                    {
                        "model_id": model.get("id", ""),
                        "name": model.get("name") or model.get("model") or model.get("id"),
                        "model": model.get("model", ""),
                    }
                )
            if profile.get("models") and not models:
                continue
            out[service].append(
                {
                    "profile_id": profile_id,
                    "name": profile.get("name") or profile_id,
                    "models": models,
                }
            )
    return out


def _admin_skill_summary() -> list[dict[str, Any]]:
    from pathmind.services.skill.service import get_admin_skill_service

    service = get_admin_skill_service()
    return [item.to_dict() for item in service.list_skills()]


def _admin_partner_summary() -> list[dict[str, Any]]:
    """The partners an admin can hand to someone else.

    Admin-managed partners only — the ones with no owner, or that the admin
    created. A partner someone built for themselves is theirs to share or not;
    listing it here would let an admin lend out a private companion (and its
    soul, which people write personally) by a single click. Identity only: no
    channel wiring or model selection leaks into the assignable summary.
    """
    from pathmind.services.partners import get_partner_manager

    admin_id = get_current_user().id
    return [
        {
            "partner_id": str(item.get("partner_id") or ""),
            "name": item.get("name") or item.get("partner_id") or "",
            "description": item.get("description") or "",
            "emoji": item.get("emoji") or "",
        }
        for item in get_partner_manager().list_partners()
        if str(item.get("owner_id") or "") in ("", admin_id)
    ]


def _reading_root(service: Any) -> Path:
    return service.get_workspace_feature_dir("reading")


def _admin_reading_summary() -> list[dict[str, Any]]:
    store = ReadingStore(_reading_root(get_admin_path_service()))
    return [manifest.to_dict() for manifest in store.list_materials()]


def _stage_assigned_materials(user_id: str, grant: dict[str, Any]) -> None:
    """Copy newly assigned admin books without touching learner-owned state."""
    policy = grant.get("learning_policy")
    reading = policy.get("reading") if isinstance(policy, dict) else None
    if not isinstance(reading, dict):
        return
    material_ids = set(reading.get("material_ids") or [])
    material_ids.discard("*")
    if not material_ids:
        return

    admin_root = _reading_root(get_admin_path_service())
    admin_store = ReadingStore(admin_root)
    user_service = get_path_service_for_scope(scope_for_user(user_id, is_admin=False))
    target_root = _reading_root(user_service)
    target_root.mkdir(parents=True, exist_ok=True)
    for material_id in sorted(material_ids):
        try:
            admin_store.manifest(material_id)
        except Exception as exc:
            raise ValueError(f"Unknown admin reading material: {material_id}") from exc
        target = target_root / material_id
        if target.exists():
            continue
        stage = target_root / f".{material_id}.{uuid.uuid4().hex[:8]}.staging"
        try:
            shutil.copytree(admin_root / material_id, stage)
            os.replace(stage, target)
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def _validate_reading_policy(grant: dict[str, Any]) -> None:
    policy = grant.get("learning_policy")
    reading = policy.get("reading") if isinstance(policy, dict) else None
    if not isinstance(reading, dict):
        return
    allowed_extensions = {
        extension.manifest.id for extension in get_reading_extension_registry().all()
    }
    unknown = sorted(set(reading.get("extensions") or []) - allowed_extensions)
    if unknown:
        raise ValueError(f"Unknown reading extensions: {', '.join(unknown)}")


def _require_assignable_user(user_id: str) -> tuple[str, dict[str, Any]]:
    user_record = get_user_by_id(user_id)
    if user_record is None:
        raise HTTPException(status_code=404, detail="User not found")
    username, record = user_record
    if str(record.get("role") or "user") == "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin users use the main workspace and cannot receive assignments.",
        )
    return username, record


@router.get("/admin/resources")
async def admin_resources(_: object = Depends(require_admin)) -> dict[str, Any]:
    """Everything an admin can assign to a user: models, KBs, skills, and
    the tool surface (system tools + MCP tools, same pool partners use)."""
    from pathmind.api.utils.tool_options import build_tool_options

    tool_options = await build_tool_options()
    return {
        "models": _admin_catalog_summary(),
        "skills": _admin_skill_summary(),
        "partners": _admin_partner_summary(),
        "reading_materials": _admin_reading_summary(),
        "reading_extensions": [
            extension.manifest.model_dump() for extension in get_reading_extension_registry().all()
        ],
        "tools": tool_options["tools"],
        "mcp_tools": tool_options["mcp_tools"],
    }


@router.get("/users/{user_id}/grants")
async def get_user_grants(user_id: str, _: object = Depends(require_admin)) -> dict[str, Any]:
    _require_assignable_user(user_id)
    return {"grant": load_grant(user_id)}


@router.put("/users/{user_id}/grants")
async def put_user_grants(
    user_id: str,
    payload: GrantPayload,
    _: object = Depends(require_admin),
) -> dict[str, Any]:
    user_record = _require_assignable_user(user_id)
    try:
        grant = normalize_grant(user_id, payload.grant)
        if (
            str(user_record[1].get("preset") or "standard") == "learner"
            and grant.get("learning_policy") is None
        ):
            raise ValueError("Learner accounts must retain a learning policy.")
        validate_grant(grant)
        _validate_reading_policy(grant)
        _stage_assigned_materials(user_id, grant)
        grant = save_grant(user_id, grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(
        "grant_set",
        target_user_id=user_id,
        summary={
            "model_count": len(grant.get("models", {}).get("llm", []) or []),
            "skill_count": len(grant.get("skills", []) or []),
            "partner_count": len(grant.get("partners", []) or []),
            "enabled_tools": grant.get("enabled_tools"),
            "mcp_tool_count": (
                None if grant.get("mcp_tools") is None else len(grant.get("mcp_tools") or [])
            ),
            "exec_enabled": grant.get("exec_enabled"),
            "learning_policy": grant.get("learning_policy"),
        },
    )
    return {"grant": grant}


@router.post("/admin/skills/install")
async def admin_install_skill(
    payload: SkillInstallPayload,
    _: object = Depends(require_admin),
) -> dict[str, Any]:
    """Install a hub skill into the admin catalog (``<hub>:<slug>[@version]``).

    The skill lands in the admin workspace — the same pool ``/admin/resources``
    lists — so it stays invisible to non-admin users until a grant assigns it.
    The install pipeline (verdict gate, safe extraction, ``always`` stripping)
    lives in :func:`pathmind.services.skill.hub.install_from_hub`; this
    endpoint only chooses the target root and audits the action.
    """
    from pathmind.services.skill.hub import HubError, install_from_hub
    from pathmind.services.skill.service import (
        InvalidSkillNameError,
        SkillExistsError,
        SkillImportError,
        get_admin_skill_service,
    )

    service = get_admin_skill_service()
    try:
        outcome = await asyncio.to_thread(
            install_from_hub,
            payload.ref,
            service=service,
            rename_to=payload.name,
            force=payload.force,
            allow_unverified=payload.allow_unverified,
        )
    except SkillExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Skill already exists: {exc}") from exc
    except (SkillImportError, InvalidSkillNameError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    log_admin_action(
        "skill_hub_install",
        summary={
            "ref": payload.ref,
            "installed_as": outcome.result.info.name,
            "version": outcome.ref.version,
            "verdict": outcome.verdict.status,
            "forced": payload.force,
            "allow_unverified": payload.allow_unverified,
        },
    )
    return {
        "skill": outcome.result.info.to_dict(),
        "verdict": {"status": outcome.verdict.status, "detail": outcome.verdict.detail},
        "version": outcome.ref.version,
        "skipped": [{"path": rel, "reason": reason} for rel, reason in outcome.result.skipped],
    }


@router.get("/users")
async def multi_user_list_users(_: object = Depends(require_admin)) -> dict[str, Any]:
    return {"users": list_user_info()}
