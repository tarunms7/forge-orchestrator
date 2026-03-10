"""Tests for EventEmitter."""

import pytest
from unittest.mock import AsyncMock



class TestEventEmitter:
    """Tests for the EventEmitter class."""

    async def test_on_registers_handler(self):
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.on("test_event", handler)

        assert handler in emitter._handlers["test_event"]

    async def test_emit_calls_registered_handler(self):
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.on("build_done", handler)

        await emitter.emit("build_done", {"status": "ok"})

        handler.assert_awaited_once_with({"status": "ok"})

    async def test_emit_calls_multiple_handlers(self):
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        h1 = AsyncMock()
        h2 = AsyncMock()
        emitter.on("progress", h1)
        emitter.on("progress", h2)

        data = {"percent": 50}
        await emitter.emit("progress", data)

        h1.assert_awaited_once_with(data)
        h2.assert_awaited_once_with(data)

    async def test_emit_unregistered_event_is_noop(self):
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        # Should not raise
        await emitter.emit("nonexistent", {"foo": "bar"})

    async def test_different_events_are_isolated(self):
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        h_start = AsyncMock()
        h_stop = AsyncMock()
        emitter.on("start", h_start)
        emitter.on("stop", h_stop)

        await emitter.emit("start", {"id": 1})

        h_start.assert_awaited_once_with({"id": 1})
        h_stop.assert_not_awaited()

    async def test_emit_with_none_data(self):
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.on("ping", handler)

        await emitter.emit("ping", None)

        handler.assert_awaited_once_with(None)

    async def test_on_same_handler_twice(self):
        """Registering the same handler twice should call it twice."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.on("event", handler)
        emitter.on("event", handler)

        await emitter.emit("event", "data")

        assert handler.await_count == 2


@pytest.mark.asyncio
async def test_failed_count_increments_on_handler_error():
    from forge.core.events import EventEmitter

    emitter = EventEmitter()

    async def bad_handler(data):
        raise RuntimeError("boom")

    emitter.on("test", bad_handler)
    await emitter.emit("test", {})
    assert emitter.failed_count() == 1
    await emitter.emit("test", {})
    assert emitter.failed_count() == 2
