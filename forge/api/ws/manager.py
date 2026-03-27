"""WebSocket connection manager for real-time pipeline updates."""

import json
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("forge.ws")

MAX_CONNECTIONS_PER_USER = 10


class ConnectionManager:
    """Manages WebSocket connections grouped by pipeline_id.

    Each pipeline can have multiple connected clients. Messages are
    broadcast to all clients watching a given pipeline. Dead connections
    are automatically pruned during broadcast.

    Per-user connection limit: at most ``MAX_CONNECTIONS_PER_USER``
    concurrent WebSocket connections are allowed per user_id.
    """

    def __init__(self) -> None:
        # pipeline_id -> list of WebSocket connections
        self.active_connections: dict[str, list[Any]] = defaultdict(list)
        # user_id -> set of WebSocket connections (for per-user limiting)
        self._user_connections: dict[str, set[Any]] = defaultdict(set)

    def _user_connection_count(self, user_id: str) -> int:
        """Return the number of active connections for a user."""
        return len(self._user_connections.get(user_id, set()))

    async def connect(self, websocket: Any, *, user_id: str, pipeline_id: str) -> bool:
        """Accept a WebSocket connection and register it for a pipeline.

        This is the primary entry point when the caller controls the accept
        lifecycle.  For already-accepted sockets (e.g. handler-side auth),
        use ``register()`` instead.

        Returns:
            True if the connection was accepted, False if rejected due to
            the per-user connection limit.
        """
        if self._user_connection_count(user_id) >= MAX_CONNECTIONS_PER_USER:
            await websocket.close(code=4002, reason="Too many connections")
            logger.warning(
                "WS rejected: user=%s pipeline=%s (limit=%d)",
                user_id,
                pipeline_id,
                MAX_CONNECTIONS_PER_USER,
            )
            return False
        await websocket.accept()
        if websocket not in self.active_connections[pipeline_id]:
            self.active_connections[pipeline_id].append(websocket)
        self._user_connections[user_id].add(websocket)
        logger.info("WS connected: user=%s pipeline=%s", user_id, pipeline_id)
        return True

    def register(self, websocket: Any, *, user_id: str, pipeline_id: str) -> None:
        """Register an already-accepted WebSocket for a pipeline.

        Use this when the WebSocket was accepted externally (e.g. the
        handler accepted it for auth negotiation).  For the full
        accept-and-register flow, use ``connect()`` instead.
        """
        if websocket not in self.active_connections[pipeline_id]:
            self.active_connections[pipeline_id].append(websocket)
        self._user_connections[user_id].add(websocket)
        logger.info("WS registered: user=%s pipeline=%s", user_id, pipeline_id)

    def disconnect(self, websocket: Any, *, pipeline_id: str, user_id: str | None = None) -> None:
        """Remove a WebSocket connection from a pipeline's list."""
        conns = self.active_connections.get(pipeline_id, [])
        while websocket in conns:
            conns.remove(websocket)
            logger.info("WS disconnected: pipeline=%s", pipeline_id)
        # Remove from user tracking (scan all users if user_id not provided)
        if user_id:
            self._user_connections.get(user_id, set()).discard(websocket)
        else:
            for uid_conns in self._user_connections.values():
                uid_conns.discard(websocket)

    async def broadcast(self, pipeline_id: str, message: dict) -> None:
        """Send a JSON message to all connections for a pipeline.

        Dead connections (those that raise on send) are automatically
        removed from the active list.
        """
        conns = self.active_connections.get(pipeline_id)
        if not conns:
            return

        payload = json.dumps(message)
        dead: list[Any] = []

        # Iterate over a snapshot to avoid mutation during iteration
        # (disconnect() or concurrent broadcast() may modify the list)
        for ws in list(conns):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
                logger.debug("Pruning dead WS connection for pipeline=%s", pipeline_id)

        for ws in dead:
            try:
                conns.remove(ws)
            except ValueError:
                pass  # Already removed by concurrent disconnect()
            # Also remove from user connection tracking to prevent lockout
            for uid_conns in self._user_connections.values():
                uid_conns.discard(ws)
