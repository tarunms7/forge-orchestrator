"""Tests for AgentOutput widget."""

from forge.tui.widgets.agent_output import format_header, format_output


def test_format_header_with_task():
    header = format_header("task-1", "Auth middleware", "in_progress")
    assert "Auth middleware" in header
    assert "task-1" in header


def test_format_header_no_task():
    header = format_header(None, None, None)
    assert "No task selected" in header


def test_format_output_empty():
    result = format_output([])
    assert "Waiting" in result


def test_format_output_with_lines():
    lines = ["Creating auth/jwt.py...", "Adding middleware...", "Done."]
    result = format_output(lines)
    assert "Creating auth/jwt.py..." in result
    assert "Done." in result
