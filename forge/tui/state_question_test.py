from forge.tui.state import TuiState


def test_task_question_updates_state():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_progress", "title": "Test"}}
    state.apply_event("task:question", {
        "task_id": "t1",
        "question": {"id": "q1", "question": "Which?", "suggestions": ["A", "B"]},
    })
    assert state.tasks["t1"]["state"] == "awaiting_input"
    assert state.pending_questions["t1"] is not None


def test_task_answer_clears_pending():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "awaiting_input", "title": "Test"}}
    state.pending_questions = {"t1": {"id": "q1", "question": "Which?"}}
    state.apply_event("task:answer", {"task_id": "t1", "answer": "A"})
    assert "t1" not in state.pending_questions


def test_task_resumed_sets_running():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "awaiting_input", "title": "Test"}}
    state.apply_event("task:resumed", {"task_id": "t1"})
    assert state.tasks["t1"]["state"] == "in_progress"


def test_review_gate_started():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_review", "title": "Test"}}
    state.apply_event("review:gate_started", {"task_id": "t1", "gate": "gate0_build"})
    assert state.review_gates.get("t1", {}).get("gate0_build", {}).get("status") == "running"


def test_review_gate_passed():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_review", "title": "Test"}}
    state.review_gates = {"t1": {"gate0_build": {"status": "running"}}}
    state.apply_event("review:gate_passed", {"task_id": "t1", "gate": "gate0_build", "details": "OK"})
    assert state.review_gates["t1"]["gate0_build"]["status"] == "passed"


def test_pipeline_all_tasks_done():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {"summary": {"done": 4, "total": 4}})
    assert state.phase == "final_approval"


def test_pipeline_pr_created():
    state = TuiState()
    state.apply_event("pipeline:pr_created", {"pr_url": "https://github.com/..."})
    assert state.pr_url == "https://github.com/..."
