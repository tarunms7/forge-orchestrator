"""Tests for planner progress state tracking and log collapsing."""

from forge.tui.state import TuiState


def test_planner_status_transitions():
    """Test planner status changes correctly through scanning→reading→building sequence."""
    state = TuiState()

    # Starting state
    assert state.planner_status == ""

    # Scanning codebase
    state._on_planner_output({"line": "Analyzing codebase for patterns"})
    assert state.planner_status == "scanning codebase"

    # Searching
    state._on_planner_output({"line": "🔍 Searching for config files"})
    assert state.planner_status == "scanning codebase"

    # Reading files
    state._on_planner_output({"line": "📖 Reading src/main.py"})
    assert state.planner_status == "reading files"

    # Building task graph
    state._on_planner_output({"line": "⚙ Planner generating task dependencies"})
    assert state.planner_status == "building task graph"


def test_planner_files_examined_count():
    """Test planner_files_examined increments for Reading lines."""
    state = TuiState()

    assert state.planner_files_examined == 0

    state._on_planner_output({"line": "📖 Reading src/main.py"})
    assert state.planner_files_examined == 1

    state._on_planner_output({"line": "📖 Reading tests/test_main.py"})
    assert state.planner_files_examined == 2

    # Non-reading lines don't increment
    state._on_planner_output({"line": "🔍 Searching for patterns"})
    assert state.planner_files_examined == 2


def test_planner_waiting_for_input():
    """Test planner_status == 'waiting for human input' on planning:question."""
    state = TuiState()

    state._on_planner_output({"line": "📖 Reading config.py"})
    assert state.planner_status == "reading files"

    state._on_planning_question({"question": {"text": "Which approach should I use?"}})
    assert state.planner_status == "waiting for human input"


def test_planner_answer_resumes_status():
    """Test planning:answer resumes planner_status."""
    state = TuiState()

    state._on_planning_question({"question": {"text": "Question?"}})
    assert state.planner_status == "waiting for human input"

    state._on_planning_answer({"answer": "Option A"})
    assert state.planner_status == "reading files"


def test_planner_completion_status():
    """Test plan_ready sets planner_status and candidate_tasks count."""
    state = TuiState()

    tasks_data = [
        {"id": "task1", "title": "Task 1"},
        {"id": "task2", "title": "Task 2"},
        {"id": "task3", "title": "Task 3"},
    ]

    state._on_plan_ready({"tasks": tasks_data})

    assert state.planner_status == "planning complete"
    assert state.planner_candidate_tasks == 3


def test_planner_collapsed_output_dedupes_reads():
    """Test collapsed output collapses runs of 3+ identical-prefix lines."""
    state = TuiState()

    # Add 5 Reading lines + 1 non-read + 3 more Reading lines
    for i in range(5):
        state.planner_output.append(f"📖 Reading file{i}.py")

    state.planner_output.append("Some other line")

    for i in range(3):
        state.planner_output.append(f"📖 Reading config{i}.py")

    collapsed = state.planner_collapsed_output

    # First run of 5 → first, summary, last (3 lines)
    # Non-read line (1 line)
    # Second run of 3 → first, summary, last (3 lines)
    assert len(collapsed) == 7

    assert collapsed[0] == "📖 Reading file0.py"
    assert "... and 3 more" in collapsed[1]
    assert collapsed[2] == "📖 Reading file4.py"
    assert collapsed[3] == "Some other line"
    assert collapsed[4] == "📖 Reading config0.py"
    assert "... and 1 more" in collapsed[5]
    assert collapsed[6] == "📖 Reading config2.py"


def test_planner_collapsed_output_preserves_distinct():
    """Test no collapsing occurs for mixed different lines."""
    state = TuiState()

    lines = [
        "📖 Reading file1.py",
        "🔍 Searching patterns",
        "📖 Reading file2.py",
        "Some other line",
        "🔍 Grep for imports",
    ]

    for line in lines:
        state.planner_output.append(line)

    collapsed = state.planner_collapsed_output
    assert collapsed == lines


def test_planner_reset_clears_progress():
    """Test reset() clears all planner progress fields."""
    state = TuiState()

    state.planner_status = "reading files"
    state.planner_files_examined = 5
    state.planner_candidate_tasks = 3

    state.reset()

    assert state.planner_status == ""
    assert state.planner_files_examined == 0
    assert state.planner_candidate_tasks == 0
