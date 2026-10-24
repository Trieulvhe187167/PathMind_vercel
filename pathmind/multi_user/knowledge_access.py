"""Knowledge-base visibility and write guards for the multi-user layer."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from pathmind.knowledge.manager import KnowledgeBaseManager
from pathmind.knowledge.manifest import (
    MANIFEST_NOTE_LIMIT,
    KbManifest,
    build_manifest,
    document_root,
)

from .context import get_current_user
from .models import KnowledgeResource
from .paths import get_admin_path_service, get_current_path_service

ADMIN_PREFIX = "admin:kb:"
USER_PREFIX = "user:kb:"
DEFAULT_KB_ALIASES = {"", "default", "current", "selected", "默认", "默认知识库", "当前知识库"}


@lru_cache(maxsize=128)
def _manager_for(base_dir: str) -> KnowledgeBaseManager:
    return KnowledgeBaseManager(base_dir=base_dir)


def current_kb_base_dir() -> Path:
    from pathmind.services.workspace.knowledge import library_request

    from .paths import get_account_path_service

    paths = get_account_path_service() if library_request.get() else get_current_path_service()
    return paths.get_knowledge_bases_root()


def admin_kb_base_dir() -> Path:
    return get_admin_path_service().get_knowledge_bases_root()


def current_kb_manager() -> KnowledgeBaseManager:
    return _manager_for(str(current_kb_base_dir().resolve()))


def admin_kb_manager() -> KnowledgeBaseManager:
    return _manager_for(str(admin_kb_base_dir().resolve()))


def _strip_resource_prefix(value: str) -> tuple[str | None, str]:
    raw = str(value or "").strip()
    if raw.startswith(ADMIN_PREFIX):
        return "admin", raw[len(ADMIN_PREFIX) :]
    if raw.startswith(USER_PREFIX):
        return "user", raw[len(USER_PREFIX) :]
    return None, raw


def resolve_kb(kb_ref: str, *, require_write: bool = False) -> KnowledgeResource:
    from pathmind.services.workspace.knowledge import (
        learning_source_kbs,
        library_request,
        parse_kb_id,
        resolve_qualified,
        resolve_selected,
    )
    from pathmind.services.workspace.resources import current_resources

    if not require_write and kb_ref in learning_source_kbs.get():
        return resolve_qualified(kb_ref)
    selected = None if library_request.get() else current_resources().knowledge_bases
    if selected is not None:
        return resolve_selected(kb_ref, selected, require_write=require_write)
    if parse_kb_id(kb_ref) is not None:
        return resolve_qualified(kb_ref, require_write=require_write)
    return _resolve_kb(kb_ref, require_write=require_write)


def _resolve_kb(kb_ref: str, *, require_write: bool = False) -> KnowledgeResource:
    user = get_current_user()
    requested_source, name = _strip_resource_prefix(kb_ref)

    if user.is_admin:
        manager = current_kb_manager()
        resolved = _resolve_default_or_name(manager, name)
        return KnowledgeResource(
            id=f"admin:kb:{resolved}",
            name=resolved,
            base_dir=current_kb_base_dir(),
            source="admin",
            assigned=False,
            read_only=False,
        )

    user_manager = current_kb_manager()

    if requested_source == "admin":
        # No per-user grants of admin knowledge bases: personal KBs only.
        raise HTTPException(status_code=403, detail="Knowledge base is not available to you")

    if requested_source == "user":
        resolved = _resolve_default_or_name(user_manager, name)
        return KnowledgeResource(
            id=f"user:kb:{resolved}",
            name=resolved,
            base_dir=current_kb_base_dir(),
            source="user",
            assigned=False,
            read_only=False,
        )

    if name.lower() in DEFAULT_KB_ALIASES:
        resolved = _resolve_default_or_name(user_manager, name)
        return KnowledgeResource(
            id=f"user:kb:{resolved}",
            name=resolved,
            base_dir=current_kb_base_dir(),
            source="user",
            assigned=False,
            read_only=False,
        )

    user_names = set(user_manager.list_knowledge_bases())
    if name in user_names:
        return KnowledgeResource(
            id=f"user:kb:{name}",
            name=name,
            base_dir=current_kb_base_dir(),
            source="user",
            assigned=False,
            read_only=False,
        )

    raise HTTPException(status_code=404, detail=f"Knowledge base '{name}' not found")


def _resolve_default_or_name(manager: KnowledgeBaseManager, name: str) -> str:
    requested = str(name or "").strip()
    names = manager.list_knowledge_bases()
    if requested and requested in names:
        return requested
    if requested.lower() in DEFAULT_KB_ALIASES:
        default_kb = manager.get_default()
        if default_kb and default_kb in names:
            return default_kb
        raise HTTPException(status_code=404, detail="No default knowledge base is configured")
    raise HTTPException(status_code=404, detail=f"Knowledge base '{requested}' not found")


def manager_for_resource(resource: KnowledgeResource) -> KnowledgeBaseManager:
    return _manager_for(str(resource.base_dir.resolve()))


def list_visible_knowledge_bases() -> list[dict[str, Any]]:
    from pathmind.services.workspace.knowledge import knowledge_catalog, library_request
    from pathmind.services.workspace.resources import current_resources

    if library_request.get():
        return knowledge_catalog()
    selected = current_resources().knowledge_bases
    if selected is None:
        return _list_visible_knowledge_bases()
    catalog = {item["id"]: item for item in knowledge_catalog()}
    return [catalog[rid] for rid in selected if rid in catalog]


def _list_visible_knowledge_bases() -> list[dict[str, Any]]:
    user = get_current_user()
    manager = current_kb_manager()
    items: list[dict[str, Any]] = []
    for name in manager.list_knowledge_bases():
        items.append(
            {
                "id": f"admin:kb:{name}" if user.is_admin else f"user:kb:{name}",
                "name": name,
                "source": "admin" if user.is_admin else "user",
                "assigned": False,
                "read_only": False,
                "provenance_label": "Created by you" if not user.is_admin else "Admin workspace",
            }
        )

    return items


def assert_writable(kb_ref: str) -> KnowledgeResource:
    return resolve_kb(kb_ref, require_write=True)


def resolve_for_rag(kb_ref: str | None) -> KnowledgeResource | None:
    if not kb_ref:
        return None
    resource = resolve_kb(kb_ref, require_write=False)
    if resource.assigned:
        from .audit import log_usage

        log_usage("knowledge_base", resource.id, "rag_query")
    return resource


def resolve_kb_metadata(kb_ref: str | None) -> dict[str, Any] | None:
    """Access-checked KB metadata (``type`` / ``vault_path`` / …) for ``kb_ref``.

    Returns ``None`` when the reference is empty or not accessible to the
    current user. A pure read with no usage audit (unlike
    :func:`resolve_for_rag`) — safe to call while resolving capability bindings.
    """
    if not kb_ref:
        return None
    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
    except HTTPException:
        return None
    manager = _manager_for(str(resource.base_dir.resolve()))
    return manager.get_metadata(resource.name)


def resolve_kb_manifest(
    kb_ref: str | None,
    *,
    limit: int = MANIFEST_NOTE_LIMIT,
    pattern: str = "",
) -> KbManifest | None:
    """Access-checked document inventory for ``kb_ref`` (``None`` if inaccessible).

    The one seam through which the chat manifest and the ``kb_files`` tool read
    a KB's document set, so neither can bypass the per-user visibility rules
    :func:`resolve_kb` enforces. A pure read with no usage audit, mirroring
    :func:`resolve_kb_metadata`; it touches the filesystem, so async callers
    should hand it to a worker thread.
    """
    if not kb_ref:
        return None
    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
    except HTTPException:
        return None
    manager = _manager_for(str(resource.base_dir.resolve()))
    entry = manager.get_kb_entry(resource.name)
    if entry is None:
        return None
    return build_manifest(
        name=resource.name,
        kb_dir=resource.base_dir / resource.name,
        entry=entry,
        limit=limit,
        pattern=pattern,
    )


def resolve_kb_document_path(kb_ref: str | None, rel_path: str) -> Path | None:
    """Access-checked path to one document inside ``kb_ref``.

    The seam a caller uses to *read a single file* the learner picked out of a
    knowledge base, rather than retrieving passages from the whole thing. Same
    visibility rules as :func:`resolve_kb_manifest`, and ``rel_path`` is
    resolved against the KB's own document root and then re-checked to be
    inside it — the value arrives from a client (and, for a generated outline,
    has passed through a model), so ``..`` and absolute paths must not be able
    to reach a file the KB does not own.

    ``None`` when the KB is inaccessible, has no local documents (a connected
    external resource), or the path is not a file inside it.
    """
    if not kb_ref or not str(rel_path or "").strip():
        return None
    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
    except HTTPException:
        return None
    manager = _manager_for(str(resource.base_dir.resolve()))
    entry = manager.get_kb_entry(resource.name)
    if entry is None:
        return None
    root = document_root(resource.base_dir / resource.name, entry)
    if root is None or not root.is_dir():
        return None
    try:
        resolved_root = root.resolve()
        candidate = (root / str(rel_path).strip()).resolve()
    except OSError:
        return None
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        return None
    return candidate
