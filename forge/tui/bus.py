"""TUI Event Bus — routes events from daemon to UI subscribers.

Two sources:
  - EmbeddedSource: bridges daemon's EventEmitter (in-process mode)
  - ClientSource: receives events over WebSocket (client mode) — added later

The bus itself is source-agnostic. Widgets subscribe to event types
and receive data dicts.
"""

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from forge.core.events import EventEmitter

logger = logging.getLogger("forge.tui.bus")

TUI_EVENT_TYPES = [
    "pipeline:phase_changed",
    "pipeline:plan_ready",
    "pipeline:cost_update",
    "pipeline:cost_estimate",
    "pipeline:budget_exceeded",
    "pipeline:contracts_ready",
    "pipeline:contracts_failed",
    "pipeline:cancelled",
    "pipeline:restarted",
    "pipeline:paused",
    "pipeline:resumed",
    "pipeline:pr_created",
    "pipeline:pr_failed",
    "pipeline:worktrees_cleaned",
    "pipeline:preflight_failed",
    "pipeline:error",
    "pipeline:branch_resolved",
    "task:state_changed",
    "task:agent_output",
    "task:files_changed",
    "task:review_update",
    "task:merge_result",
    "task:cost_update",
    "task:awaiting_approval",
    "planner:output",
    "contracts:output",
    "followup:task_started",
    "followup:task_completed",
    "followup:task_error",
    "followup:agent_output",
    "task:question",
    "task:answer",
    "planning:question",
    "planning:answer",
    "planning:scout",
    "planning:architect",
    "planning:detailer",
    "planning:validator",
    "task:resumed",
    "task:auto_decided",
    "task:interjection",
    "pipeline:all_tasks_done",
    "pipeline:interrupted",
    "pipeline:pr_creating",
    "review:gate_started",
    "review:gate_passed",
    "review:gate_failed",
    "review:llm_feedback",
    "review:llm_output",
    "slot:acquired",
    "slot:released",
    "slot:queued",
]


class EventBus:
    """Source-agnostic event bus for TUI widgets."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event_type: str, data: Any = None) -> None:
        for handler in self._handlers.get(event_type, []):
            try:
                await handler(data)
            except Exception:
                logger.exception("Handler error for %r", event_type)


class EmbeddedSource:
    """Bridges daemon's EventEmitter to the TUI EventBus."""

    def __init__(self, emitter: EventEmitter, bus: EventBus) -> None:
        self._emitter = emitter
        self._bus = bus
        self._connected = False
        self._bridge_handlers: dict[str, Callable] = {}

    def connect(self) -> None:
        if self._connected:
            return
        for event_type in TUI_EVENT_TYPES:
            async def _bridge(data: Any, _type: str = event_type) -> None:
                await self._bus.emit(_type, data)
            self._bridge_handlers[event_type] = _bridge
            self._emitter.on(event_type, _bridge)
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        for event_type, handler in self._bridge_handlers.items():
            handlers = self._emitter._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
        self._bridge_handlers.clear()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected


class ClientSource:
    """Receives events from a running Forge server over WebSocket.

    Used in client mode when a Forge server is already running.
    Message format from server: {"type": "event_type", ...payload}
    """

    def __init__(self, ws_url: str, bus: EventBus, *, token: str) -> None:
        self._ws_url = ws_url
        self._bus = bus
        self._token = token
        self._connected = False
        self._authenticated = False
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Start WebSocket connection in background."""
        self._task = asyncio.create_task(self._listen())

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._task:
            self._task.cancel()
            self._task = None
        self._connected = False
        self._authenticated = False

    async def _listen(self) -> None:
        """WebSocket listen loop."""
        try:
            import websockets
            async with websockets.connect(self._ws_url) as ws:
                self._connected = True
                await ws.send(json.dumps({"token": self._token}))
                async for message in ws:
                    await self._handle_message(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("WebSocket error: %s", e)
            await self._bus.emit("pipeline:error", {"error": f"WebSocket disconnected: {e}"})
        finally:
            self._connected = False

    async def _handle_message(self, raw: str) -> None:
        """Parse and route a WebSocket message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from WebSocket: %s", raw[:100])
            return

        msg_type = msg.pop("type", None)
        if not msg_type:
            return

        if msg_type == "auth_ok":
            self._authenticated = True
            logger.info("WebSocket authenticated as %s", msg.get("user_id"))
            return

        await self._bus.emit(msg_type, msg)

    @property
    def connected(self) -> bool:
        return self._connected
