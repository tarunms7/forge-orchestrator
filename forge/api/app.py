"""FastAPI application factory for the Forge web UI."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware


def create_app(
    *,
    db_url: str | None = None,
    jwt_secret: str | None = None,
    # Kept for backward compat — ignored, uses db_url for everything
    forge_db_url: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_url: Async database URL (e.g. ``sqlite+aiosqlite:///forge.db``).
            Single DB for auth + pipelines + tasks.
        jwt_secret: Secret key used for JWT token signing.
        forge_db_url: **Deprecated** — ignored. All data uses ``db_url``.
    """
    if jwt_secret is None:
        jwt_secret = os.environ.get("FORGE_JWT_SECRET", "")
        if not jwt_secret:
            import secrets

            jwt_secret = secrets.token_urlsafe(32)
            logging.getLogger(__name__).warning(
                "No FORGE_JWT_SECRET set — using random secret (tokens won't survive restarts). "
                "Set FORGE_JWT_SECRET env var for production: "
                "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )

    # ── Single unified database ─────────────────────────────────────
    from forge.storage.db import Database

    db = Database(db_url) if db_url is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if db is not None:
            await db.initialize()
        yield
        if db is not None:
            await db.close()

    app = FastAPI(title="Forge", version="0.1.0", lifespan=lifespan)

    # Store on app.state
    app.state.jwt_secret = jwt_secret
    app.state.db = db
    # Backward compat aliases — routes that use _get_forge_db still work
    app.state.forge_db = db
    app.state.pending_graphs = {}
    app.state.pending_graphs_lock = asyncio.Lock()

    # ── WebSocket connection manager ─────────────────────────────────
    from forge.api.ws.manager import ConnectionManager

    app.state.ws_manager = ConnectionManager()

    def daemon_factory(project_path: str, model_strategy: str):
        """Create a ForgeDaemon + EventEmitter pair for a pipeline."""
        from forge.config.settings import ForgeSettings
        from forge.core.daemon import ForgeDaemon
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        settings = ForgeSettings()
        settings.model_strategy = model_strategy
        daemon = ForgeDaemon(project_path, settings=settings, event_emitter=emitter)
        return daemon, emitter

    app.state.daemon_factory = daemon_factory

    # CORS -- configurable via FORGE_CORS_ORIGINS env var
    cors_origins_str = os.environ.get("FORGE_CORS_ORIGINS", "http://localhost:3000")
    cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

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
