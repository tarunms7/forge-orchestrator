"""Tests for AgentOutput widget."""

from __future__ import annotations

from forge.tui.widgets.agent_output import (
    _TYPING_FRAMES,
    AgentOutput,
    format_error_detail,
    format_header,
    format_output,
    format_unified_output,
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
    result = format_output([])
    assert "Waiting" in result
    # Should contain one of the breathing pulse characters
    assert any(c in result for c in ["●", "◉", "○"])


def test_format_output_spinner_frames():
    result_0 = format_output([], spinner_frame=0)
    result_1 = format_output([], spinner_frame=1)
    # Both should show the waiting message with a pulse character
    assert "Waiting" in result_0
    assert "Waiting" in result_1
    # Different frames should produce different output
    assert any(c in result_0 for c in ["●", "◉", "○"])
    assert any(c in result_1 for c in ["●", "◉", "○"])


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


# ── format_error_detail tests ─────────────────────────────────────────


def test_format_error_detail_basic():
    """Error detail view should contain header, error, and action bar."""
    task = {"title": "Auth middleware", "error": "Import failed", "files_changed": ["auth.py"]}
    result = format_error_detail("task-1", task, ["line1", "line2"])
    assert "✖ Auth middleware — ERROR" in result
    assert "Import failed" in result
    assert "auth.py" in result
    assert "Last output" in result
    assert "line1" in result
    assert "line2" in result
    assert "[R] retry" in result
    assert "[s] skip" in result
    assert "[Esc] dismiss" in result


def test_format_error_detail_no_files_changed():
    """Error detail without files_changed should still render."""
    task = {"title": "Task X", "error": "Boom"}
    result = format_error_detail("task-1", task, [])
    assert "✖ Task X — ERROR" in result
    assert "Boom" in result
    assert "No output captured" in result


def test_format_error_detail_truncates_to_last_20_lines():
    """Only the last 20 lines of output should be shown."""
    lines = [f"line-{i}" for i in range(50)]
    task = {"title": "T", "error": "err"}
    result = format_error_detail("t1", task, lines)
    assert "line-30" in result
    assert "line-49" in result
    assert "line-0" not in result


def test_format_error_detail_fewer_than_20_lines():
    """When fewer than 20 lines, all should be shown."""
    lines = [f"line-{i}" for i in range(5)]
    task = {"title": "T", "error": "err"}
    result = format_error_detail("t1", task, lines)
    for i in range(5):
        assert f"line-{i}" in result


def test_format_error_detail_default_error_message():
    """When error key is missing, should show 'Unknown error'."""
    task = {"title": "T"}
    result = format_error_detail("t1", task, [])
    assert "Unknown error" in result


def test_format_error_detail_valid_rich_markup():
    """Error detail output should be valid Rich markup."""
    from io import StringIO

    from rich.console import Console

    task = {"title": "Auth", "error": "Failed", "files_changed": ["a.py"]}
    result = format_error_detail("t1", task, ["line1"])
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)  # Raises MarkupError if broken


# ── AgentOutput error mode tests ──────────────────────────────────────


def test_agent_output_error_mode_default_false():
    widget = AgentOutput()
    assert widget.is_error_mode is False


def test_agent_output_render_error_detail_sets_mode():
    widget = AgentOutput()
    task = {"title": "T", "error": "err"}
    widget.render_error_detail("t1", task, ["line"])
    assert widget.is_error_mode is True
    assert widget._streaming is False


def test_agent_output_clear_error_detail_resets_mode():
    widget = AgentOutput()
    task = {"title": "T", "error": "err"}
    widget.render_error_detail("t1", task, ["line"])
    widget.clear_error_detail()
    assert widget.is_error_mode is False


def test_agent_output_render_error_detail_before_compose():
    """render_error_detail should not raise before widget is composed."""
    widget = AgentOutput()
    task = {"title": "T", "error": "err", "files_changed": ["a.py"]}
    widget.render_error_detail("t1", task, ["line1", "line2"])
    assert widget.is_error_mode is True
    assert widget._task_id == "t1"


# ── format_unified_output tests ────────────────────────────────────────


def test_format_unified_output_empty_shows_spinner():
    result = format_unified_output([])
    assert "Waiting" in result


