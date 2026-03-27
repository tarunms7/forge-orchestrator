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
        self._failed_count: int = 0

    def on(self, event: str, handler: Callable) -> None:
        """Register an async handler for *event*."""
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Callable) -> None:
        """Remove a previously registered handler for *event*.

        Safe to call with a handler that was never registered (no-op).
        """
        try:
            self._handlers[event].remove(handler)
        except (ValueError, KeyError):
            pass

    def clear(self, event: str | None = None) -> None:
        """Clear registered handlers.

        If *event* is provided, clears only handlers for that event.
        If *event* is None, clears all handlers for all events.
        """
        if event is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event, None)

    async def emit(self, event: str, data: Any = None) -> None:
        """Invoke all handlers registered for *event* with *data*."""
        for handler in self._handlers.get(event, []):
            try:
                await handler(data)
            except Exception:
                self._failed_count += 1
                logger.exception("Error in handler for event %r", event)

    def failed_count(self) -> int:
        """Return the number of handler invocations that raised exceptions."""
        return self._failed_count
