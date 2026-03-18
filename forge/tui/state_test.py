"""Tests for TuiState."""

import pytest
from forge.tui.state import TuiState


def _make_state_with_task(task_id="t1"):
    """Helper: create state with one task ready."""
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": task_id, "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    return state


def test_initial_state():
    state = TuiState()
    assert state.phase == "idle"
    assert state.tasks == {}
    assert state.selected_task_id is None
    assert state.total_cost_usd == 0.0
    assert state.pipeline_id is None


def test_initial_state_new_fields():
    state = TuiState()
    assert state.contracts_output == []
    assert state.contracts_ready is False
    assert state.contracts_failed is None
    assert state.cost_estimate is None
    assert state.budget_exceeded is False
    assert state.preflight_error is None
    assert state.followup_tasks == {}
    assert state.followup_output == {}


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


def test_initial_state_has_review_output_and_streaming():
    state = TuiState()
    assert state.review_output == {}
    assert state.streaming_task_ids == set()


def test_review_llm_output_appends():
    state = TuiState()
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "Checking scope..."})
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "Looks good."})
    assert state.review_output["t1"] == ["Checking scope...", "Looks good."]


def test_review_llm_output_ring_buffer():
    state = TuiState(max_output_lines=3)
    for i in range(5):
        state.apply_event("review:llm_output", {"task_id": "t1", "line": f"line {i}"})
    assert len(state.review_output["t1"]) == 3
    assert state.review_output["t1"][0] == "line 2"


def test_review_llm_output_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda field: changes.append(field))
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "x"})
    assert "review_output" in changes


def test_review_llm_output_adds_to_streaming_task_ids():
    state = TuiState()
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "x"})
    assert "t1" in state.streaming_task_ids


def test_agent_output_adds_to_streaming_task_ids():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    assert "t1" in state.streaming_task_ids


def test_state_changed_done_clears_streaming():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    assert "t1" in state.streaming_task_ids
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "done"})
    assert "t1" not in state.streaming_task_ids


def test_state_changed_error_clears_streaming():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "error"})
    assert "t1" not in state.streaming_task_ids


def test_review_llm_output_ignores_missing_task_id():
    state = TuiState()
    changes = []
    state.on_change(lambda field: changes.append(field))
    state.apply_event("review:llm_output", {"line": "no task_id"})
    assert "review_output" not in changes


# --- cost_estimate ---

def test_cost_estimate_range():
    state = TuiState()
    state.apply_event("pipeline:cost_estimate", {"min_usd": 1.0, "max_usd": 5.0})
    assert state.cost_estimate == {"min_usd": 1.0, "max_usd": 5.0}


def test_cost_estimate_legacy():
    state = TuiState()
    state.apply_event("pipeline:cost_estimate", {"estimated_cost": 3.5})
    assert state.cost_estimate == {"estimated_cost": 3.5}


def test_cost_estimate_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("pipeline:cost_estimate", {"min_usd": 1.0, "max_usd": 2.0})
    assert "cost_estimate" in changes


# --- budget_exceeded ---

def test_budget_exceeded():
    state = TuiState()
    state.apply_event("pipeline:budget_exceeded", {})
    assert state.budget_exceeded is True


def test_budget_exceeded_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("pipeline:budget_exceeded", {})
    assert "budget_exceeded" in changes


# --- contracts:output ---

def test_contracts_output_appends():
    state = TuiState()
    state.apply_event("contracts:output", {"line": "Building..."})
    state.apply_event("contracts:output", {"line": "Done."})
    assert state.contracts_output == ["Building...", "Done."]


def test_contracts_output_ring_buffer():
    state = TuiState(max_output_lines=3)
    for i in range(5):
        state.apply_event("contracts:output", {"line": f"line {i}"})
    assert len(state.contracts_output) == 3
    assert state.contracts_output[0] == "line 2"


def test_contracts_output_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("contracts:output", {"line": "x"})
    assert "contracts_output" in changes


def test_contracts_output_missing_line():
    state = TuiState()
    state.apply_event("contracts:output", {})
    assert state.contracts_output == [""]


# --- contracts_ready ---

def test_contracts_ready():
    state = TuiState()
    state.apply_event("pipeline:contracts_ready", {})
    assert state.contracts_ready is True


def test_contracts_ready_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("pipeline:contracts_ready", {})
    assert "contracts_ready" in changes


# --- contracts_failed ---

def test_contracts_failed():
    state = TuiState()
    state.apply_event("pipeline:contracts_failed", {"error": "timeout"})
    assert state.contracts_failed == "timeout"


def test_contracts_failed_default_error():
    state = TuiState()
    state.apply_event("pipeline:contracts_failed", {})
    assert state.contracts_failed == "Unknown"