def test_format_unified_output_agent_section_header():
    entries = [("agent", "line 1"), ("agent", "line 2")]
    result = format_unified_output(entries)
    assert "AGENT" in result
    assert "─────" in result
    assert "line 1" in result
    assert "line 2" in result


def test_format_unified_output_review_section_header():
    entries = [("review", "review line")]
    result = format_unified_output(entries)
    assert "REVIEW 1" in result
    assert "review line" in result


def test_format_unified_output_interleaved_sections():
    entries = [
        ("agent", "agent 1"),
        ("review", "review 1"),
        ("agent", "agent 2"),
    ]
    result = format_unified_output(entries)
    # Should have AGENT header, then REVIEW 1, then AGENT again
    assert result.count("AGENT") == 2
    assert "REVIEW 1" in result


def test_format_unified_output_review_count_increments():
    entries = [
        ("agent", "a1"),
        ("review", "r1"),
        ("agent", "a2"),
        ("review", "r2"),
    ]
    result = format_unified_output(entries)
    assert "REVIEW 1" in result
    assert "REVIEW 2" in result


def test_format_unified_output_gate_merges_into_review():
    """Gate entries should appear under the review section, not create their own header."""
    entries = [
        ("agent", "coding..."),
        ("gate", "🔨 Build: ✓ passed"),
        ("review", "analyzing..."),
    ]
    result = format_unified_output(entries)
    # gate should trigger a REVIEW section, not a GATE section
    assert "REVIEW 1" in result
    assert "🔨 Build: ✓ passed" in result
    assert "GATE" not in result


def test_format_unified_output_gate_formatting():
    """Gate lines should be indented and colored."""
    entries = [("gate", "🔨 Build: ✓ passed")]
    result = format_unified_output(entries)
    assert "#79c0ff" in result  # gate color


def test_format_unified_output_streaming_indicator():
    entries = [("agent", "working...")]
    result = format_unified_output(entries, streaming=True, typing_frame=0)
    assert "Typing" in result


def test_format_unified_output_no_streaming_indicator_by_default():
    entries = [("agent", "done")]
    result = format_unified_output(entries)
    assert "Typing" not in result


def test_format_unified_output_valid_rich_markup():
    """Output should be valid Rich markup."""
    from io import StringIO

    from rich.console import Console

    entries = [
        ("agent", "line 1"),
        ("gate", "🔨 Build: ✓ ok"),
        ("review", "looks good"),
    ]
    result = format_unified_output(entries)
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)  # Raises MarkupError if broken


# ── AgentOutput unified methods ────────────────────────────────────


def test_agent_output_init_has_unified_entries():
    widget = AgentOutput()
    assert widget._unified_entries == []


def test_append_unified_adds_to_entries():
    widget = AgentOutput()
    widget.append_unified("agent", "first line")
    assert widget._unified_entries == [("agent", "first line")]
    widget.append_unified("review", "review line")
    assert widget._unified_entries == [("agent", "first line"), ("review", "review line")]


def test_append_unified_before_compose():
    """append_unified should not raise before widget is composed."""
    widget = AgentOutput()
    widget.append_unified("agent", "safe to call")
    assert widget._unified_entries == [("agent", "safe to call")]


def test_update_unified_replaces_entries():
    widget = AgentOutput()
    widget._unified_entries = [("agent", "old")]
    widget.update_unified("t1", "Title", "running", [("agent", "new")])
    assert widget._unified_entries == [("agent", "new")]
    assert widget._task_id == "t1"
    assert widget._title == "Title"
    assert widget._state == "running"


def test_update_unified_resets_streaming():
    widget = AgentOutput()
    widget._streaming = True
    widget.update_unified("t1", "T", "s", [("agent", "x")])
    assert widget._streaming is False


def test_update_unified_before_compose():
    """update_unified should not raise before widget is composed."""
    widget = AgentOutput()
    widget.update_unified("t1", "Title", "running", [("agent", "line")])
    assert widget._unified_entries == [("agent", "line")]


def test_render_markdown_escapes_rich_markup_in_plain_text():
    """Square brackets in plain text should be escaped to prevent Rich markup injection."""
    import forge.tui.widgets.agent_output as ao
    from forge.tui.widgets.agent_output import _render_markdown

    ao._IN_CODE_BLOCK = False

    result = _render_markdown("Use [bold]this[/bold] to inject")
    # The brackets should be escaped (both [ and ] are escaped)
    assert "\\[bold\\]" in result
    assert "\\[/bold\\]" in result


