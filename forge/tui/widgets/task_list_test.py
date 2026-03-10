"""Tests for TaskList widget."""

from forge.tui.widgets.task_list import format_task_line, STATE_ICONS


def test_state_icons_all_states():
    expected = ["todo", "in_progress", "in_review", "awaiting_approval", "merging", "done", "cancelled", "error"]
    for state in expected:
        assert state in STATE_ICONS, f"Missing icon for {state}"


def test_format_task_line_todo():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert "Setup database" in line
    assert STATE_ICONS["todo"] in line


def test_format_task_line_selected():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=True)
    assert "Setup database" in line
    assert "1f2937" in line  # highlight background color
    assert "►" not in line  # no more arrow indicator


def test_format_task_line_done():
    task = {"id": "t1", "title": "Setup database", "state": "done", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["done"] in line


def test_format_task_line_error():
    task = {"id": "t1", "title": "Setup database", "state": "error", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["error"] in line
