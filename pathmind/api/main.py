import asyncio
from contextlib import asynccontextmanager
import logging
import sys

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from pathmind.logging import configure_logging
from pathmind.services.config import (
    ensure_runtime_settings_files,
    export_runtime_settings_to_env,
    load_auth_settings,
    load_system_settings,
)
from pathmind.services.config.origins import normalize_origins
from pathmind.services.path_service import get_path_service

ensure_runtime_settings_files()
export_runtime_settings_to_env(overwrite=True)
configure_logging()
logger = logging.getLogger(__name__)


class _SuppressWsNoise(logging.Filter):
    """Suppress noisy uvicorn logs for WebSocket connection churn."""

    _SUPPRESSED = ("connection open", "connection closed")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(f in msg for f in self._SUPPRESSED)


logging.getLogger("uvicorn.error").addFilter(_SuppressWsNoise())

CONFIG_DRIFT_ERROR_TEMPLATE = (
    "Configuration Drift Detected: Capability tool references {drift} are not "
    "registered in the runtime tool registry. Register the missing tools or "
    "remove the stale tool names from the capability manifests."
)


def validate_tool_consistency():
    """
    Validate that capability manifests only reference tools that are actually
    registered in the runtime ``ToolRegistry``.
    """
    try:
        from pathmind.runtime.registry.capability_registry import get_capability_registry
        from pathmind.runtime.registry.tool_registry import get_tool_registry

        capability_registry = get_capability_registry()
        tool_registry = get_tool_registry()
        available_tools = set(tool_registry.list_tools())

        referenced_tools = set()
        for manifest in capability_registry.get_manifests():
            referenced_tools.update(manifest.get("tools_used", []) or [])

        drift = referenced_tools - available_tools
        if drift:
            raise RuntimeError(CONFIG_DRIFT_ERROR_TEMPLATE.format(drift=drift))
    except RuntimeError:
        logger.exception("Configuration validation failed")
        raise
    except Exception:
        logger.exception("Failed to load configuration for validation")
        raise


