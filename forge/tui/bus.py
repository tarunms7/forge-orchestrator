"""TUI Event Bus — routes events from daemon to UI subscribers.

Two sources:
  - EmbeddedSource: bridges daemon's EventEmitter (in-process mode)
  - ClientSource: receives events over WebSocket (client mode) — added later

The bus itself is source-agnostic. Widgets subscribe to event types
and receive data dicts.
"""

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
    "pipeline:error",
    "task:state_changed",
    "task:agent_output",
    "task:files_changed",
    "task:review_update",
    "task:merge_result",
    "task:cost_update",
    "task:awaiting_approval",
    "planner:output",
    "contracts:output",
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
