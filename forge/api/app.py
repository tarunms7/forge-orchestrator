"""FastAPI application factory for the Forge web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def create_app(
    *,
    db_url: str | None = None,
    jwt_secret: str = "dev-secret-change-me",
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_url: Async database URL (e.g. ``sqlite+aiosqlite:///forge.db``).
            If provided, an async engine and sessionmaker are attached to
            ``app.state`` and tables are created on startup.
        jwt_secret: Secret key used for JWT token signing.
    """
    engine = None
    session_factory = None

    if db_url is not None:
        engine = create_async_engine(db_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: create tables if we have a DB
        if engine is not None:
            from forge.api.models.user import Base

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        yield
        # Shutdown: dispose engine
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

    # CORS -- allow the React dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ─────────────────────────────────────────────────────
    from forge.api.routes.auth import router as auth_router

    app.include_router(auth_router)

    # ── WebSocket endpoint ─────────────────────────────────────────
    from forge.api.ws.handler import websocket_endpoint

    @app.websocket("/ws/{pipeline_id}")
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

    return app
