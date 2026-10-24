"""Server-side model grant resolution and redacted model views.

Grants carry LLM assignments only (grant v2): embedding and search always
resolve from the deployment's active profiles, so per-user grants for them
were never enforced and are not stored.

Three sources reach an ordinary user, and :func:`redacted_model_access` is
the one place they are resolved: ``plan`` models included in their
subscription plan, ``admin`` models assigned through a grant, and the
``personal`` owner-bound profiles the user signed in for themselves (see
:mod:`pathmind.multi_user.personal_models`). Everything downstream — the
option list, the capability gate, and selection validation — reads that one
function, so the three can never disagree about what a user may use.
"""

from __future__ import annotations

from typing import Any

from pathmind.services.config.model_catalog import ModelCatalogService
from pathmind.services.config.provider_links import resolve_profile_provider
from pathmind.services.model_selection import list_llm_options

from .context import get_current_user
from .grants import load_grant
from .paths import get_admin_path_service


def admin_catalog_service() -> ModelCatalogService:
    return ModelCatalogService(path=get_admin_path_service().get_settings_file("model_catalog"))


def admin_catalog() -> dict[str, Any]:
    return admin_catalog_service().load()


def _profile_by_id(catalog: dict[str, Any], service: str, profile_id: str) -> dict[str, Any] | None:
    for profile in catalog.get("services", {}).get(service, {}).get("profiles", []) or []:
        if str(profile.get("id") or "") == profile_id:
            return profile
    return None


