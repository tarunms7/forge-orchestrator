"""Tests for planner progress state tracking and log collapsing functionality."""

import pytest
from forge.tui.state import TuiState


def test_planner_status_transitions():
    """Test that planner_status changes correctly through scanning→reading→building sequence."""
    state = TuiState()

    # Start with empty status
    assert state.planner_status == ""

    # Analyzing codebase → scanning
    state.apply_event("planner:output", {"line": "Analyzing codebase structure..."})
    assert state.planner_status == "scanning codebase"

    # Reading files → reading
    state.apply_event("planner:output", {"line": "📖 Reading src/main.py"})
    assert state.planner_status == "reading files"
    assert state.planner_files_examined == 1

    # Searching → scanning
    state.apply_event("planner:output", {"line": "🔍 Searching for test files"})
    assert state.planner_status == "scanning codebase"

    # Planner generating → building
    state.apply_event("planner:output", {"line": "⚙ Planner generating task graph"})
    assert state.planner_status == "building task graph"


def test_planner_files_examined_count():
    """Test that planner_files_examined increments correctly for Reading lines."""
    state = TuiState()

    assert state.planner_files_examined == 0

    # Multiple reading events should increment count
    state.apply_event("planner:output", {"line": "📖 Reading src/main.py"})
    assert state.planner_files_examined == 1

    state.apply_event("planner:output", {"line": "📖 Reading tests/test_main.py"})
    assert state.planner_files_examined == 2

    state.apply_event("planner:output", {"line": "📖 Reading README.md"})
    assert state.planner_files_examined == 3

    # Non-reading lines should not increment
    state.apply_event("planner:output", {"line": "🔍 Searching for imports"})
    assert state.planner_files_examined == 3


def test_planner_waiting_for_input():
    """Test that planner_status is set to 'waiting for human input' on planning question."""
    state = TuiState()

    # Set some initial status
    state.planner_status = "reading files"

    # Planning question should set status to waiting
    state.apply_event("planning:question", {
        "question": {"question": "Should I include tests?", "suggestions": ["yes", "no"]},
        "question_id": "q1"
    })

    assert state.planner_status == "waiting for human input"


def test_planner_answer_resumes_status():
    """Test that planner_status resumes to 'reading files' after answering a question."""
    state = TuiState()

    # Set waiting status
    state.planner_status = "waiting for human input"

    # Answer should resume to reading files
    state.apply_event("planning:answer", {"answer": "yes"})

    assert state.planner_status == "reading files"


def test_planner_completion_status():
    """Test that plan_ready sets status to 'planning complete' and counts candidate tasks."""
    state = TuiState()

    # Set some initial status
    state.planner_status = "building task graph"

    # Plan ready with tasks
    tasks = [
        {"id": "task1", "title": "Task 1", "description": "First task"},
        {"id": "task2", "title": "Task 2", "description": "Second task"},
        {"id": "task3", "title": "Task 3", "description": "Third task"}
    ]

    state.apply_event("pipeline:plan_ready", {
        "tasks": tasks,
        "repos": [{"name": "test-repo", "path": "/test"}]
    })

    assert state.planner_status == "planning complete"
    assert state.planner_candidate_tasks == 3


def test_planner_collapsed_output_dedupes_reads():
    """Test that collapsed output collapses runs of 3+ identical-prefix lines."""
    state = TuiState()

    # Add 5 Reading lines
    for i in range(5):
        state.planner_output.append(f"📖 Reading file_{i}.py")

    # Add a non-read line
    state.planner_output.append("Some other output")

    # Add 3 more Reading lines
    for i in range(3):
        state.planner_output.append(f"📖 Reading another_{i}.py")

    collapsed = state.planner_collapsed_output

    # First run of 5 should collapse to 3 lines: first, summary, last
    # Non-read line preserved
    # Second run of 3 should collapse to 3 lines: first, summary, last
    expected = [
        "📖 Reading file_0.py",          # first of first run
        "  ... and 3 more",               # summary of first run (5-2=3)
        "📖 Reading file_4.py",          # last of first run
        "Some other output",              # non-collapsible line
        "📖 Reading another_0.py",       # first of second run
        "  ... and 1 more",               # summary of second run (3-2=1)
        "📖 Reading another_2.py"        # last of second run
    ]

    assert collapsed == expected


def test_planner_collapsed_output_preserves_distinct():
    """Test that mix of different lines doesn't collapse."""
    state = TuiState()

    # Add mix of different types
    state.planner_output.extend([
        "📖 Reading file1.py",
        "🔍 Searching for imports",
        "📖 Reading file2.py",
        "Some normal output",
        "🔍 Grep for patterns"
    ])

    collapsed = state.planner_collapsed_output

    # No runs of 3+ identical prefixes, so no collapsing should occur
    assert collapsed == state.planner_output


def test_planner_reset_clears_progress():
    """Test that reset() clears all planner progress fields."""
    state = TuiState()

    # Set some progress state
    state.planner_status = "reading files"
    state.planner_files_examined = 5
    state.planner_candidate_tasks = 3

    # Reset should clear everything
    state.reset()

    assert state.planner_status == ""
    assert state.planner_files_examined == 0
    assert state.planner_candidate_tasks == 0


def test_planner_collapsed_output_handles_grep_and_searching():
    """Test that collapsing works for all collapsible prefixes."""
    state = TuiState()

    # Add runs of different collapsible prefixes
    state.planner_output.extend([
        "🔍 Searching pattern 1",
        "🔍 Searching pattern 2",
        "🔍 Searching pattern 3",
        "🔍 Searching pattern 4",  # 4 searching lines -> should collapse
        "🔍 Grep for imports 1",
        "🔍 Grep for imports 2",
        "🔍 Grep for imports 3",   # 3 grep lines -> should collapse
        "📖 Reading only one file"  # single read -> no collapse
    ])

    collapsed = state.planner_collapsed_output

    expected = [
        "🔍 Searching pattern 1",     # first of searching run
        "  ... and 2 more",           # summary (4-2=2)
        "🔍 Searching pattern 4",     # last of searching run
        "🔍 Grep for imports 1",      # first of grep run
        "  ... and 1 more",           # summary (3-2=1)
        "🔍 Grep for imports 3",      # last of grep run
        "📖 Reading only one file"    # single line preserved
    ]

    assert collapsed == expected


def test_planner_status_notifications():
    """Test that status changes trigger planner_status notifications."""
    state = TuiState()
    notifications = []

    def capture_notification(event_type):
        notifications.append(event_type)

    state._notify = capture_notification

    # Status change should trigger notification
    state.apply_event("planner:output", {"line": "📖 Reading test.py"})

    assert "planner_status" in notifications


def test_handle_planning_output_status_inference():
    """Test that _handle_planning_output also infers planner status correctly."""
    state = TuiState()

    # Test via handle_planning_output method (legacy compatibility)
    state._handle_planning_output("Architect", {"line": "🔍 Searching for dependencies"})
    assert state.planner_status == "scanning codebase"

    state._handle_planning_output("Architect", {"line": "📖 Reading config.py"})
    assert state.planner_status == "reading files"
    assert state.planner_files_examined == 1