def test_contracts_failed_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("pipeline:contracts_failed", {"error": "x"})
    assert "contracts_failed" in changes


# --- files_changed ---

def test_files_changed():
    state = _make_state_with_task("t1")
    state.apply_event("task:files_changed", {"task_id": "t1", "files": ["a.py", "b.py"]})
    assert state.tasks["t1"]["files_changed"] == ["a.py", "b.py"]


def test_files_changed_missing_files():
    state = _make_state_with_task("t1")
    state.apply_event("task:files_changed", {"task_id": "t1"})
    assert state.tasks["t1"]["files_changed"] == []


def test_files_changed_unknown_task():
    state = TuiState()
    # Should not crash for unknown task
    state.apply_event("task:files_changed", {"task_id": "unknown", "files": ["x.py"]})
    assert "unknown" not in state.tasks


def test_files_changed_notifies():
    state = _make_state_with_task("t1")
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("task:files_changed", {"task_id": "t1", "files": ["a.py"]})
    assert "tasks" in changes


# --- cancelled ---

def test_cancelled():
    state = TuiState()
    state.apply_event("pipeline:cancelled", {})
    assert state.phase == "cancelled"


def test_cancelled_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("pipeline:cancelled", {})
    assert "phase" in changes


# --- paused ---

def test_paused():
    state = TuiState()
    state.apply_event("pipeline:paused", {})
    assert state.phase == "paused"


# --- resumed ---

def test_resumed():
    state = TuiState()
    state.apply_event("pipeline:resumed", {})
    assert state.phase == "executing"


# --- restarted ---

def test_restarted_resets_state():
    state = _make_state_with_task("t1")
    state.contracts_output.append("x")
    state.contracts_ready = True
    state.cost_estimate = {"min_usd": 1.0, "max_usd": 2.0}
    state.budget_exceeded = True
    state.error = "some error"
    state.total_cost_usd = 5.0
    state.followup_tasks["f1"] = {"task_id": "f1", "state": "started", "error": None}

    state.apply_event("pipeline:restarted", {})

    assert state.phase == "planning"
    assert state.tasks == {}
    assert state.task_order == []
    assert state.contracts_output == []
    assert state.contracts_ready is False
    assert state.contracts_failed is None
    assert state.cost_estimate is None
    assert state.budget_exceeded is False
    assert state.preflight_error is None
    assert state.followup_tasks == {}
    assert state.error is None
    assert state.total_cost_usd == 0.0


# --- worktrees_cleaned ---

def test_worktrees_cleaned_no_op():
    state = TuiState()
    state.phase = "executing"
    state.apply_event("pipeline:worktrees_cleaned", {})
    # Should not change phase
    assert state.phase == "executing"


# --- preflight_failed ---

def test_preflight_failed():
    state = TuiState()
    state.apply_event("pipeline:preflight_failed", {"error": "git dirty"})
    assert state.preflight_error == "git dirty"


def test_preflight_failed_default():
    state = TuiState()
    state.apply_event("pipeline:preflight_failed", {})
    assert state.preflight_error == "Unknown"


def test_preflight_failed_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("pipeline:preflight_failed", {"error": "x"})
    assert "preflight_error" in changes


# --- followup:task_started ---

def test_followup_started():
    state = TuiState()
    state.apply_event("followup:task_started", {"task_id": "f1"})
    assert state.followup_tasks["f1"] == {"task_id": "f1", "state": "started", "error": None}


def test_followup_started_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("followup:task_started", {"task_id": "f1"})
    assert "followup_tasks" in changes


# --- followup:task_completed ---

def test_followup_completed():
    state = TuiState()
    state.apply_event("followup:task_started", {"task_id": "f1"})
    state.apply_event("followup:task_completed", {"task_id": "f1"})
    assert state.followup_tasks["f1"]["state"] == "completed"


def test_followup_completed_unknown_task():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("followup:task_completed", {"task_id": "unknown"})
    # Should not crash, should not notify
    assert "followup_tasks" not in changes


# --- followup:task_error ---

def test_followup_error():
    state = TuiState()
    state.apply_event("followup:task_started", {"task_id": "f1"})
    state.apply_event("followup:task_error", {"task_id": "f1", "error": "boom"})
    assert state.followup_tasks["f1"]["state"] == "error"
    assert state.followup_tasks["f1"]["error"] == "boom"


def test_followup_error_unknown_task():
    state = TuiState()
    state.apply_event("followup:task_error", {"task_id": "unknown", "error": "x"})
    assert "unknown" not in state.followup_tasks


# --- followup:agent_output ---

def test_followup_output():
    state = TuiState()
    state.apply_event("followup:agent_output", {"task_id": "f1", "line": "Running..."})
    state.apply_event("followup:agent_output", {"task_id": "f1", "line": "Done."})
    assert state.followup_output["f1"] == ["Running...", "Done."]


