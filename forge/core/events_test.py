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

    async def test_off_removes_handler(self):
        """off() removes handler so subsequent emit doesn't call it."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.on("task:answer", handler)
        emitter.off("task:answer", handler)

        await emitter.emit("task:answer", {"answer": "yes"})

        handler.assert_not_awaited()

    async def test_off_nonexistent_handler_no_error(self):
        """off() with a handler that was never registered does not raise."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        # Should not raise
        emitter.off("task:answer", handler)

    async def test_off_nonexistent_event_no_error(self):
        """off() for an event that has no handlers does not raise."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.off("never:registered", handler)

    async def test_off_only_removes_one_registration(self):
        """off() removes only one instance when handler registered twice."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        handler = AsyncMock()
        emitter.on("event", handler)
        emitter.on("event", handler)
        emitter.off("event", handler)

        await emitter.emit("event", "data")

        assert handler.await_count == 1

    async def test_clear_removes_all_handlers(self):
        """clear() with no argument removes all handlers for all events."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        h1 = AsyncMock()
        h2 = AsyncMock()
        emitter.on("event_a", h1)
        emitter.on("event_b", h2)
        emitter.clear()

        await emitter.emit("event_a", {})
        await emitter.emit("event_b", {})

        h1.assert_not_awaited()
        h2.assert_not_awaited()

    async def test_clear_event_removes_specific_event_handlers(self):
        """clear(event) removes handlers only for that event."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        h1 = AsyncMock()
        h2 = AsyncMock()
        emitter.on("event_a", h1)
        emitter.on("event_b", h2)
        emitter.clear("event_a")

        await emitter.emit("event_a", {})
        await emitter.emit("event_b", {})

        h1.assert_not_awaited()
        h2.assert_awaited_once_with({})

    async def test_clear_nonexistent_event_no_error(self):
        """clear(event) for an event with no handlers does not raise."""
        from forge.core.events import EventEmitter

        emitter = EventEmitter()
        emitter.clear("never:registered")


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