def _build_cors_settings() -> dict[str, object]:
    """Build CORS settings for both localhost and remote Docker deployments."""
    system_settings = load_system_settings()
    auth_settings = load_auth_settings()
    frontend_port = str(system_settings["frontend_port"])
    extra_origins = normalize_origins(
        [system_settings["cors_origin"], system_settings["cors_origins"]]
    )
    origins = [
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    for origin in extra_origins:
        if origin not in origins:
            origins.append(origin)

    # Auth is disabled by default. In that local/single-user mode, mirror the
    # pre-v1.3.8 behavior and allow remote Docker/LAN origins out of the box.
    # When auth is enabled, require explicit CORS_ORIGIN(S) for credentialed
    # cross-origin requests.
    allow_origin_regex = None if auth_settings["enabled"] else r"https?://.*"
    mode = "explicit" if auth_settings["enabled"] else "permissive"
    return {
        "allow_origins": origins,
        "allow_origin_regex": allow_origin_regex,
        "mode": mode,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management
    Gracefully handle startup and shutdown events, avoid CancelledError
    """
    # Execute on startup
    logger.info("Application startup")
    app.state.ready = False

    # Validate configuration consistency
    validate_tool_consistency()

    # Build one process-level dependency graph.  Every adapter resolves turns
    # through this container, so a WebSocket cannot accidentally construct a
    # second store/runtime for the same authenticated scope.
    from pathmind.app.container import get_application_container

    application_container = getattr(app.state, "application_container", None)
    if application_container is None:
        application_container = get_application_container()
        app.state.application_container = application_container
    await application_container.start()
    from pathmind.api.utils.progress_broadcaster import ProgressBroadcaster
    from pathmind.api.utils.task_log_stream import get_task_stream_manager
    from pathmind.knowledge.progress_events import install_progress_ports

    install_progress_ports(
        broadcast=ProgressBroadcaster.get_instance().broadcast,
        emit_task_event=get_task_stream_manager().emit,
    )
    migration_reports = await application_container.run_startup_data_migrations()
    legacy_reports = migration_reports["legacy_chat"]
    migrated_reports = [report for report in legacy_reports if report.get("source_hash")]
    if migrated_reports:
        logger.info(
            "Legacy chat migration complete: imported=%s skipped=%s archived=%s",
            sum(int(report.get("imported") or 0) for report in migrated_reports),
            sum(int(report.get("skipped") or 0) for report in migrated_reports),
            [report.get("archived_to", "") for report in migrated_reports],
        )
    workspace_migrated = sum(
        int(report.get("migrated") or 0) for report in migration_reports["workspace_preferences"]
    )
    if workspace_migrated:
        logger.info(
            "Workspace session migration complete: migrated=%s scopes=%s",
            workspace_migrated,
            len(migration_reports["workspace_preferences"]),
        )

    # Initialize LLM client early so OPENAI_* env vars are available before
    # any downstream provider integrations start.
    try:
        from pathmind.services.llm import get_llm_client

        llm_client = get_llm_client()
        logger.info(f"LLM client initialized: model={llm_client.config.model}")
    except Exception as e:
        logger.warning(f"Failed to initialize LLM client at startup: {e}")

    try:
        from pathmind.events.event_bus import get_event_bus

        event_bus = get_event_bus()
        await event_bus.start()
        logger.info("EventBus started")
    except Exception as e:
        logger.warning(f"Failed to start EventBus: {e}")

    async def _start_partners() -> None:
        from pathmind.services.partners import get_partner_manager

        manager = get_partner_manager()
        manager.configure_runtime_owner(application_container.worker_id)
        await manager.auto_start_partners()

    async def _stop_partners() -> None:
        from pathmind.services.partners import get_partner_manager

        await get_partner_manager().stop_all(preserve_auto_start=True)

    async def _start_cron() -> None:
        from pathmind.services.cron import get_cron_service

        await get_cron_service().start()

    async def _stop_cron() -> None:
        from pathmind.services.cron import get_cron_service

        await get_cron_service().stop()

    async def _start_github_sync() -> None:
        from pathmind.services.github_source.sync_service import get_sync_service

        await get_sync_service().start()

    async def _stop_github_sync() -> None:
        from pathmind.services.github_source.sync_service import get_sync_service

        await get_sync_service().stop()

    from pathmind.runtime.coordination import BackgroundCommandKind

    async def _handle_background_command(command) -> None:
        kind = str(command.kind)
        if kind == BackgroundCommandKind.CRON_RELOAD:
            from pathmind.services.cron import get_cron_service

            get_cron_service().reload()
            return

        from pathmind.services.partners import get_partner_manager

        manager = get_partner_manager()
        manager.configure_runtime_owner(application_container.worker_id)
        partner_id = str(command.payload.get("partner_id") or "").strip()
        if not partner_id:
            raise ValueError(f"Background command {kind!r} needs partner_id")
        if kind == BackgroundCommandKind.PARTNER_START:
            instance = await manager.start_partner(partner_id)
            if bool(command.payload.get("persist_auto_start", True)):
                manager.save_config(partner_id, instance.config, auto_start=True)
            return
        if kind == BackgroundCommandKind.PARTNER_STOP:
            await manager.stop_partner(
                partner_id,
                preserve_auto_start=bool(command.payload.get("preserve_auto_start", False)),
            )
            return
        if kind == BackgroundCommandKind.PARTNER_RELOAD:
            instance = manager.get_partner(partner_id)
            if instance is None or not instance.running:
                return
            config = manager.load_config(partner_id)
            if config is not None:
                instance.config = config
            await manager.reload_channels(partner_id)
            return
        raise ValueError(f"Unknown background command kind: {kind}")

    cron_service = None
    try:
        from pathmind.services.cron import get_cron_service

        cron_service = get_cron_service()
        loop = asyncio.get_running_loop()

        def _notify_cron_change() -> None:
            task = loop.create_task(
                application_container.coordinator.submit_background_command(
                    BackgroundCommandKind.CRON_RELOAD
                )
            )
            task.add_done_callback(
                lambda completed: completed.exception() if not completed.cancelled() else None
            )

        cron_service.change_notifier = _notify_cron_change
    except Exception:
        logger.exception("Failed to configure cron change notifications")

    from pathmind.runtime.background_leader import BackgroundLeaderSupervisor

    background_supervisor = BackgroundLeaderSupervisor(
        application_container.coordinator,
        application_container.worker_id,
        start_callbacks=[_start_partners, _start_cron, _start_github_sync],
        stop_callbacks=[_stop_partners, _stop_cron, _stop_github_sync],
        recovery_callback=application_container.recover_once,
        control_callback=_handle_background_command,
        renew_interval_seconds=application_container.settings.renew_interval_seconds,
        recovery_interval_seconds=application_container.settings.recovery_interval_seconds,
    )
    app.state.background_supervisor = background_supervisor
    await background_supervisor.start()

    # Ping PocketBase if configured — logs a warning (not an error) if unreachable
    try:
        from pathmind.services.pocketbase_client import ping_pocketbase

        await ping_pocketbase()
    except Exception as e:
        logger.warning(f"PocketBase startup check failed: {e}")

    # Migrate any v1 memory files (PROFILE.md / SOUL.md / SUMMARY.md) into a
    # backup folder so the v2 three-layer subsystem starts clean.
    try:
        from pathmind.services.memory import (
            migrate_partner_surface_if_needed,
            migrate_v1_if_needed,
        )
        from pathmind.services.path_service import get_path_service

        get_path_service().migrate_legacy_memory_markdown()
        backup = migrate_v1_if_needed()
        if backup is not None:
            logger.info("v1 memory archived to %s", backup)
        # Rename the legacy ``tutorbot`` memory surface (footnote refs, L2
        # doc, snapshot/trace dirs, L3 meta keys) to ``partner``.
        migrate_partner_surface_if_needed()
    except Exception as e:
        logger.warning(f"v1 memory migration failed: {e}")

    app.state.ready = True
    yield

    # Execute on shutdown
    app.state.ready = False
    logger.info("Application shutdown")

    install_progress_ports(broadcast=None, emit_task_event=None)

    try:
        await background_supervisor.close()
        logger.info("Leader-owned background services stopped")
    except Exception as e:
        logger.warning(f"Failed to stop background leader: {e}")

    try:
        await application_container.close()
        logger.info("Application container closed")
    except Exception as e:
        logger.warning(f"Failed to close application container: {e}")

    # Close pooled LLM SDK clients so their keep-alive sockets and transports
    # are released deterministically instead of waiting for interpreter GC.
    try:
        from pathmind.services.llm.provider_factory import close_runtime_provider_pool

        await close_runtime_provider_pool()
        logger.info("LLM provider pool closed")
    except Exception as e:
        logger.warning(f"Failed to close LLM provider pool: {e}")

    try:
        from pathmind.runtime.agentic.client import close_agentic_client_pool

        await close_agentic_client_pool()
        logger.info("Agentic LLM client pool closed")
    except Exception as e:
        logger.warning(f"Failed to close agentic LLM client pool: {e}")

    # Stop EventBus
    try:
        from pathmind.events.event_bus import get_event_bus

        event_bus = get_event_bus()
        await event_bus.stop()
        logger.info("EventBus stopped")
    except Exception as e:
        logger.warning(f"Failed to stop EventBus: {e}")


from pathmind.services.workspace.activity import WorkspaceActivityMiddleware

app = FastAPI(
    title="PathMind API",
    version="1.0.0",
    lifespan=lifespan,
    # Disable automatic trailing slash redirects to prevent protocol downgrade issues
    # when deployed behind HTTPS reverse proxies (e.g., nginx).
    # Without this, FastAPI's 307 redirects may change HTTPS to HTTP.
    # See: https://github.com/HKUDS/DeepTutor/issues/112
    redirect_slashes=False,
)
app.add_middleware(WorkspaceActivityMiddleware)


@app.middleware("http")
async def json_error_boundary(request: Request, call_next):
    """Catch-all so 500s always return JSON, never Starlette's plain-text body.

    Registered as a middleware rather than an ``@app.exception_handler``: a
    handler for ``Exception`` is installed on Starlette's outermost
    ``ServerErrorMiddleware``, so its response skips every middleware added
    here — the 500 would carry no CORS headers (a cross-origin caller sees an
    opaque CORS failure instead of this body) and would never reach the access
    log below. Registered *before* ``CORSMiddleware``, this boundary sits
    inside it, so the response travels back out through the normal stack.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {exc}",
                "type": type(exc).__name__,
            },
        )