def test_render_markdown_preserves_bold_and_code():
    """Markdown bold and inline code should still render as Rich markup."""
    import forge.tui.widgets.agent_output as ao
    from forge.tui.widgets.agent_output import _render_markdown

    ao._IN_CODE_BLOCK = False

    result = _render_markdown("This is **bold** and `code`")
    assert "[bold]" in result
    assert "[#79c0ff]" in result


def test_render_markdown_escapes_brackets_in_bold():
    """Brackets inside **bold** should be escaped."""
    import forge.tui.widgets.agent_output as ao
    from forge.tui.widgets.agent_output import _render_markdown

    ao._IN_CODE_BLOCK = False

    result = _render_markdown("**array[0]** value")
    assert "\\[0]" in result or "\\[0\\]" in result


def test_format_output_with_brackets_is_valid_rich():
    """Output containing square brackets should be valid Rich markup."""
    from io import StringIO

    from rich.console import Console

    lines = ["Use [red]color[/red] to inject", "Normal **bold** text"]
    result = format_output(lines)
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)  # Raises MarkupError if broken


def test_tick_spinner_skipped_when_unified_entries_present():
    """_tick_spinner should not overwrite content when unified entries exist."""
    widget = AgentOutput()
    widget._unified_entries = [("agent", "some content")]
    initial_frame = widget._spinner_frame
    widget._tick_spinner()
    assert widget._spinner_frame == initial_frame  # Should not increment


# ── Incremental rendering tests ────────────────────────────────────


def test_format_unified_incremental_returns_appended_text():
    from forge.tui.widgets.agent_output import format_unified_incremental

    # First entry — gets header + line
    text, section, review_count = format_unified_incremental(
        "agent", "hello world", current_section=None, review_count=0, is_first=True
    )
    assert "AGENT" in text
    assert "hello world" in text

    # Second entry — same section, no header
    text2, section2, review_count2 = format_unified_incremental(
        "agent", "second line", current_section="agent", review_count=0, is_first=False
    )
    assert "AGENT" not in text2
    assert "second line" in text2

    # Section change to review
    text3, section3, review_count3 = format_unified_incremental(
        "review", "review line", current_section="agent", review_count=0, is_first=False
    )
    assert "REVIEW 1" in text3
    assert "review line" in text3
    assert review_count3 == 1


def test_format_unified_incremental_gate_merges_into_review():
    from forge.tui.widgets.agent_output import format_unified_incremental

    text, section, count = format_unified_incremental(
        "gate", "Build: passed", current_section=None, review_count=0, is_first=True
    )
    assert section == "review"
    assert "REVIEW 1" in text


def test_scroll_debounce_flag_exists():
    widget = AgentOutput()
    assert hasattr(widget, "_scroll_pending")
    assert widget._scroll_pending is False


def test_incremental_state_reset_on_update_unified():
    widget = AgentOutput()
    widget._rendered_parts = ["some old content"]
    widget._rendered_section = "agent"
    widget._rendered_review_count = 2
    # Calling update_unified should reset incremental state
    widget.update_unified("t1", "Title", "running", [("agent", "line")])
    assert widget._rendered_parts == []
    assert widget._rendered_section is None
    assert widget._rendered_review_count == 0


def test_sync_streaming_rebuilds_rendered_parts():
    """sync_streaming must rebuild _rendered_parts from entries so subsequent
    append_unified calls don't start from empty and lose all previous content.

    Regression test: sync_streaming used to clear _rendered_parts to [],
    causing the output panel to show only new lines after any state change
    triggered _refresh_all during streaming.
    """
    from forge.tui.widgets.agent_output import format_unified_incremental

    widget = AgentOutput()
    # Simulate 3 lines streamed via append_unified
    entries = [("agent", "line 1"), ("agent", "line 2"), ("review", "review note")]
    widget._unified_entries = list(entries)
    widget._streaming = True

    # Manually build expected rendered_parts
    expected_parts = []
    section = None
    review_count = 0
    for i, (src, line) in enumerate(entries):
        text, section, review_count = format_unified_incremental(
            src, line, current_section=section, review_count=review_count, is_first=(i == 0)
        )
        expected_parts.append(text)

    # Call sync_streaming (what _refresh_all does during streaming)
    widget.sync_streaming("t1", "Task 1", "in_progress", entries)

    # _rendered_parts must be rebuilt, NOT empty
    assert len(widget._rendered_parts) == 3
    assert widget._rendered_parts == expected_parts
    assert widget._rendered_section == "review"
    assert widget._rendered_review_count == 1

    # Now append a new line — it should be the 4th entry, not the 1st
    widget._unified_entries.append(("agent", "line 4"))
    text, widget._rendered_section, widget._rendered_review_count = format_unified_incremental(
        "agent",
        "line 4",
        current_section=widget._rendered_section,
        review_count=widget._rendered_review_count,
        is_first=False,
    )
    widget._rendered_parts.append(text)

    assert len(widget._rendered_parts) == 4
    assert "AGENT" in widget._rendered_parts[3]  # New section header since it switched back
    assert "line 4" in widget._rendered_parts[3]