def _model_by_id(profile: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    for model in profile.get("models", []) or []:
        if str(model.get("id") or "") == model_id:
            return model
    return None


#: Bindings whose credential is one person's own subscription login rather than
#: a billable team key. Codex stamps ``owner_bound`` onto the managed profile it
#: publishes, but a profile can also be created by hand in the settings editor —
#: a CodeBuddy profile is, and it reads the operator's own IDE-plugin session —
#: and there is nowhere for such a profile to acquire the flag. Binding is the
#: durable fact, so it decides too.
OWNER_BOUND_BINDINGS = frozenset({"openai_codex", "codebuddy"})


def is_owner_bound(profile: dict[str, Any]) -> bool:
    """Whether a profile is tied to the identity of the operator who set it up.

    OAuth providers such as Codex authenticate one individual's plan rather than
    a billable team key, so those profiles are never lent to other accounts
    through grants — each user signs in for themselves or goes without.
    """
    binding = str(profile.get("binding") or "").strip().lower()
    if binding in OWNER_BOUND_BINDINGS:
        return True
    return bool(profile.get("owner_bound"))


def _plan_llm_rows(user_id: str, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Deployment models the user's subscription plan includes.

    A paid plan is itself an entitlement: assigning or buying one must unlock
    the catalog models its ``allowed_models`` list names, without an admin
    also hand-editing a per-user grant. Owner-bound profiles stay excluded.
    """
    if not user_id:
        return []
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.services.quota_service import get_user_plan, plan_allows_model

        with get_db_session() as db:
            plan = get_user_plan(user_id, db)
            rows: list[dict[str, Any]] = []
            for profile in catalog.get("services", {}).get("llm", {}).get("profiles", []) or []:
                profile_id = str(profile.get("id") or "")
                if not profile_id or is_owner_bound(profile):
                    continue
                for model in profile.get("models", []) or []:
                    model_id = str(model.get("id") or "")
                    model_name = str(model.get("model") or model.get("name") or "")
                    if not model_id or not model_name or not plan_allows_model(plan, model_name, db):
                        continue
                    try:
                        effective = resolve_profile_provider(catalog, "llm", profile, model)
                    except ValueError:
                        continue
                    if is_owner_bound(effective):
                        continue
                    rows.append(
                        {
                            "profile_id": profile_id,
                            "model_id": model_id,
                            "name": model.get("name") or model_id,
                            "model": model.get("model") or "",
                            "provider": effective.get("binding") or "",
                            "profile_name": effective.get("name") or profile_id,
                            "reasoning_effort": model.get("reasoning_effort"),
                            "supported_reasoning_efforts": model.get(
                                "codex_supported_reasoning_levels"
                            ),
                            "source": "plan",
                            "available": True,
                        }
                    )
            return rows
    except Exception:  # pragma: no cover - billing DB optional
        return []


def redacted_model_access(user_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    user = get_current_user()
    if user_id is None:
        user_id = user.id
    grant = load_grant(user_id)
    catalog = admin_catalog()
    result: dict[str, list[dict[str, Any]]] = {"llm": []}
    for item in grant.get("models", {}).get("llm", []) or []:
        profile_id = str(item.get("profile_id") or item.get("id") or "")
        profile = _profile_by_id(catalog, "llm", profile_id)
        if profile is not None and is_owner_bound(profile):
            # A grant may predate the profile becoming owner-bound. Drop it here,
            # the one place every caller resolves grants through, so the option
            # list, the capability gate, and selection validation all agree.
            continue
        if not profile:
            result["llm"].append(
                {
                    "profile_id": profile_id,
                    "name": item.get("name") or profile_id or "Unavailable profile",
                    "source": "admin",
                    "available": False,
                }
            )
            continue
        for model_id in item.get("model_ids") or []:
            model = _model_by_id(profile, str(model_id))
            try:
                effective = resolve_profile_provider(catalog, "llm", profile, model)
            except ValueError:
                continue
            if is_owner_bound(effective):
                continue
            result["llm"].append(
                {
                    "profile_id": profile_id,
                    "model_id": str(model_id),
                    "name": (model or {}).get("name") or str(model_id),
                    "model": (model or {}).get("model") or "",
                    "provider": effective.get("binding") or "",
                    "profile_name": effective.get("name") or profile_id,
                    "reasoning_effort": (model or {}).get("reasoning_effort"),
                    "supported_reasoning_efforts": (model or {}).get(
                        "codex_supported_reasoning_levels"
                    ),
                    "source": "admin",
                    "available": model is not None,
                }
            )
    seen = {(row.get("profile_id"), row.get("model_id")) for row in result["llm"]}
    for row in _plan_llm_rows(user_id, catalog):
        key = (row["profile_id"], row["model_id"])
        if key not in seen:
            seen.add(key)
            result["llm"].append(row)
    if user_id == user.id:
        # Only ever the caller's OWN personal models. An administrator
        # inspecting somebody's grants asks for that user's id, and their
        # personal sign-in is not the administrator's business — nor is it in
        # the grant editor's gift to assign.
        from .personal_models import personal_llm_rows

        result["llm"].extend(personal_llm_rows())
    return result


def allowed_llm_options() -> dict[str, Any]:
    user = get_current_user()
    if user.is_admin:
        return list_llm_options(admin_catalog())
    catalog = admin_catalog()
    llm_service = catalog.get("services", {}).get("llm", {})
    active_profile_id = str(llm_service.get("active_profile_id") or "")
    active_model_id = str(llm_service.get("active_model_id") or "")
    options = [
        {
            "profile_id": item.get("profile_id"),
            "model_id": item.get("model_id"),
            "profile_name": item.get("profile_name")
            or item.get("name")
            or item.get("profile_id")
            or "LLM",
            "model_name": item.get("name") or item.get("model") or item.get("model_id"),
            "label": item.get("name") or item.get("model") or item.get("model_id"),
            "model": item.get("model") or "",
            "provider": item.get("provider") or "",
            "reasoning_effort": item.get("reasoning_effort"),
            "supported_reasoning_efforts": item.get("supported_reasoning_efforts"),
            "source": item.get("source") or "admin",
            "is_active_default": (
                item.get("profile_id") == active_profile_id
                and item.get("model_id") == active_model_id
            ),
        }
        for item in redacted_model_access(user.id).get("llm", [])
        if item.get("available")
    ]
    # A plan can name its own default (e.g. the cheapest model for Free);
    # it wins over the deployment default when the user can use it.
    preferred = plan_default_option(user.id, options)
    if preferred is not None:
        for option in options:
            option["is_active_default"] = option is preferred
    active = next(
        (
            {"profile_id": option["profile_id"], "model_id": option["model_id"]}
            for option in options
            if option["is_active_default"]
        ),
        None,
    )
    return {"active": active, "options": options}


def plan_default_option(user_id: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The option matching the user's plan ``default_model``, if any."""
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.services.quota_service import model_matches_name, plan_default_model

        with get_db_session() as db:
            wanted = plan_default_model(user_id, db)
            if not wanted:
                return None
            for option in options:
                if model_matches_name(str(option.get("model") or ""), wanted, db):
                    return option
    except Exception:
        return None
    return None


def has_capability_access(capability: str, user_id: str | None = None) -> bool:
    """Whether the user has at least one usable model for ``capability``.

    Admins are never gated — they manage the catalog directly. For ordinary
    users this mirrors exactly what ``redacted_model_access`` exposes to the
    frontend, so the server-side gate and the UI lock always agree.
    """
    user = get_current_user()
    if user.is_admin:
        return True
    if user_id is None:
        user_id = user.id
    items = redacted_model_access(user_id).get(capability, []) or []
    return any(item.get("available") for item in items)


def apply_allowed_llm_selection(selection: dict[str, Any] | None) -> dict[str, Any] | None:
    """Allow only admin-granted LLM profile/model selections for ordinary users."""
    user = get_current_user()
    if user.is_admin or not selection:
        return selection
    profile_id = str(selection.get("profile_id") or "")
    model_id = str(selection.get("model_id") or "")
    for item in redacted_model_access(user.id).get("llm", []):
        if item.get("profile_id") == profile_id and item.get("model_id") == model_id:
            return selection
    raise PermissionError("This model is not assigned to your account.")
