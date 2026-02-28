"""FastAPI application factory for the Forge web UI."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def create_app(
    *,
    db_url: str | None = None,
    jwt_secret: str | None = None,
    forge_db_url: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_url: Async database URL (e.g. ``sqlite+aiosqlite:///forge.db``).
            If provided, an async engine and sessionmaker are attached to
            ``app.state`` and tables are created on startup.
        jwt_secret: Secret key used for JWT token signing.
            If not provided, reads from ``FORGE_JWT_SECRET`` env var.
            Falls back to a random secret (tokens won't survive restarts).
        forge_db_url: Async database URL for Forge pipeline data.
            If provided, the pipeline DB is initialized on startup.
            Separate from the user auth DB to avoid coupling.
    """
    if jwt_secret is None:
        jwt_secret = os.environ.get("FORGE_JWT_SECRET", "")
        if not jwt_secret:
            import secrets

            jwt_secret = secrets.token_urlsafe(32)
            logging.getLogger(__name__).warning(
                "No FORGE_JWT_SECRET set — using random secret (tokens won't survive restarts)"
            )
    engine = None
    session_factory = None

    if db_url is not None:
        engine = create_async_engine(db_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ── Forge pipeline DB (separate from user auth DB) ─────────────
    from forge.storage.db import Database as ForgeDB

    forge_db = ForgeDB(forge_db_url) if forge_db_url is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: create tables if we have a DB
        if engine is not None:
            from forge.api.models.user import Base

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        # Initialize forge pipeline DB
        if forge_db is not None:
            await forge_db.initialize()
        yield
        # Shutdown: dispose engines
        if forge_db is not None:
            await forge_db.close()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Forge", version="0.1.0", lifespan=lifespan)

    # Store jwt_secret on app state for use by auth service
    app.state.jwt_secret = jwt_secret

    # ── WebSocket connection manager ─────────────────────────────────
    from forge.api.ws.manager import ConnectionManager

    app.state.ws_manager = ConnectionManager()

    # Attach DB objects to app.state (if provided)
    if engine is not None:
        app.state.async_engine = engine
        app.state.async_session = session_factory

    # Attach forge pipeline DB and daemon factory
    app.state.forge_db = forge_db
    app.state.pending_graphs = {}

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

    # CORS -- allow the React dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers (all under /api prefix) ──────────────────────────────
    from forge.api.routes.auth import router as auth_router
    from forge.api.routes.diff import router as diff_router
    from forge.api.routes.github import router as github_router
    from forge.api.routes.history import router as history_router
    from forge.api.routes.settings import router as settings_router
    from forge.api.routes.tasks import router as tasks_router
    from forge.api.routes.templates import router as templates_router

    app.include_router(auth_router, prefix="/api")
    app.include_router(tasks_router, prefix="/api/tasks")
    app.include_router(diff_router, prefix="/api")
    app.include_router(history_router, prefix="/api")
    app.include_router(github_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")

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