def test_followup_output_notifies():
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("followup:agent_output", {"task_id": "f1", "line": "x"})
    assert "followup_output" in changes


def test_followup_output_missing_line():
    state = TuiState()
    state.apply_event("followup:agent_output", {"task_id": "f1"})
    assert state.followup_output["f1"] == [""]


# --- slot events (no-op) ---

def test_slot_acquired_no_op():
    state = TuiState()
    state.phase = "executing"
    state.apply_event("slot:acquired", {})
    assert state.phase == "executing"


def test_slot_released_no_op():
    state = TuiState()
    state.phase = "executing"
    state.apply_event("slot:released", {})
    assert state.phase == "executing"


def test_slot_queued_no_op():
    state = TuiState()
    state.phase = "executing"
    state.apply_event("slot:queued", {})
    assert state.phase == "executing"


# --- task:state_changed stores error ---

def test_task_state_changed_stores_error():
    state = _make_state_with_task("t1")
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "error", "error": "Agent crashed"})
    assert state.tasks["t1"]["state"] == "error"
    assert state.tasks["t1"]["error"] == "Agent crashed"


def test_task_state_changed_no_error_key():
    state = _make_state_with_task("t1")
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "in_progress"})
    assert state.tasks["t1"]["error"] is None


# --- all 19 events in _EVENT_MAP ---

def test_all_new_events_in_event_map():
    """Verify all 19 new event types are registered in _EVENT_MAP."""
    new_events = [
        "pipeline:cost_estimate",
        "pipeline:budget_exceeded",
        "contracts:output",
        "pipeline:contracts_ready",
        "pipeline:contracts_failed",
        "task:files_changed",
        "pipeline:cancelled",
        "pipeline:paused",
        "pipeline:resumed",
        "pipeline:restarted",
        "pipeline:worktrees_cleaned",
        "pipeline:preflight_failed",
        "followup:task_started",
        "followup:task_completed",
        "followup:task_error",
        "followup:agent_output",
        "slot:acquired",
        "slot:released",
        "slot:queued",
    ]
    for evt in new_events:
        assert evt in TuiState._EVENT_MAP, f"{evt} missing from _EVENT_MAP"


# --- unified_log ---

def test_initial_state_has_unified_log():
    state = TuiState()
    assert state.unified_log == {}


def test_agent_output_appends_to_unified_log():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Creating file..."})
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Done."})
    assert state.unified_log["t1"] == [("agent", "Creating file..."), ("agent", "Done.")]


def test_review_llm_output_appends_to_unified_log():
    state = TuiState()
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "Checking scope..."})
    assert state.unified_log["t1"] == [("review", "Checking scope...")]


def test_unified_log_interleaves_agent_and_review():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "agent line"})
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "review line"})
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "agent line 2"})
    assert state.unified_log["t1"] == [
        ("agent", "agent line"),
        ("review", "review line"),
        ("agent", "agent line 2"),
    ]


def test_unified_log_ring_buffer():
    state = TuiState(max_output_lines=3)
    for i in range(5):
        state.apply_event("task:agent_output", {"task_id": "t1", "line": f"line {i}"})
    assert len(state.unified_log["t1"]) == 3
    assert state.unified_log["t1"][0] == ("agent", "line 2")


def test_review_gate_passed_appends_to_unified_log():
    state = _make_state_with_task("t1")
    state.apply_event("review:gate_passed", {"task_id": "t1", "gate": "gate0_build", "details": "passed"})
    assert len(state.unified_log["t1"]) == 1
    assert state.unified_log["t1"][0][0] == "gate"
    assert "Build" in state.unified_log["t1"][0][1]
    assert "\u2713" in state.unified_log["t1"][0][1]


def test_review_gate_failed_appends_to_unified_log():
    state = _make_state_with_task("t1")
    state.apply_event("review:gate_failed", {"task_id": "t1", "gate": "gate1_lint", "details": "3 errors"})
    assert len(state.unified_log["t1"]) == 1
    assert state.unified_log["t1"][0][0] == "gate"
    assert "Lint" in state.unified_log["t1"][0][1]
    assert "\u2717" in state.unified_log["t1"][0][1]


def test_reset_clears_unified_log():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    state.reset()
    assert state.unified_log == {}


def test_restarted_clears_unified_log():
    state = _make_state_with_task("t1")
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    state.apply_event("pipeline:restarted", {})
    assert state.unified_log == {}


# --- Task 5: partial_success / retrying / interrupted phases ---

def test_all_tasks_done_partial_success():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {
        "summary": {"done": 3, "error": 1, "blocked": 1, "cancelled": 0, "total": 5, "result": "partial_success"}
    })
    assert state.phase == "partial_success"


