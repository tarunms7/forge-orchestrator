"""Lightweight async EventEmitter for daemon lifecycle events."""

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("forge.events")


class EventEmitter:
    """Simple pub/sub event emitter with async handlers.

    Usage::

        emitter = EventEmitter()
        emitter.on("pipeline:started", my_async_handler)
        await emitter.emit("pipeline:started", {"pipeline_id": "abc"})

    Handlers are async callables that receive a single *data* argument.
    Multiple handlers can be registered for the same event and are
    called in registration order.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable) -> None:
        """Register an async handler for *event*."""
        self._handlers[event].append(handler)

    async def emit(self, event: str, data: Any = None) -> None:
        """Invoke all handlers registered for *event* with *data*."""
        for handler in self._handlers.get(event, []):
            try:
                await handler(data)
            except Exception:
                logger.exception("Error in handler for event %r", event)
