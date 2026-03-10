"""Tests for TuiState."""

import pytest
from forge.tui.state import TuiState


def test_initial_state():
    state = TuiState()
    assert state.phase == "idle"
    assert state.tasks == {}
    assert state.selected_task_id is None
    assert state.total_cost_usd == 0.0
    assert state.pipeline_id is None


def test_apply_phase_changed():
    state = TuiState()
    state.apply_event("pipeline:phase_changed", {"phase": "planning"})
    assert state.phase == "planning"


def test_apply_plan_ready_populates_tasks():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [
            {"id": "t1", "title": "Setup DB", "description": "...", "files": ["db.py"], "depends_on": [], "complexity": "low"},
            {"id": "t2", "title": "Add API", "description": "...", "files": ["api.py"], "depends_on": ["t1"], "complexity": "medium"},
        ]
    })
    assert len(state.tasks) == 2
    assert state.tasks["t1"]["title"] == "Setup DB"
    assert state.tasks["t1"]["state"] == "todo"
    assert state.selected_task_id == "t1"


def test_apply_task_state_changed():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "in_progress"})
    assert state.tasks["t1"]["state"] == "in_progress"


def test_apply_agent_output_appends():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Creating file..."})
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Done."})
    assert state.agent_output["t1"] == ["Creating file...", "Done."]


def test_agent_output_ring_buffer():
    state = TuiState(max_output_lines=5)
    for i in range(10):
        state.apply_event("task:agent_output", {"task_id": "t1", "line": f"line {i}"})
    assert len(state.agent_output["t1"]) == 5
    assert state.agent_output["t1"][0] == "line 5"


def test_apply_cost_update():
    state = TuiState()
    state.apply_event("pipeline:cost_update", {"total_cost_usd": 1.23})
    assert state.total_cost_usd == 1.23


def test_apply_task_cost_update():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.apply_event("task:cost_update", {"task_id": "t1", "agent_cost": 0.5})
    assert state.tasks["t1"]["agent_cost"] == 0.5


def test_on_change_callback():
    state = TuiState()
    changes = []
    state.on_change(lambda field: changes.append(field))
    state.apply_event("pipeline:phase_changed", {"phase": "executing"})
    assert "phase" in changes


def test_task_counts():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [
            {"id": "t1", "title": "A", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"},
            {"id": "t2", "title": "B", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"},
            {"id": "t3", "title": "C", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"},
        ]
    })
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "done"})
    state.apply_event("task:state_changed", {"task_id": "t2", "state": "in_progress"})
    assert state.done_count == 1
    assert state.total_count == 3
    assert state.progress_pct == pytest.approx(33.3, abs=1)
