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
    from forge.tui.widgets.agent_output import _SPINNER_FRAMES
    result = format_output([])
    assert "Waiting" in result
    # Default frame 0 should show first spinner char
    assert _SPINNER_FRAMES[0] in result


def test_format_output_spinner_frames():
    from forge.tui.widgets.agent_output import _SPINNER_FRAMES
    result_0 = format_output([], spinner_frame=0)
    result_1 = format_output([], spinner_frame=1)
    assert _SPINNER_FRAMES[0] in result_0
    assert _SPINNER_FRAMES[1] in result_1


def test_format_output_with_lines():
    lines = ["Creating auth/jwt.py...", "Adding middleware...", "Done."]
    result = format_output(lines)
    assert "Creating auth/jwt.py..." in result
    assert "Done." in result
