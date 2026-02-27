"""WebSocket endpoint handler for real-time pipeline updates."""

from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from forge.api.security.jwt import decode_token
from forge.api.ws.manager import ConnectionManager


async def websocket_endpoint(
    websocket: WebSocket,
    pipeline_id: str,
    manager: ConnectionManager,
    jwt_secret: str,
) -> None:
    """Handle a WebSocket connection for pipeline events.

    Accepts the connection first, then authenticates the client via a JWT
    token sent in the first JSON message (``{"token": "..."}``).  This
    avoids exposing the token in URL query parameters (server logs,
    browser history, etc.).

    Args:
        websocket: The incoming WebSocket connection.
        pipeline_id: Pipeline to subscribe to (from URL path).
        manager: Shared ConnectionManager instance.
        jwt_secret: Secret used to verify JWT tokens.
    """
    # ── Accept connection first (unauthenticated) ────────────────────
    await websocket.accept()

    # ── Read auth from the first message ─────────────────────────────
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        token = auth_message.get("token")
        if not token:
            await websocket.close(code=4001, reason="Missing token")
            return
        payload = decode_token(token, secret=jwt_secret)
        user_id = payload["sub"]
    except asyncio.TimeoutError:
        await websocket.close(code=4001, reason="Auth timeout")
        return
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # ── Register as authenticated ────────────────────────────────────
    manager.register(websocket, user_id=user_id, pipeline_id=pipeline_id)
    await websocket.send_json({"type": "auth_ok", "user_id": user_id})

    try:
        while True:
            # Keep connection alive; ignore client messages for now
            await websocket.receive_json()
    except WebSocketDisconnect:
        manager.disconnect(websocket, pipeline_id=pipeline_id)
