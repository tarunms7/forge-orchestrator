"""Tests for FinalApprovalScreen."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from forge.tui.screens.final_approval import (
    format_summary_stats,
    format_task_table,
    FinalApprovalScreen,
    DiffScreen,
)
from forge.tui.widgets.followup_input import FollowUpInput


def test_format_summary_stats():
    stats = {"added": 342, "removed": 28, "files": 12, "elapsed": "8m 23s", "cost": 0.42, "questions": 2}
    result = format_summary_stats(stats)
    assert "+342" in result
    assert "$0.42" in result


def test_format_task_table():
    tasks = [
        {"title": "JWT middleware", "added": 89, "removed": 4, "tests_passed": 14, "tests_total": 14, "review": "passed"},
    ]
    result = format_task_table(tasks)
    assert "JWT middleware" in result
    assert "14/14" in result


def test_final_approval_screen_accepts_pipeline_branch():
    screen = FinalApprovalScreen(
        stats={"added": 10, "removed": 2, "files": 3, "elapsed": "1m", "cost": 0.1, "questions": 0},
        tasks=[],
        pipeline_branch="forge/test-branch",
    )
    assert screen._pipeline_branch == "forge/test-branch"


def test_final_approval_screen_default_pipeline_branch():
    screen = FinalApprovalScreen()
    assert screen._pipeline_branch == ""


def test_show_pr_url_updates_widget():
    """show_pr_url should update the #pr-url Static widget."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    # Mock query_one to return a mock Static widget
    mock_widget = MagicMock()
    screen.query_one = MagicMock(return_value=mock_widget)

    screen.show_pr_url("https://github.com/org/repo/pull/42")

    screen.query_one.assert_called_once()
    mock_widget.update.assert_called_once()
    call_arg = mock_widget.update.call_args[0][0]
    assert "https://github.com/org/repo/pull/42" in call_arg
    assert "PR created" in call_arg


def test_show_pr_url_handles_missing_widget():
    """show_pr_url should not raise if widget is missing."""
    screen = FinalApprovalScreen(stats={}, tasks=[])
    screen.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    screen.show_pr_url("https://example.com/pull/1")


def test_show_pr_url_sets_pr_created_flag():
    """show_pr_url should set _pr_created and clear _pr_creating."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    screen.query_one = MagicMock(side_effect=Exception("no widget"))
    screen._pr_creating = True

    screen.show_pr_url("https://example.com/pull/1")

    assert screen._pr_created is True
    assert screen._pr_creating is False


def test_action_view_diff_no_branch_notifies():
    """action_view_diff with no branch should notify a warning."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="")
    screen.notify = MagicMock()
    screen.action_view_diff()
    screen.notify.assert_called_once()
    assert "No pipeline branch" in screen.notify.call_args[0][0]


@pytest.mark.asyncio
async def test_load_and_show_diff_success():
    """_load_and_show_diff should run git diff and push DiffScreen."""
    screen = FinalApprovalScreen(
        stats={}, tasks=[], pipeline_branch="forge/my-branch",
    )
    mock_app = MagicMock()
    screen._app = mock_app
    # Patch app property
    type(screen).app = property(lambda self: mock_app)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"+++ a/file.py\n+new line\n", b""))

    # Mock is_running so lifecycle guard doesn't block
    type(screen).is_running = property(lambda self: True)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await screen._load_and_show_diff()

    mock_app.push_screen.assert_called_once()
    pushed = mock_app.push_screen.call_args[0][0]
    assert isinstance(pushed, DiffScreen)
    assert "+new line" in pushed._diff_text


@pytest.mark.asyncio
async def test_load_and_show_diff_git_error():
    """_load_and_show_diff should show error text if git fails."""
    screen = FinalApprovalScreen(
        stats={}, tasks=[], pipeline_branch="forge/bad-branch",
    )
    mock_app = MagicMock()
    type(screen).app = property(lambda self: mock_app)

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: bad revision"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await screen._load_and_show_diff()

    pushed = mock_app.push_screen.call_args[0][0]
    assert "git diff failed" in pushed._diff_text


@pytest.mark.asyncio
async def test_load_and_show_diff_exception():
    """_load_and_show_diff should handle subprocess exceptions."""
    screen = FinalApprovalScreen(
        stats={}, tasks=[], pipeline_branch="forge/branch",
    )
    mock_app = MagicMock()
    type(screen).app = property(lambda self: mock_app)

    with patch("asyncio.create_subprocess_exec", side_effect=OSError("git not found")):
        await screen._load_and_show_diff()

    pushed = mock_app.push_screen.call_args[0][0]
    assert "Error running git diff" in pushed._diff_text


def test_diff_screen_stores_params():
    """DiffScreen should store diff text and branch."""
    ds = DiffScreen("diff content here", branch="feat/x")
    assert ds._diff_text == "diff content here"
    assert ds._branch == "feat/x"