# Access logging is funneled through this one middleware. uvicorn's own
# per-request access log is disabled on every launch path (run_server.py via
# access_log=False; the launcher and Docker via `--no-access-log`), so routine
# 200s — the chatty frontend polling of /settings, /tools, /knowledge-bases,
# etc. — never reach the logs. Only non-200s are surfaced, since those are the
# ones worth seeing.
#
# The `pathmind.access` logger gets its own INFO stdout handler rather than
# leaning on the root handlers: the root console handler runs at the global log
# level (WARNING by default), which would swallow these INFO access lines.
# propagate=False keeps them from also printing through root if the global
# level is ever lowered to INFO/DEBUG.
_access_logger = logging.getLogger("pathmind.access")
if not any(getattr(h, "_pathmind_access_handler", False) for h in _access_logger.handlers):
    _access_handler = logging.StreamHandler(sys.stdout)
    _access_handler.setLevel(logging.INFO)
    _access_handler.setFormatter(logging.Formatter("%(message)s"))
    _access_handler._pathmind_access_handler = True  # type: ignore[attr-defined]
    _access_logger.addHandler(_access_handler)
    _access_logger.setLevel(logging.INFO)
    _access_logger.propagate = False


@app.middleware("http")
async def selective_access_log(request, call_next):
    response = await call_next(request)
    # An expired app login must not strand provider credentials in a callback URL.
    # Authentication still runs normally; only its failure presentation changes.
    if (
        request.url.path == "/api/video-learning/invidious/account/callback"
        and response.status_code in {401, 403}
    ):
        response = RedirectResponse(
            "/watching?account=authorization_login_required",
            status_code=303,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    if response.status_code != 200:
        _access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            request.client.host if request.client else "-",
            request.method,
            request.url.path,
            request.scope.get("http_version", "1.1"),
            response.status_code,
        )
    return response


