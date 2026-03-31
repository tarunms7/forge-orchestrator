from forge.tui.state import TuiState


def test_task_question_updates_state():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_progress", "title": "Test"}}
    state.apply_event(
        "task:question",
        {
            "task_id": "t1",
            "question": {"id": "q1", "question": "Which?", "suggestions": ["A", "B"]},
        },
    )
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
    state.apply_event(
        "review:gate_passed", {"task_id": "t1", "gate": "gate0_build", "details": "OK"}
    )
    assert state.review_gates["t1"]["gate0_build"]["status"] == "passed"


def test_pipeline_all_tasks_done():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {"summary": {"done": 4, "total": 4}})
    assert state.phase == "final_approval"


def test_pipeline_pr_created():
    state = TuiState()
    state.apply_event("pipeline:pr_created", {"pr_url": "https://github.com/..."})
    assert state.pr_url == "https://github.com/..."


# --- planning:question / planning:answer ---


def test_planning_question_added_to_pending():
    """planning:question should add __planning__ to pending_questions."""
    state = TuiState()
    state.apply_event(
        "planning:question",
        {
            "question_id": "q1",
            "question": {"question": "JWT or session?", "suggestions": ["JWT", "session"]},
        },
    )
    assert "__planning__" in state.pending_questions
    assert state.pending_questions["__planning__"]["question"] == "JWT or session?"
    assert state.pending_questions["__planning__"]["question_id"] == "q1"


def test_planning_answer_removes_from_pending():
    """planning:answer should remove __planning__ from pending_questions."""
    state = TuiState()
    state.pending_questions["__planning__"] = {"question": "JWT or session?"}
    state.apply_event("planning:answer", {"answer": "JWT"})
    assert "__planning__" not in state.pending_questions


def test_planning_question_notifies():
    """planning:question should notify with 'planning' field."""
    state = TuiState()
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event(
        "planning:question",
        {
            "question": {"question": "Which DB?"},
        },
    )
    assert "planning" in changes


def test_planning_answer_notifies():
    """planning:answer should notify with 'planning' field."""
    state = TuiState()
    state.pending_questions["__planning__"] = {"question": "Which DB?"}
    changes = []
    state.on_change(lambda f: changes.append(f))
    state.apply_event("planning:answer", {"answer": "Postgres"})
    assert "planning" in changes


def test_planning_answer_noop_when_no_pending():
    """planning:answer should not crash when no pending planning question."""
    state = TuiState()
    state.apply_event("planning:answer", {"answer": "JWT"})
    assert "__planning__" not in state.pending_questions


def test_planning_events_in_event_map():
    """planning:question and planning:answer should be in _EVENT_MAP."""
    assert "planning:question" in TuiState._EVENT_MAP
    assert "planning:answer" in TuiState._EVENT_MAP
