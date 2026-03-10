"""Tests for TaskList widget."""

from forge.tui.widgets.task_list import format_task_line, STATE_ICONS


def test_state_icons_all_states():
    from forge.core.models import TaskState
    for state in TaskState:
        assert state.value in STATE_ICONS, f"Missing icon for {state.value}"


def test_format_task_line_todo():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert "Setup database" in line
    assert STATE_ICONS["todo"] in line


def test_format_task_line_selected():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=True)
    assert "►" in line


def test_format_task_line_done():
    task = {"id": "t1", "title": "Setup database", "state": "done", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["done"] in line


def test_format_task_line_error():
    task = {"id": "t1", "title": "Setup database", "state": "error", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["error"] in line