_cors_settings = _build_cors_settings()
logger.info(
    "CORS configured: mode=%s allow_origins=%s allow_origin_regex=%s",
    _cors_settings["mode"],
    _cors_settings["allow_origins"],
    _cors_settings["allow_origin_regex"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_settings["allow_origins"],
    allow_origin_regex=_cors_settings["allow_origin_regex"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize user directories on startup
try:
    from pathmind.services.setup import init_user_directories

    init_user_directories()
except Exception:
    # Fallback: just create the main directory if it doesn't exist
    user_dir = get_path_service().get_public_outputs_root()
    if not user_dir.exists():
        user_dir.mkdir(parents=True)

# Import routers only after runtime settings are initialized.
# Some router modules load YAML settings at import time.
from pathmind.api.routers import (
    admin_dashboard,
    agent_config,
    attachments,
    auth,
    billing,
    book,
    capabilities,
    admin_ops,
    answer_ratings,
    knowledge_branch,
    recommendations,
    rewards,
    system_status,
    knowledge_tree,
    capabilities_settings,
    co_writer,
    community,
    courses,
    dashboard,
    friends,
    imports,
    knowledge,
    marginnote4,
    mastery_path,
    memory,
    memory_sharing,
    notebook,
    outputs,
    partner_groups,
    partner_shares,
    partners,
    personas,
    practice,
    question,
    question_notebook,
    quiz_judge,
    reading,
    reading_extensions,
    sessions,
    settings,
    skills,
    system,
    unified_ws,
    video_learning,
    visualizers,
    voice,
    workspace,
)
from pathmind.api.routers import (
    tools as tools_router,
)
from pathmind.api.routers.file_library import router as file_library_router  # noqa: E402
from pathmind.api.routers.multi_user import router as multi_user_router  # noqa: E402

from pathmind.database.connection import init_database
init_database()

# Seed files in data/seed/ (accounts, knowledge graph, LLM providers...) load once.
try:
    from pathmind.services.seed import apply_pending as _apply_seeds

    _apply_seeds()
except Exception as _seed_exc:  # noqa: BLE001 - never block start-up
    logging.getLogger(__name__).warning("Seed loading skipped: %s", _seed_exc)

# Auth router is public — login/logout/register/status require no token
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(outputs.router, prefix="/files/outputs", tags=["outputs"])
app.include_router(
    workspace.files_router,
    prefix="/files/workspace-items",
    tags=["workspace"],
)

# All other routers require a valid session when AUTH_ENABLED=true.
# require_auth is a no-op when AUTH_ENABLED=false, so this is safe for local use.
from pathmind.api.routers.auth import (  # noqa: E402
    require_admin,
    require_rnd,
    require_learning_surface,
)

_auth = [Depends(require_learning_surface)]
# Partner data is anchored at the admin workspace (data/partners) and shared
# process-wide, so management is admin-gated in multi-user deployments
# (single-user local runs are implicitly admin — no behaviour change there).
_admin = [Depends(require_admin)]
# Plan entitlements (services/entitlements.py): paid-only surfaces answer 402
# with an upgrade hint. They run after ``_auth`` so the current user is known.
from pathmind.services import entitlements as _ent  # noqa: E402

_community = _auth + [Depends(_ent.community_guard)]
_reading = _auth + [Depends(_ent.reading_guard)]
_watching = _auth + [Depends(_ent.feature_dependency("watching"))]
_notebooks = _auth + [Depends(_ent.notebook_guard)]
_courses = _auth + [Depends(_ent.courses_guard)]
_memory = _auth + [Depends(_ent.feature_dependency("memory"))]
_question_bank = _auth + [Depends(_ent.question_bank_guard)]

app.include_router(billing.router, prefix="/api/billing", tags=["billing"])
app.include_router(
    knowledge_tree.router,
    prefix="/api/knowledge-tree",
    tags=["knowledge-tree"],
    dependencies=_auth,
)
app.include_router(
    knowledge_branch.router,
    prefix="/api/knowledge-branch",
    tags=["knowledge-branch"],
    dependencies=_auth,
)
app.include_router(
    recommendations.router, prefix="/api/recommendations", tags=["recommendations"], dependencies=_auth
)
app.include_router(
    answer_ratings.router, prefix="/api/answer-ratings", tags=["answer-ratings"], dependencies=_auth
)
app.include_router(admin_ops.router, prefix="/api/admin-ops", tags=["admin-ops"], dependencies=_admin)
app.include_router(rewards.router, prefix="/api/rewards", tags=["rewards"], dependencies=_auth)
app.include_router(
    system_status.router,
    prefix="/api/system-status",
    tags=["system-status"],
    dependencies=_auth + [Depends(require_rnd)],
)
app.include_router(friends.router, prefix="/api/friends", tags=["friends"], dependencies=_auth)
app.include_router(partner_shares.router, prefix="/api/social", tags=["social"], dependencies=_auth)
app.include_router(community.router, prefix="/api/community", tags=["community"], dependencies=_community)
app.include_router(admin_dashboard.router, prefix="/api/admin", tags=["admin"], dependencies=_admin)

app.include_router(
    multi_user_router,
    prefix="/api/multi-user",
    tags=["multi-user"],
    dependencies=_auth,
)
app.include_router(question.router, prefix="/api/question", tags=["question"], dependencies=_auth)
app.include_router(knowledge.router, prefix="/api", tags=["knowledge-bases"], dependencies=_auth)
app.include_router(imports.router, prefix="/api/imports", tags=["imports"], dependencies=_auth)
app.include_router(
    dashboard.router, prefix="/api/dashboard", tags=["dashboard"], dependencies=_auth
)
app.include_router(
    mastery_path.router,
    prefix="/api/mastery-paths",
    tags=["mastery-path"],
    dependencies=_auth,
)
app.include_router(
    file_library_router,
    prefix="/files/library",
    tags=["library"],
    dependencies=_auth,
)
# WebSocket handlers authenticate inside the connection before ``accept``.
# Keep them off HTTP router dependencies: ``require_learning_surface`` takes a
# Request, which FastAPI cannot construct for a WebSocket scope.
app.include_router(question.ws_router, prefix="/ws/questions", tags=["question"])
app.include_router(knowledge.ws_router, prefix="/ws", tags=["knowledge-bases"])
app.include_router(
    mastery_path.ws_router,
    prefix="/ws",
    tags=["mastery-path"],
)
app.include_router(co_writer.router, prefix="/api", tags=["documents"], dependencies=_auth)
app.include_router(notebook.router, prefix="/api", tags=["notebooks"], dependencies=_notebooks)
app.include_router(book.router, prefix="/api", tags=["books"], dependencies=_auth)
app.include_router(book.ws_router, prefix="/ws", tags=["books"])
app.include_router(reading.router, prefix="/api/reading", tags=["reading"], dependencies=_reading)
app.include_router(
    reading_extensions.router,
    prefix="/api/reading",
    tags=["reading-extensions"],
    dependencies=_reading,
)
app.include_router(memory.router, prefix="/api/memory", tags=["memory"], dependencies=_memory)
# Consent management is never plan-gated: anyone can switch sharing off and erase it.
app.include_router(
    memory_sharing.router,
    prefix="/api/memory-sharing",
    tags=["memory-sharing"],
    dependencies=_auth,
)
app.include_router(
    capabilities_settings.router,
    prefix="/api/capabilities",
    tags=["capabilities"],
    dependencies=_auth,
)
app.include_router(
    capabilities.router,
    prefix="/api/capabilities",
    tags=["capabilities"],
    dependencies=_auth,
)
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"], dependencies=_auth)
app.include_router(courses.router, prefix="/api/courses", tags=["courses"], dependencies=_courses)
app.include_router(
    question_notebook.router,
    prefix="/api/question-notebook",
    tags=["question-notebook"],
    dependencies=_question_bank,
)
app.include_router(
    practice.router,
    prefix="/api/question-notebook/practice",
    tags=["practice"],
    dependencies=_auth,
)
# Public UI-settings read (auth pages bootstrap the interface language
# before a session exists, so GET /api/settings/ui must not be gated
# by _auth). Mounted first so the path resolves here, not on the gated
# settings router below.
app.include_router(
    settings.public_router,
    prefix="/api/settings",
    tags=["settings"],
)
app.include_router(settings.router, prefix="/api/settings", tags=["settings"], dependencies=_auth)
app.include_router(
    workspace.settings_router,
    prefix="/api/settings/workspace",
    tags=["workspace-settings"],
    dependencies=_auth,
)
app.include_router(
    video_learning.settings_router,
    prefix="/api/settings/video-learning",
    tags=["video-learning-settings"],
    dependencies=_admin,
)
app.include_router(skills.router, prefix="/api/skills", tags=["skills"], dependencies=_auth)
app.include_router(personas.router, prefix="/api", tags=["personas"], dependencies=_auth)
app.include_router(tools_router.router, prefix="/api/tools", tags=["tools"], dependencies=_auth)
app.include_router(system.router, prefix="/api/system", tags=["system"], dependencies=_auth)
app.include_router(voice.router, prefix="/api/voice", tags=["voice"], dependencies=_auth)
app.include_router(
    video_learning.router,
    prefix="/api/video-learning",
    tags=["video-learning"],
    dependencies=_watching,
)
app.include_router(
    visualizers.router,
    prefix="/api/visualizers",
    tags=["visualizers"],
    dependencies=_auth,
)
app.include_router(
    agent_config.router, prefix="/api/agent-config", tags=["agent-config"], dependencies=_auth
)
# Partners are per-user resources now: anyone may build their own, and an admin
# may assign theirs to others. Only ``_auth`` here — every route in the router
# declares whether it needs *use* or *manage* rights on the partner it names
# (see ``multi_user.partner_access``), which a blanket admin gate could not
# express.
app.include_router(partners.router, prefix="/api/partners", tags=["partners"], dependencies=_auth)
app.include_router(
    partner_groups.router,
    prefix="/api/partner-groups",
    tags=["partner-groups"],
    dependencies=_auth,
)
app.include_router(partners.ws_router, prefix="/ws/partners", tags=["partners"])
app.include_router(
    partner_groups.ws_router,
    prefix="/ws/partner-groups",
    tags=["partner-groups"],
)
app.include_router(
    attachments.router,
    prefix="/files/attachments",
    tags=["attachments"],
    dependencies=_auth,
)

# MarginNote 4 device bridge — pairing/management routes carry _auth in-router;
# sync/heartbeat use device-token auth (the Add-on has no session).
app.include_router(
    marginnote4.router,
    prefix="/api/marginnote4",
    tags=["marginnote4"],
)

# Unified WebSocket endpoint — auth is checked inside the handler (WebSockets
# cannot use FastAPI dependencies in the standard way)
app.include_router(unified_ws.router, tags=["unified-ws"])

# Quiz AI-judge WebSocket — same caveat as unified_ws above; auth is checked
# inside the handler so the WS upgrade isn't rejected by an HTTP-style dep.
app.include_router(quiz_judge.router, prefix="/ws", tags=["quiz-judge"])


@app.get("/")
async def root():
    return {"message": "Welcome to PathMind API"}


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready(request: Request):
    if not bool(getattr(request.app.state, "ready", False)):
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    container = getattr(request.app.state, "application_container", None)
    if container is None or not await container.coordinator.health():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "coordination_unavailable"},
        )
    return {"status": "ready"}


if __name__ == "__main__":
    from pathmind.api.run_server import main as run_server_main

    run_server_main()
