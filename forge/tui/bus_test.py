"""Tests for the TUI event bus."""

import pytest
from forge.tui.bus import EventBus, EmbeddedSource


@pytest.mark.asyncio
async def test_bus_subscribe_and_receive():
    bus = EventBus()
    received = []
    async def handler(data):
        received.append(data)
    bus.subscribe("task:state_changed", handler)
    await bus.emit("task:state_changed", {"task_id": "t1", "state": "done"})
    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_bus_unsubscribe():
    bus = EventBus()
    received = []
    async def handler(data):
        received.append(data)
    bus.subscribe("test:event", handler)
    await bus.emit("test:event", {"n": 1})
    bus.unsubscribe("test:event", handler)
    await bus.emit("test:event", {"n": 2})
    assert len(received) == 1


@pytest.mark.asyncio
async def test_bus_multiple_event_types():
    bus = EventBus()
    a_events, b_events = [], []
    async def handler_a(data): a_events.append(data)
    async def handler_b(data): b_events.append(data)
    bus.subscribe("type_a", handler_a)
    bus.subscribe("type_b", handler_b)
    await bus.emit("type_a", {"x": 1})
    await bus.emit("type_b", {"x": 2})
    assert len(a_events) == 1
    assert len(b_events) == 1


@pytest.mark.asyncio
async def test_bus_handler_error_does_not_crash():
    bus = EventBus()
    received = []
    async def bad_handler(data): raise RuntimeError("boom")
    async def good_handler(data): received.append(data)
    bus.subscribe("evt", bad_handler)
    bus.subscribe("evt", good_handler)
    await bus.emit("evt", {"ok": True})
    assert len(received) == 1


@pytest.mark.asyncio
async def test_embedded_source_bridges_emitter():
    from forge.core.events import EventEmitter
    emitter = EventEmitter()
    bus = EventBus()
    source = EmbeddedSource(emitter, bus)
    received = []
    async def handler(data): received.append(data)
    bus.subscribe("task:state_changed", handler)
    source.connect()
    await emitter.emit("task:state_changed", {"task_id": "t1", "state": "done"})
    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_review_llm_output_in_tui_event_types():
    """review:llm_output must be in TUI_EVENT_TYPES for bridging."""
    from forge.tui.bus import TUI_EVENT_TYPES
    assert "review:llm_output" in TUI_EVENT_TYPES


@pytest.mark.asyncio
async def test_embedded_source_bridges_review_llm_output():
    """EmbeddedSource bridges review:llm_output events."""
    from forge.core.events import EventEmitter
    emitter = EventEmitter()
    bus = EventBus()
    source = EmbeddedSource(emitter, bus)
    received = []
    async def handler(data): received.append(data)
    bus.subscribe("review:llm_output", handler)
    source.connect()
    await emitter.emit("review:llm_output", {"task_id": "t1", "line": "Reviewing..."})
    assert len(received) == 1
    assert received[0]["line"] == "Reviewing..."


def test_tui_event_types_no_duplicates():
    """TUI_EVENT_TYPES must not contain duplicate entries."""
    from forge.tui.bus import TUI_EVENT_TYPES
    assert len(TUI_EVENT_TYPES) == len(set(TUI_EVENT_TYPES)), (
        f"Duplicates found: {[e for e in TUI_EVENT_TYPES if TUI_EVENT_TYPES.count(e) > 1]}"
    )


@pytest.mark.asyncio
async def test_embedded_source_disconnect():
    from forge.core.events import EventEmitter
    emitter = EventEmitter()
    bus = EventBus()
    source = EmbeddedSource(emitter, bus)
    received = []
    async def handler(data): received.append(data)
    bus.subscribe("task:state_changed", handler)
    source.connect()
    source.disconnect()
    await emitter.emit("task:state_changed", {"task_id": "t1", "state": "done"})
    assert len(received) == 0
