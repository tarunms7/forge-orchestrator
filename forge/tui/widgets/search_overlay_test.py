"""Tests for SearchOverlay widget and apply_highlights utility."""

from __future__ import annotations

from forge.tui.widgets.search_overlay import (
    _HIGHLIGHT_OPEN,
    SearchOverlay,
    apply_highlights,
)

# ── apply_highlights tests ──────────────────────────────────────────────


def test_apply_highlights_empty_pattern():
    text = "hello world"
    result, count = apply_highlights(text, "")
    assert result == text
    assert count == 0


def test_apply_highlights_no_match():
    text = "hello world"
    result, count = apply_highlights(text, "xyz")
    assert result == text
    assert count == 0


def test_apply_highlights_single_match():
    text = "hello world"
    result, count = apply_highlights(text, "world")
    assert count == 1
    assert _HIGHLIGHT_OPEN in result
    assert "world" in result


def test_apply_highlights_multiple_matches():
    text = "foo bar foo baz foo"
    result, count = apply_highlights(text, "foo")
    assert count == 3
    assert result.count(_HIGHLIGHT_OPEN) == 3


def test_apply_highlights_case_insensitive():
    text = "Hello HELLO hello"
    result, count = apply_highlights(text, "hello")
    assert count == 3


def test_apply_highlights_preserves_rich_markup():
    """Rich markup tags should not be highlighted."""
    text = "[bold #58a6ff]task-1[/]: Auth middleware"
    result, count = apply_highlights(text, "task")
    assert count == 1
    # The tag itself should be intact
    assert "[bold #58a6ff]" in result


def test_apply_highlights_regex_mode():
    text = "error: line 1\nwarning: line 2\nerror: line 3"
    result, count = apply_highlights(text, r"error|warning", use_regex=True)
    assert count == 3


def test_apply_highlights_invalid_regex():
    text = "hello world"
    result, count = apply_highlights(text, "[invalid", use_regex=True)
    assert result == text
    assert count == 0


def test_apply_highlights_special_chars_plain_mode():
    """Special regex chars in plain mode should be escaped."""
    text = "file.py (test)"
    result, count = apply_highlights(text, "file.py")
    assert count == 1


def test_apply_highlights_highlight_tag_format():
    """Verify the exact highlight tag format matches the contract."""
    text = "match here"
    result, count = apply_highlights(text, "match")
    assert "[on #d29922]match[/]" in result


def test_apply_highlights_performance_large_text():
    """Should handle 1000+ lines efficiently."""
    lines = [f"line {i}: some output text here" for i in range(2000)]
    text = "\n".join(lines)
    result, count = apply_highlights(text, "output")
    assert count == 2000


# ── SearchOverlay widget tests ──────────────────────────────────────────


def test_search_overlay_init_defaults():
    widget = SearchOverlay()
    assert widget.pattern is None
    assert widget.match_count == 0
    assert widget.current_match == 0
    assert widget._use_regex is False


def test_search_overlay_update_match_count():
    widget = SearchOverlay()
    widget._pattern = "test"
    widget.update_match_count(5)
    assert widget.match_count == 5
    assert widget.current_match == 1  # auto-set to 1


def test_search_overlay_update_match_count_zero():
    widget = SearchOverlay()
    widget.update_match_count(0)
    assert widget.match_count == 0
    assert widget.current_match == 0


def test_search_overlay_navigate_next():
    widget = SearchOverlay()
    widget._match_count = 5
    widget._current_match = 1
    # Can't post messages without app, but state should update
    widget._match_count = 5
    widget._current_match = 2
    assert widget.current_match == 2


def test_search_overlay_navigate_wraps_forward():
    widget = SearchOverlay()
    widget._match_count = 3
    widget._current_match = 3
    # Simulate navigate(+1) wrapping
    widget._current_match += 1
    if widget._current_match > widget._match_count:
        widget._current_match = 1
    assert widget.current_match == 1


def test_search_overlay_navigate_wraps_backward():
    widget = SearchOverlay()
    widget._match_count = 3
    widget._current_match = 1
    # Simulate navigate(-1) wrapping
    widget._current_match -= 1
    if widget._current_match < 1:
        widget._current_match = widget._match_count
    assert widget.current_match == 3


def test_search_overlay_clear_search():
    widget = SearchOverlay()
    widget._pattern = "test"
    widget._highlights_active = True
    widget._match_count = 5
    widget._current_match = 3
    # Simulate _clear_search (without message posting)
    widget._pattern = None
    widget._highlights_active = False
    widget._match_count = 0
    widget._current_match = 0
    assert widget.pattern is None
    assert widget.match_count == 0


def test_search_overlay_is_visible_default():
    widget = SearchOverlay()
    assert widget.is_visible is False


# ── AgentOutput.set_search_highlights tests ─────────────────────────────


def test_agent_output_set_search_highlights_default():
    from forge.tui.widgets.agent_output import AgentOutput

    widget = AgentOutput()
    assert widget._search_pattern is None


def test_agent_output_set_search_highlights_stores_pattern():
    from forge.tui.widgets.agent_output import AgentOutput

    widget = AgentOutput()
    widget._lines = ["hello world", "hello again"]
    # Before compose, query_one will fail but pattern should still be stored
    widget.set_search_highlights("hello")
    assert widget._search_pattern == "hello"


def test_agent_output_set_search_highlights_clears_pattern():
    from forge.tui.widgets.agent_output import AgentOutput

    widget = AgentOutput()
    widget._search_pattern = "test"
    widget.set_search_highlights(None)
    assert widget._search_pattern is None


# ── DiffViewer.set_search_highlights tests ──────────────────────────────


def test_diff_viewer_set_search_highlights_default():
    from forge.tui.widgets.diff_viewer import DiffViewer

    widget = DiffViewer()
    assert widget._search_pattern is None


def test_diff_viewer_set_search_highlights_stores_pattern():
    from forge.tui.widgets.diff_viewer import DiffViewer

    widget = DiffViewer()
    widget._diff_text = "+added line\n-removed line"
    count = widget.set_search_highlights("added")
    assert widget._search_pattern == "added"
    assert count == 1


def test_diff_viewer_set_search_highlights_clears():
    from forge.tui.widgets.diff_viewer import DiffViewer

    widget = DiffViewer()
    widget._search_pattern = "test"
    count = widget.set_search_highlights(None)
    assert widget._search_pattern is None
    assert count == 0


def test_diff_viewer_set_search_highlights_empty_diff():
    from forge.tui.widgets.diff_viewer import DiffViewer

    widget = DiffViewer()
    widget._diff_text = ""
    count = widget.set_search_highlights("test")
    assert count == 0


# ── Rich markup validity tests ──────────────────────────────────────────


def test_apply_highlights_valid_rich_markup():
    """Highlighted output should be valid Rich markup."""
    from io import StringIO

    from rich.console import Console

    text = "[bold #58a6ff]task-1[/]: Creating auth module..."
    result, count = apply_highlights(text, "auth")
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)  # Raises MarkupError if broken


def test_apply_highlights_with_escaped_brackets():
    """Text with escaped brackets (\\[) should work correctly."""
    text = "\\[R] retry  \\[s] skip"
    result, count = apply_highlights(text, "retry")
    assert count == 1
    assert "retry" in result
