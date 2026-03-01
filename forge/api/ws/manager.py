"""WebSocket connection manager for real-time pipeline updates."""

import json
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("forge.ws")


class ConnectionManager:
    """Manages WebSocket connections grouped by pipeline_id.

    Each pipeline can have multiple connected clients. Messages are
    broadcast to all clients watching a given pipeline. Dead connections
    are automatically pruned during broadcast.
    """

    def __init__(self) -> None:
        # pipeline_id -> list of WebSocket connections
        self.active_connections: dict[str, list[Any]] = defaultdict(list)

    async def connect(self, websocket: Any, *, user_id: str, pipeline_id: str) -> None:
        """Accept a WebSocket connection and register it for a pipeline."""
        await websocket.accept()
        self.active_connections[pipeline_id].append(websocket)
        logger.info("WS connected: user=%s pipeline=%s", user_id, pipeline_id)

    def register(self, websocket: Any, *, user_id: str, pipeline_id: str) -> None:
        """Register an already-accepted WebSocket connection for a pipeline."""
        self.active_connections[pipeline_id].append(websocket)
        logger.info("WS registered: user=%s pipeline=%s", user_id, pipeline_id)

    def disconnect(self, websocket: Any, *, pipeline_id: str) -> None:
        """Remove a WebSocket connection from a pipeline's list."""
        conns = self.active_connections.get(pipeline_id, [])
        if websocket in conns:
            conns.remove(websocket)
            logger.info("WS disconnected: pipeline=%s", pipeline_id)

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
