"""FastAPI application factory for the Forge web UI."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_app(
    *,
    db_url: str | None = None,
    jwt_secret: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_url: Async database URL (e.g. ``sqlite+aiosqlite:///forge.db``).
            Single DB for auth + pipelines + tasks.
        jwt_secret: Secret key used for JWT token signing.
    """
    from forge.core.logging_config import configure_logging

    configure_logging()

    from forge.config.settings import ForgeSettings as _AuthSettings

    _auth_settings = _AuthSettings()
    auth_disabled = _auth_settings.auth_disabled

    if jwt_secret is None:
        jwt_secret = os.environ.get("FORGE_JWT_SECRET", "")
        if not jwt_secret:
            import secrets

            jwt_secret = secrets.token_urlsafe(32)
            # Auto-enable single-user mode unless user explicitly set FORGE_AUTH_DISABLED=false
            if os.environ.get("FORGE_AUTH_DISABLED", "").lower() != "false":
                auth_disabled = True
                logging.getLogger(__name__).info(
                    "Single-user mode (set FORGE_JWT_SECRET to enable auth)"
                )
            else:
                logging.getLogger(__name__).warning(
                    "No FORGE_JWT_SECRET set — using random secret (tokens won't survive restarts). "
                    "Set FORGE_JWT_SECRET env var for production: "
                    "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
                )

    # ── Single unified database ─────────────────────────────────────
    from forge.storage.db import Database

    db = Database(db_url) if db_url is not None else None

    async def _cleanup_stale_stores(app: FastAPI) -> None:
        """Periodically prune stale entries from in-memory stores.

        Runs every 30 minutes and removes entries older than 2 hours from:
        - followup_store
        - pipeline_images
        - pending_graphs
        """
        import time
        from datetime import datetime

        ttl_seconds = 2 * 60 * 60  # 2 hours
        interval_seconds = 30 * 60  # 30 minutes

        while True:
            await asyncio.sleep(interval_seconds)
            now = time.monotonic()
            cutoff = now - ttl_seconds
            utc_now = datetime.now(UTC)

            # Prune followup_store using FollowUpExecution.created_at field
            followup_store: dict = getattr(app.state, "followup_store", {})
            stale_keys = []
            for k, v in followup_store.items():
                created_at = getattr(v, "created_at", None)
                if created_at is None:
                    continue
                try:
                    created_dt = datetime.fromisoformat(created_at)
                    age_seconds = (utc_now - created_dt).total_seconds()
                    if age_seconds > ttl_seconds:
                        stale_keys.append(k)
                except (ValueError, TypeError):
                    continue
            for k in stale_keys:
                followup_store.pop(k, None)
            if stale_keys:
                logger.info("Pruned %d stale followup_store entries", len(stale_keys))

            # Prune pipeline_images (stored as (images, timestamp) tuples)
            pipeline_images: dict = getattr(app.state, "pipeline_images", {})
            stale_keys = [
                k
                for k, v in pipeline_images.items()
                if (isinstance(v, tuple) and len(v) == 2 and v[1] < cutoff)
            ]
            for k in stale_keys:
                pipeline_images.pop(k, None)
            if stale_keys:
                logger.info("Pruned %d stale pipeline_images entries", len(stale_keys))

            # Prune pending_graphs (stored as (graph, daemon, timestamp) tuples)
            pending_graphs: dict = getattr(app.state, "pending_graphs", {})
            lock = getattr(app.state, "pending_graphs_lock", None)
            if lock:
                async with lock:
                    stale_keys = [
                        k
                        for k, v in pending_graphs.items()
                        if (isinstance(v, tuple) and len(v) == 3 and v[2] < cutoff)
                    ]
                    for k in stale_keys:
                        pending_graphs.pop(k, None)
            else:
                stale_keys = [
                    k
                    for k, v in pending_graphs.items()
                    if (isinstance(v, tuple) and len(v) == 3 and v[2] < cutoff)
                ]
                for k in stale_keys:
                    pending_graphs.pop(k, None)
            if stale_keys:
                logger.info("Pruned %d stale pending_graphs entries", len(stale_keys))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if db is not None:
            await db.initialize()
        cleanup_task = asyncio.create_task(_cleanup_stale_stores(app))
        try:
            yield
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            if db is not None:
                await db.close()

    app = FastAPI(title="Forge", version="0.1.0", lifespan=lifespan)

    # Store on app.state
    app.state.jwt_secret = jwt_secret
    app.state.auth_disabled = auth_disabled
    app.state.db = db
    # Backward compat aliases — routes that use _get_forge_db still work
    app.state.forge_db = db
    app.state.pending_graphs = {}
    app.state.pending_graphs_lock = asyncio.Lock()
    app.state.rate_limit_store: dict[str, list[float]] = {}
    app.state.rate_limit_last_cleanup: float = 0.0

    # ── WebSocket connection manager ─────────────────────────────────
    from forge.api.ws.manager import ConnectionManager

    app.state.ws_manager = ConnectionManager()

    # ── Provider registry + cost registry ─────────────────────────────
    from forge.config.settings import ForgeSettings as _RegistrySettings
    from forge.core.cost_registry import CostRegistry
    from forge.core.provider_config import build_provider_registry, build_settings_for_project

    _reg_settings = _RegistrySettings()
    registry = build_provider_registry(_reg_settings)

    app.state.registry = registry
    app.state.cost_registry = CostRegistry(
        overrides=_reg_settings.build_cost_registry_overrides(),
    )

    def daemon_factory(
        project_path: str,
        model_strategy: str,
        *,
        user_settings: dict | None = None,
        provider_config: str | dict | None = None,
    ):
        """Create a ForgeDaemon + EventEmitter pair for a pipeline."""
        from forge.core.daemon import ForgeDaemon
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        settings, _project_config = build_settings_for_project(
            project_path,
            user_settings=user_settings,
            model_strategy=model_strategy,
            provider_config=provider_config,
        )

        daemon = ForgeDaemon(project_path, settings=settings, event_emitter=emitter)
        return daemon, emitter

    app.state.daemon_factory = daemon_factory

    # CORS -- configurable via FORGE_CORS_ORIGINS env var
    cors_origins_str = os.environ.get("FORGE_CORS_ORIGINS", "http://localhost:3000")
    cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]

    # Reject wildcard '*' when credentials are enabled (browsers block this)
    if "*" in cors_origins:
        logger.warning(
            "CORS origin '*' is not allowed with allow_credentials=True — "
            "removing wildcard. Set specific origins via FORGE_CORS_ORIGINS."
        )
        cors_origins = [o for o in cors_origins if o != "*"]
        if not cors_origins:
            cors_origins = ["http://localhost:3000"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # ── Security headers middleware ────────────────────────────────────
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next) -> Response:  # noqa: ANN001
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response

    # ── GitHub webhook settings (from env) ──────────────────────────
    from forge.config.settings import ForgeSettings as _ForgeSettings

    _webhook_settings = _ForgeSettings()
    app.state.github_webhook_secret = _webhook_settings.github_webhook_secret
    app.state.github_allowed_repos = _webhook_settings.github_allowed_repos
    app.state.webhook_project_dir = _webhook_settings.github_webhook_project_dir or None

    # ── Routers (all under /api prefix) ──────────────────────────────
    from forge.api.routes.auth import router as auth_router
    from forge.api.routes.diff import router as diff_router
    from forge.api.routes.followup import router as followup_router
    from forge.api.routes.github import router as github_router
    from forge.api.routes.history import router as history_router
    from forge.api.routes.providers import router as providers_router
    from forge.api.routes.settings import router as settings_router
    from forge.api.routes.tasks import router as tasks_router
    from forge.api.routes.templates import router as templates_router
    from forge.api.routes.webhooks import router as webhooks_router

    app.include_router(auth_router, prefix="/api")
    app.include_router(tasks_router, prefix="/api/tasks")
    app.include_router(followup_router, prefix="/api/tasks")
    app.include_router(diff_router, prefix="/api")
    app.include_router(history_router, prefix="/api")
    app.include_router(github_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")
    app.include_router(webhooks_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")

    # ── WebSocket endpoint ─────────────────────────────────────────
    from forge.api.ws.handler import websocket_endpoint

    @app.websocket("/api/ws/{pipeline_id}")
    async def ws_route(websocket: WebSocket, pipeline_id: str) -> None:
        await websocket_endpoint(
            websocket,
            pipeline_id,
            manager=app.state.ws_manager,
            jwt_secret=app.state.jwt_secret,
        )

    # ── Health check ────────────────────────────────────────────────
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": app.version}

    # ── Serve built frontend (must be LAST — catch-all) ─────────────
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web", "out")
    if os.path.isdir(frontend_dir):
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
