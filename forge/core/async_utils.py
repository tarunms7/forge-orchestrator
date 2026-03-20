"""Safe asyncio task creation with automatic exception logging."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


def safe_create_task(
    coro: Coroutine[Any, Any, Any],
    *,
    logger: logging.Logger | None = None,
    name: str | None = None,
) -> asyncio.Task:
    """Create an asyncio task with automatic exception logging."""
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(lambda t: _log_task_exception(t, logger))
    return task


def _log_task_exception(task: asyncio.Task, logger: logging.Logger | None) -> None:
    """Log exceptions from completed tasks, ignoring cancellations."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log = logger or logging.getLogger("forge")
        log.error("Background task %r failed: %s", task.get_name(), exc, exc_info=exc)