def test_action_view_diff_with_branch_creates_task():
    """action_view_diff with a branch should schedule _load_and_show_diff."""
    screen = FinalApprovalScreen(
        stats={}, tasks=[], pipeline_branch="forge/branch",
    )
    with patch("asyncio.create_task") as mock_create_task:
        screen.action_view_diff()
        mock_create_task.assert_called_once()


# --- Follow-up tests ---


def test_followup_binding_exists():
    """FinalApprovalScreen should have 'ctrl+f' binding for follow-up."""
    bindings = {b.key: b for b in FinalApprovalScreen.BINDINGS}
    assert "ctrl+f" in bindings
    assert "follow" in bindings["ctrl+f"].description.lower()


def test_ctrl_s_binding_exists():
    """FinalApprovalScreen should have ctrl+s binding for submitting follow-up."""
    bindings = {b.key: b for b in FinalApprovalScreen.BINDINGS}
    assert "ctrl+s" in bindings


def test_action_focus_followup():
    """action_focus_followup should focus the FollowUpInput widget."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    mock_followup = MagicMock()
    screen.query_one = MagicMock(return_value=mock_followup)

    screen.action_focus_followup()

    mock_followup.focus_input.assert_called_once()


def test_action_focus_followup_handles_missing():
    """action_focus_followup should not raise if widget is missing."""
    screen = FinalApprovalScreen(stats={}, tasks=[])
    screen.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    screen.action_focus_followup()


def test_action_submit_followup():
    """action_submit_followup should call submit on FollowUpInput."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    mock_followup = MagicMock()
    screen.query_one = MagicMock(return_value=mock_followup)

    screen.action_submit_followup()

    mock_followup.submit.assert_called_once()


def test_action_submit_followup_handles_missing():
    """action_submit_followup should not raise if widget is missing."""
    screen = FinalApprovalScreen(stats={}, tasks=[])
    screen.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    screen.action_submit_followup()


def test_on_follow_up_input_submitted():
    """FollowUpInput.Submitted should be relayed as FinalApprovalScreen.FollowUp."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    screen.post_message = MagicMock()

    event = FollowUpInput.Submitted("add docs", "feat/x", 5)
    screen.on_follow_up_input_submitted(event)

    screen.post_message.assert_called_once()
    msg = screen.post_message.call_args[0][0]
    assert isinstance(msg, FinalApprovalScreen.FollowUp)
    assert msg.prompt == "add docs"
    assert msg.branch == "feat/x"
    assert msg.files_changed == 5


def test_followup_message_fields():
    """FinalApprovalScreen.FollowUp should store all fields."""
    msg = FinalApprovalScreen.FollowUp("refactor auth", "main", 3)
    assert msg.prompt == "refactor auth"
    assert msg.branch == "main"
    assert msg.files_changed == 3


def test_help_text_includes_followup():
    """The bindings should include follow up with ctrl+f."""
    bindings = {b.key: b for b in FinalApprovalScreen.BINDINGS}
    assert "ctrl+f" in bindings


# --- PR double-creation guard tests ---


def test_action_create_pr_posts_message():
    """action_create_pr should post CreatePR message on first call."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    screen.post_message = MagicMock()

    screen.action_create_pr()

    screen.post_message.assert_called_once()
    msg = screen.post_message.call_args[0][0]
    assert isinstance(msg, FinalApprovalScreen.CreatePR)


def test_action_create_pr_guard_prevents_double_press():
    """action_create_pr should not post a second message if already creating."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    screen.post_message = MagicMock()

    screen.action_create_pr()
    screen.action_create_pr()  # second press

    # Only one message should be posted
    screen.post_message.assert_called_once()


def test_action_create_pr_guard_prevents_after_created():
    """action_create_pr should not post if PR already created."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    screen.post_message = MagicMock()
    screen.query_one = MagicMock(side_effect=Exception("no widget"))

    screen.action_create_pr()
    screen.show_pr_url("https://example.com/pull/1")
    screen.post_message.reset_mock()

    screen.action_create_pr()  # should be blocked

    screen.post_message.assert_not_called()


def test_pr_creating_flag_initialized_false():
    """New FinalApprovalScreen should have _pr_creating=False."""
    screen = FinalApprovalScreen()
    assert screen._pr_creating is False
    assert screen._pr_created is False


# --- Keybinding update tests ---


def test_keybindings_use_ctrl_prefix():
    """All single-key shortcuts (except enter/escape) should use ctrl+ prefix."""
    bindings = {b.key: b for b in FinalApprovalScreen.BINDINGS}
    assert "ctrl+d" in bindings
    assert "ctrl+r" in bindings
    assert "ctrl+f" in bindings
    assert "ctrl+n" in bindings
    # These should NOT exist as single-key shortcuts
    assert "d" not in bindings
    assert "r" not in bindings
    assert "f" not in bindings
    assert "n" not in bindings
    # Enter and escape should remain
    assert "enter" in bindings
    assert "escape" in bindings
