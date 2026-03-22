"""Integration test: event flow from EventEmitter to TuiState."""

import pytest

from forge.core.events import EventEmitter
from forge.tui.bus import TUI_EVENT_TYPES, EmbeddedSource, EventBus
from forge.tui.state import TuiState


@pytest.mark.asyncio
async def test_full_event_flow():
    """Events flow: EventEmitter → EmbeddedSource → EventBus → TuiState."""
    emitter = EventEmitter()
    bus = EventBus()
    state = TuiState()

    for evt_type in TUI_EVENT_TYPES:

        async def _handler(data, _type=evt_type):
            state.apply_event(_type, data)

        bus.subscribe(evt_type, _handler)

    source = EmbeddedSource(emitter, bus)
    source.connect()

    await emitter.emit("pipeline:phase_changed", {"phase": "planning"})
    assert state.phase == "planning"

    await emitter.emit(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Build API",
                    "description": "...",
                    "files": ["api.py"],
                    "depends_on": [],
                    "complexity": "medium",
                },
            ]
        },
    )
    assert len(state.tasks) == 1
    assert state.tasks["t1"]["title"] == "Build API"

    await emitter.emit("task:state_changed", {"task_id": "t1", "state": "in_progress"})
    assert state.tasks["t1"]["state"] == "in_progress"

    await emitter.emit("task:agent_output", {"task_id": "t1", "line": "Creating api.py..."})
    assert state.agent_output["t1"] == ["Creating api.py..."]

    await emitter.emit("pipeline:cost_update", {"total_cost_usd": 0.42})
    assert state.total_cost_usd == 0.42
