"""WebSocket endpoint handler for real-time pipeline updates."""

from __future__ import annotations

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

    Authenticates the client via a JWT token passed as a ``token`` query
    parameter, registers the connection with the :class:`ConnectionManager`,
    and keeps the connection alive until the client disconnects.

    Args:
        websocket: The incoming WebSocket connection.
        pipeline_id: Pipeline to subscribe to (from URL path).
        manager: Shared ConnectionManager instance.
        jwt_secret: Secret used to verify JWT tokens.
    """
    # ── Auth via query-param token ───────────────────────────────────
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        payload = decode_token(token, secret=jwt_secret)
        user_id = payload["sub"]
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # ── Accept & register ────────────────────────────────────────────
    await manager.connect(websocket, user_id=user_id, pipeline_id=pipeline_id)

    try:
        while True:
            # Keep connection alive; ignore client messages for now
            await websocket.receive_json()
    except WebSocketDisconnect:
        manager.disconnect(websocket, pipeline_id=pipeline_id)
