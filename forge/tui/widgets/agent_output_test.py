"""Tests for AgentOutput widget."""

from __future__ import annotations


from forge.tui.widgets.agent_output import (
    AgentOutput,
    _TYPING_FRAMES,
    format_header,
    format_output,
)


# ── format_header tests ──────────────────────────────────────────────────


def test_format_header_with_task():
    header = format_header("task-1", "Auth middleware", "in_progress")
    assert "Auth middleware" in header
    assert "task-1" in header


def test_format_header_no_task():
    header = format_header(None, None, None)
    assert "No task selected" in header


def test_format_header_planner():
    header = format_header("planner", "Planning", "planning")
    assert "Planner" in header
    assert "exploring" in header


# ── format_output tests ─────────────────────────────────────────────────


def test_format_output_empty():
    from forge.tui.widgets.agent_output import _SPINNER_FRAMES
    result = format_output([])
    assert "Waiting" in result
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


def test_format_output_no_typing_indicator_by_default():
    lines = ["line1", "line2"]
    result = format_output(lines)
    assert "Typing" not in result


def test_format_output_with_streaming_shows_typing_indicator():
    lines = ["line1", "line2"]
    result = format_output(lines, streaming=True, typing_frame=0)
    assert "Typing" in result
    cursor = _TYPING_FRAMES[0]
    assert cursor in result


def test_format_output_streaming_false_no_indicator():
    lines = ["line1"]
    result = format_output(lines, streaming=False)
    assert "Typing" not in result


def test_format_output_typing_frame_cycles():
    lines = ["line1"]
    result_0 = format_output(lines, streaming=True, typing_frame=0)
    result_1 = format_output(lines, streaming=True, typing_frame=1)
    # Both should contain the typing indicator
    assert "Typing" in result_0
    assert "Typing" in result_1
    # Cursor chars should differ
    assert _TYPING_FRAMES[0] in result_0
    assert _TYPING_FRAMES[1] in result_1


def test_format_output_empty_lines_no_streaming_indicator():
    """When lines is empty, streaming indicator should NOT appear (spinner shown instead)."""
    result = format_output([], streaming=True, typing_frame=0)
    assert "Waiting" in result


# ── AgentOutput widget unit tests ────────────────────────────────────────


def test_agent_output_init_defaults():
    widget = AgentOutput()
    assert widget._lines == []
    assert widget._streaming is False
    assert widget._typing_frame == 0
    assert widget._typing_timer is None


def test_set_streaming_on_before_compose():
    """set_streaming should not raise when widget is not yet composed."""
    widget = AgentOutput()
    widget.set_streaming(True)
    assert widget._streaming is True


def test_set_streaming_off_before_compose():
    widget = AgentOutput()
    widget.set_streaming(True)
    widget.set_streaming(False)
    assert widget._streaming is False
    assert widget._typing_timer is None
    assert widget._typing_frame == 0


def test_set_streaming_idempotent():
    """Calling set_streaming with the same value should be a no-op."""
    widget = AgentOutput()
    widget.set_streaming(False)  # already False
    assert widget._streaming is False
    assert widget._typing_timer is None


def test_append_line_adds_to_lines():
    widget = AgentOutput()
    widget.append_line("first line")
    assert widget._lines == ["first line"]
    widget.append_line("second line")
    assert widget._lines == ["first line", "second line"]


def test_append_line_before_compose():
    """append_line should not raise before widget is composed."""
    widget = AgentOutput()
    widget.append_line("safe to call")
    assert widget._lines == ["safe to call"]


def test_update_output_resets_streaming():
    """update_output should call set_streaming(False) internally."""
    widget = AgentOutput()
    widget._streaming = True
    widget.update_output("task-1", "Test", "running", ["line1"])
    assert widget._streaming is False
    assert widget._task_id == "task-1"
    assert widget._title == "Test"
    assert widget._state == "running"
    assert widget._lines == ["line1"]


def test_update_output_replaces_lines():
    widget = AgentOutput()
    widget._lines = ["old1", "old2"]
    widget.update_output("t1", "T", "s", ["new1"])
    assert widget._lines == ["new1"]


def test_update_output_before_compose():
    """update_output should not raise before widget is composed."""
    widget = AgentOutput()
    widget.update_output("t1", "Title", "running", ["line"])
    assert widget._lines == ["line"]


def test_tick_typing_increments_frame():
    widget = AgentOutput()
    widget._streaming = True
    widget._typing_frame = 0
    # _tick_typing will fail on query_one but should still increment frame
    widget._tick_typing()
    assert widget._typing_frame == 1


def test_tick_typing_noop_when_not_streaming():
    widget = AgentOutput()
    widget._streaming = False
    widget._typing_frame = 5
    widget._tick_typing()
    assert widget._typing_frame == 5  # unchanged


def test_set_streaming_on_off_resets_typing_frame():
    widget = AgentOutput()
    widget.set_streaming(True)
    widget._typing_frame = 5
    widget.set_streaming(False)
    assert widget._typing_frame == 0