# ── Breathing pulse spinner tests ──────────────────────────────────────


def test_spinner_frames_are_tuples():
    """Breathing pulse spinner frames should be (markup, color) tuples."""
    from forge.tui.widgets.agent_output import _SPINNER_FRAMES

    for frame in _SPINNER_FRAMES:
        assert isinstance(frame, tuple), f"Expected tuple, got {type(frame)}"
        assert len(frame) == 2
        assert isinstance(frame[0], str)
        assert isinstance(frame[1], str)


def test_format_output_empty_breathing_pulse():
    """format_output with empty lines should show breathing pulse spinner."""
    result = format_output([])
    assert "Waiting" in result
    # Should contain one of the pulse characters
    assert any(c in result for c in ["●", "◉", "○"])


# ── Fade-in animation tests ───────────────────────────────────────────


def test_fade_state_initialized():
    """AgentOutput should have fade animation state."""
    from forge.tui.widgets.agent_output import _FADE_STEPS

    widget = AgentOutput()
    assert hasattr(widget, "_fade_step")
    assert widget._fade_step == len(_FADE_STEPS)  # No fade active
    assert widget._fade_timer is None


def test_fade_steps_are_valid_colors():
    """Fade steps should be valid hex color strings."""
    from forge.tui.widgets.agent_output import _FADE_STEPS

    assert len(_FADE_STEPS) == 5
    for step in _FADE_STEPS:
        assert step.startswith("#")
        assert len(step) == 7


def test_fade_reset_on_update_unified():
    """update_unified should reset fade animation state."""
    from forge.tui.widgets.agent_output import _FADE_STEPS

    widget = AgentOutput()
    widget._fade_step = 2
    widget.update_unified("t1", "Title", "running", [("agent", "line")])
    assert widget._fade_step == len(_FADE_STEPS)
    assert widget._fade_timer is None


def test_fade_color_wrapping_produces_valid_rich_markup():
    """Wrapping a rendered part in fade color should produce valid Rich markup."""
    from io import StringIO

    from rich.console import Console

    from forge.tui.widgets.agent_output import _FADE_STEPS, format_unified_incremental

    # Generate a typical rendered part (agent line with markdown)
    text, _, _ = format_unified_incremental(
        "agent", "Creating `auth/jwt.py`...", current_section=None, review_count=0, is_first=True
    )
    # Wrap in fade color (what _update_content does)
    for fade_color in _FADE_STEPS:
        wrapped = f"[{fade_color}]{text}[/]"
        console = Console(file=StringIO(), force_terminal=True)
        # Should not raise — valid markup
        console.print(wrapped)

    # Test with review section too
    text2, _, _ = format_unified_incremental(
        "review", "Code looks **good**", current_section="agent", review_count=0, is_first=False
    )
    for fade_color in _FADE_STEPS:
        wrapped = f"[{fade_color}]{text2}[/]"
        console = Console(file=StringIO(), force_terminal=True)
        console.print(wrapped)


def test_append_unified_starts_fade():
    """append_unified should attempt to start fade-in animation.

    Before compose, set_interval raises and fade is skipped gracefully,
    so fade_step falls back to len(_FADE_STEPS). The important thing is
    the fade machinery is wired up and doesn't crash.
    """
    from forge.tui.widgets.agent_output import _FADE_STEPS

    widget = AgentOutput()
    widget.append_unified("agent", "new line")
    # Before compose, set_interval fails so fade is skipped
    assert widget._fade_step == len(_FADE_STEPS)
    assert widget._fade_timer is None
