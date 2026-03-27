"""WebSocket endpoint handler for real-time pipeline updates."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from forge.api.security.jwt import decode_token
from forge.api.ws.manager import ConnectionManager

logger = logging.getLogger(__name__)

# Heartbeat interval (seconds) — server sends a ping to detect dead clients
HEARTBEAT_INTERVAL = 30
# Receive timeout (seconds) — if no message (including pong) within this window,
# the connection is considered dead
RECEIVE_TIMEOUT = 60


async def _safe_close(websocket: WebSocket, code: int = 1000, reason: str = "") -> None:
    """Close a WebSocket without raising on already-disconnected sockets."""
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        # Socket already closed/disconnected — nothing to do
        pass


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

    A heartbeat ping is sent every ``HEARTBEAT_INTERVAL`` seconds.  If
    no message is received within ``RECEIVE_TIMEOUT`` seconds the
    connection is considered dead and closed.

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
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=3.0)
        token = auth_message.get("token")
        if not token:
            await _safe_close(websocket, code=4001, reason="Missing token")
            return
        payload = decode_token(token, secret=jwt_secret)
        user_id = payload["sub"]
    except TimeoutError:
        await _safe_close(websocket, code=4001, reason="Auth timeout")
        return
    except WebSocketDisconnect:
        # Client disconnected during auth — nothing to close
        logger.debug("WS client disconnected during auth for pipeline %s", pipeline_id)
        return
    except Exception:
        await _safe_close(websocket, code=4001, reason="Invalid token")
        return

    # ── Register as authenticated ────────────────────────────────────
    manager.register(websocket, user_id=user_id, pipeline_id=pipeline_id)
    try:
        await websocket.send_json({"type": "auth_ok", "user_id": user_id})
    except Exception:
        manager.disconnect(websocket, pipeline_id=pipeline_id)
        return

    try:
        missed_heartbeats = 0
        max_missed = max(1, RECEIVE_TIMEOUT // HEARTBEAT_INTERVAL)  # ≥1 to avoid instant kill
        while True:
            try:
                # Wait for a client message; timeout at HEARTBEAT_INTERVAL
                # so we can send proactive pings on that cadence.
                await asyncio.wait_for(
                    websocket.receive_json(), timeout=HEARTBEAT_INTERVAL
                )
                missed_heartbeats = 0  # Got a message — connection alive
            except TimeoutError:
                # No message within the heartbeat window — send a ping.
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break  # Connection dead — can't even send
                missed_heartbeats += 1
                if missed_heartbeats >= max_missed:
                    # No client message for RECEIVE_TIMEOUT seconds total
                    break
            except WebSocketDisconnect:
                break
    except Exception:
        # Catch any unexpected transport errors
        pass
    finally:
        manager.disconnect(websocket, pipeline_id=pipeline_id, user_id=user_id)
