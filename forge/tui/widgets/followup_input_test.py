"""Tests for FollowUpInput widget."""
from __future__ import annotations

from unittest.mock import MagicMock


from forge.tui.widgets.followup_input import (
    FollowUpInput,
    format_context_badge,
    format_followup_history,
    DEFAULT_SUGGESTIONS,
)


def test_format_context_badge_with_branch():
    result = format_context_badge("feat/auth", 5)
    assert "feat/auth" in result
    assert "5 files" in result


def test_format_context_badge_single_file():
    result = format_context_badge("fix/bug", 1)
    assert "1 file changed" in result
    assert "files" not in result  # should not have "files" plural


def test_format_context_badge_empty_branch():
    result = format_context_badge("", 3)
    assert result == ""


def test_format_followup_history_empty():
    result = format_followup_history([])
    assert result == ""


def test_format_followup_history_with_entries():
    result = format_followup_history(["Add tests", "Fix lint errors"])
    assert "Previous follow-ups" in result
    assert "1. Add tests" in result
    assert "2. Fix lint errors" in result


def test_format_followup_history_truncates_long():
    long_prompt = "x" * 100
    result = format_followup_history([long_prompt])
    assert "..." in result
    assert len(result.split("\n")[1]) < 120  # truncated


def test_followup_input_defaults():
    widget = FollowUpInput()
    assert widget._branch == ""
    assert widget._files_changed == 0
    assert widget._suggestions == list(DEFAULT_SUGGESTIONS)
    assert widget._history == []


def test_followup_input_custom_params():
    widget = FollowUpInput(
        branch="feat/x",
        files_changed=10,
        suggestions=["Run tests"],
    )
    assert widget._branch == "feat/x"
    assert widget._files_changed == 10
    assert widget._suggestions == ["Run tests"]


def test_update_context():
    widget = FollowUpInput(branch="old", files_changed=1)
    mock_static = MagicMock()
    widget.query_one = MagicMock(return_value=mock_static)

    widget.update_context("new-branch", 7)

    assert widget._branch == "new-branch"
    assert widget._files_changed == 7
    mock_static.update.assert_called_once()
    call_arg = mock_static.update.call_args[0][0]
    assert "new-branch" in call_arg
    assert "7 files" in call_arg


def test_update_context_handles_missing_widget():
    widget = FollowUpInput()
    widget.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    widget.update_context("branch", 5)
    assert widget._branch == "branch"


def test_add_history():
    widget = FollowUpInput()
    mock_static = MagicMock()
    widget.query_one = MagicMock(return_value=mock_static)

    widget.add_history("Add tests for auth")

    assert widget._history == ["Add tests for auth"]
    mock_static.update.assert_called_once()


def test_add_history_multiple():
    widget = FollowUpInput()
    mock_static = MagicMock()
    widget.query_one = MagicMock(return_value=mock_static)

    widget.add_history("First follow-up")
    widget.add_history("Second follow-up")

    assert len(widget._history) == 2
    assert widget._history[0] == "First follow-up"
    assert widget._history[1] == "Second follow-up"


def test_focus_input():
    widget = FollowUpInput()
    mock_ta = MagicMock()
    widget.query_one = MagicMock(return_value=mock_ta)

    widget.focus_input()

    mock_ta.focus.assert_called_once()


def test_focus_input_handles_missing():
    widget = FollowUpInput()
    widget.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    widget.focus_input()


def test_submit_posts_message():
    widget = FollowUpInput(branch="feat/x", files_changed=3)
    mock_ta = MagicMock()
    mock_ta.text = "  Please add docs  "
    widget.query_one = MagicMock(return_value=mock_ta)
    widget.post_message = MagicMock()

    widget.submit()

    widget.post_message.assert_called_once()
    msg = widget.post_message.call_args[0][0]
    assert isinstance(msg, FollowUpInput.Submitted)
    assert msg.prompt == "Please add docs"
    assert msg.branch == "feat/x"
    assert msg.files_changed == 3
    mock_ta.clear.assert_called_once()


def test_submit_empty_text_does_nothing():
    widget = FollowUpInput(branch="feat/x", files_changed=3)
    mock_ta = MagicMock()
    mock_ta.text = "   "
    widget.query_one = MagicMock(return_value=mock_ta)
    widget.post_message = MagicMock()

    widget.submit()

    widget.post_message.assert_not_called()


def test_submit_adds_to_history():
    widget = FollowUpInput(branch="feat/x", files_changed=3)
    mock_ta = MagicMock()
    mock_ta.text = "Add tests"
    # Return mock_ta for TextArea, mock_static for history widget
    mock_static = MagicMock()

    def side_effect(*args, **kwargs):
        if args and args[0] == "#followup-history":
            return mock_static
        return mock_ta

    widget.query_one = MagicMock(side_effect=side_effect)
    widget.post_message = MagicMock()

    widget.submit()

    assert "Add tests" in widget._history


def test_suggestion_chip_selected():
    widget = FollowUpInput(branch="feat/y", files_changed=2)
    widget.post_message = MagicMock()
    # Mock add_history to avoid widget queries
    mock_static = MagicMock()
    widget.query_one = MagicMock(return_value=mock_static)

    from forge.tui.widgets.suggestion_chips import SuggestionChips
    event = SuggestionChips.Selected("Add tests")

    widget.on_suggestion_chips_selected(event)

    widget.post_message.assert_called_once()
    msg = widget.post_message.call_args[0][0]
    assert isinstance(msg, FollowUpInput.Submitted)
    assert msg.prompt == "Add tests"
    assert msg.branch == "feat/y"
    assert "Add tests" in widget._history


def test_submitted_message_fields():
    msg = FollowUpInput.Submitted("do stuff", "main", 5)
    assert msg.prompt == "do stuff"
    assert msg.branch == "main"
    assert msg.files_changed == 5


def test_followup_input_clear_action_clears_text():
    """action_clear_input() should clear the text area contents."""
    widget = FollowUpInput(branch="feat/x", files_changed=3)
    mock_ta = MagicMock()
    widget.query_one = MagicMock(return_value=mock_ta)

    widget.action_clear_input()

    mock_ta.clear.assert_called_once()


def test_followup_input_clear_action_handles_missing_widget():
    """action_clear_input() should not raise if the text area is missing."""
    widget = FollowUpInput()
    widget.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    widget.action_clear_input()
