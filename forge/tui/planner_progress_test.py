"""Tests for planner progress state tracking and log collapsing."""

import pytest
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

    # Add Reading lines
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

    # Normal status
    state._on_planner_output({"line": "📖 Reading config.py"})
    assert state.planner_status == "reading files"

    # Question event sets waiting status
    state._on_planning_question({"question": {"text": "Which approach should I use?"}})
    assert state.planner_status == "waiting for human input"


def test_planner_answer_resumes_status():
    """Test planning:answer resumes planner_status."""
    state = TuiState()

    # Set waiting status
    state._on_planning_question({"question": {"text": "Question?"}})
    assert state.planner_status == "waiting for human input"

    # Answer should resume to reading
    state._on_planning_answer({"answer": "Option A"})
    assert state.planner_status == "reading files"


def test_planner_completion_status():
    """Test plan_ready sets planner_status and candidate_tasks count."""
    state = TuiState()

    tasks_data = [
        {"id": "task1", "title": "Task 1"},
        {"id": "task2", "title": "Task 2"},
        {"id": "task3", "title": "Task 3"}
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

    # First run of 5 should become: first, summary, last (3 lines)
    # Non-read line stays (1 line)
    # Second run of 3 should become: first, summary, last (3 lines)
    # Total: 7 lines
    expected_length = 3 + 1 + 3
    assert len(collapsed) == expected_length

    # Check structure
    assert collapsed[0] == "📖 Reading file0.py"  # first
    assert "... and 3 more" in collapsed[1]  # summary
    assert collapsed[2] == "📖 Reading file4.py"  # last
    assert collapsed[3] == "Some other line"  # non-read
    assert collapsed[4] == "📖 Reading config0.py"  # first of second run
    assert "... and 1 more" in collapsed[5]  # summary
    assert collapsed[6] == "📖 Reading config2.py"  # last of second run


def test_planner_collapsed_output_preserves_distinct():
    """Test no collapsing occurs for mixed different lines."""
    state = TuiState()

    lines = [
        "📖 Reading file1.py",
        "🔍 Searching patterns",
        "📖 Reading file2.py",
        "Some other line",
        "🔍 Grep for imports"
    ]

    for line in lines:
        state.planner_output.append(line)

    collapsed = state.planner_collapsed_output

    # Should be unchanged since no run of 3+ identical prefixes
    assert collapsed == lines


def test_planner_reset_clears_progress():
    """Test reset() clears all planner progress fields."""
    state = TuiState()

    # Set some values
    state.planner_status = "reading files"
    state.planner_files_examined = 5
    state.planner_candidate_tasks = 3

    state.reset()

    assert state.planner_status == ""
    assert state.planner_files_examined == 0
    assert state.planner_candidate_tasks == 0


# PhaseBanner tests for task-2
def test_pipeline_banner_shows_planner_status():
    """Test PhaseBanner.update_planner_detail builds correct status summary."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()

    # Test status with files examined
    banner.update_planner_detail("reading files", 12, 0)
    assert banner._planner_detail == "reading files · 12 files examined"

    # Test status with files and tasks
    banner.update_planner_detail("building task graph", 8, 3)
    assert banner._planner_detail == "building task graph · 8 files examined · 3 tasks"

    # Test status with only tasks
    banner.update_planner_detail("validating dependencies", 0, 5)
    assert banner._planner_detail == "validating dependencies · 5 tasks"

    # Test status alone
    banner.update_planner_detail("scanning codebase", 0, 0)
    assert banner._planner_detail == "scanning codebase"


def test_pipeline_banner_waiting_for_answer():
    """Test PhaseBanner shows waiting message for human input."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()

    # Test waiting for human input
    banner.update_planner_detail("waiting for human input", 5, 2)
    assert banner._planner_detail == "⏳ waiting for your answer"


def test_pipeline_banner_clears_on_non_planning():
    """Test PhaseBanner clears detail when status is empty."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()

    # Set some detail first
    banner.update_planner_detail("reading files", 3, 0)
    assert banner._planner_detail == "reading files · 3 files examined"

    # Clear with empty status
    banner.update_planner_detail("", 0, 0)
    assert banner._planner_detail == ""