def test_all_tasks_done_complete():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {
        "summary": {"done": 5, "error": 0, "blocked": 0, "cancelled": 0, "total": 5, "result": "complete"}
    })
    assert state.phase == "final_approval"


def test_all_tasks_done_all_error():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {
        "summary": {"done": 0, "error": 5, "blocked": 0, "cancelled": 0, "total": 5, "result": "error"}
    })
    assert state.phase == "error"


def test_pipeline_interrupted_event():
    state = TuiState()
    state.phase = "executing"
    state.apply_event("pipeline:interrupted", {"summary": {"done": 2, "todo": 3}})
    assert state.phase == "interrupted"


# ── Integration health check state tests ─────────────────────────────


def test_integration_initial_state():
    state = TuiState()
    assert state.integration_baseline is None
    assert state.integration_degraded is False
    assert state.integration_checks == {}
    assert state.integration_prompt is None
    assert state.integration_final_gate is None


def test_integration_baseline_started():
    state = TuiState()
    state.apply_event("integration:baseline_started", {})
    assert state.integration_baseline == {"status": "running"}


def test_integration_baseline_result():
    state = TuiState()
    state.apply_event("integration:baseline_result", {"status": "passed", "exit_code": 0})
    assert state.integration_baseline["status"] == "passed"
    assert state.integration_baseline["exit_code"] == 0


def test_integration_baseline_failed_prompt():
    state = TuiState()
    state.apply_event("integration:baseline_failed_prompt", {"exit_code": 1})
    assert state.integration_prompt is not None
    assert state.integration_prompt["type"] == "baseline"
    assert state.integration_prompt["exit_code"] == 1


def test_integration_baseline_response_ignore():
    state = TuiState()
    state.integration_prompt = {"type": "baseline"}
    state.apply_event("integration:baseline_response", {"action": "ignore_and_continue"})
    assert state.integration_prompt is None
    assert state.integration_degraded is True


def test_integration_check_started():
    state = TuiState()
    state.apply_event("integration:check_started", {"task_id": "t1"})
    assert state.integration_checks["t1"]["status"] == "running"


def test_integration_check_result_passed():
    state = TuiState()
    state.apply_event("integration:check_result", {"task_id": "t1", "status": "passed"})
    assert state.integration_checks["t1"]["status"] == "passed"
    assert state.integration_degraded is False


def test_integration_check_result_ignore():
    state = TuiState()
    state.apply_event("integration:check_result", {
        "task_id": "t1", "status": "failed", "action": "ignore_and_continue",
    })
    assert state.integration_degraded is True


def test_integration_check_prompt():
    state = TuiState()
    state.apply_event("integration:check_prompt", {
        "task_id": "t1", "cmd": "make test", "exit_code": 1,
        "is_regression": True, "baseline_was_red": False,
        "options": ["ignore_and_continue", "stop_pipeline"],
        "phase": "post_merge",
    })
    assert state.integration_prompt is not None
    assert state.integration_prompt["type"] == "post_merge"
    assert state.integration_prompt["task_id"] == "t1"


def test_integration_check_response():
    state = TuiState()
    state.integration_prompt = {"type": "post_merge"}
    state.apply_event("integration:check_response", {"action": "stop_pipeline"})
    assert state.integration_prompt is None
    assert state.integration_degraded is False  # stop doesn't degrade, it halts


def test_integration_final_gate_started():
    state = TuiState()
    state.apply_event("integration:final_gate_started", {})
    assert state.integration_final_gate == {"status": "running"}


def test_integration_final_gate_result():
    state = TuiState()
    state.apply_event("integration:final_gate_result", {"status": "passed", "exit_code": 0})
    assert state.integration_final_gate["status"] == "passed"


def test_integration_reset_clears_state():
    state = TuiState()
    state.integration_baseline = {"status": "passed"}
    state.integration_degraded = True
    state.integration_checks["t1"] = {"status": "passed"}
    state.integration_prompt = {"type": "baseline"}
    state.integration_final_gate = {"status": "passed"}
    state.reset()
    assert state.integration_baseline is None
    assert state.integration_degraded is False
    assert state.integration_checks == {}
    assert state.integration_prompt is None
    assert state.integration_final_gate is None


def test_integration_events_in_event_map():
    """All integration events must be in the EVENT_MAP."""
    integration_events = [
        "integration:baseline_started",
        "integration:baseline_result",
        "integration:baseline_failed_prompt",
        "integration:baseline_response",
        "integration:check_started",
        "integration:check_result",
        "integration:check_prompt",
        "integration:check_response",
        "integration:final_gate_started",
        "integration:final_gate_result",
    ]
    for event in integration_events:
        assert event in TuiState._EVENT_MAP, f"Missing from _EVENT_MAP: {event}"
