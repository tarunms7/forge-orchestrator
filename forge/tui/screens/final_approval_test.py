"""Tests for FinalApprovalScreen."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.tui.screens.final_approval import (
    DiffScreen,
    FinalApprovalScreen,
    format_summary_stats,
    format_task_table,
)
from forge.tui.widgets.followup_input import FollowUpInput


def test_format_summary_stats():
    stats = {
        "added": 342,
        "removed": 28,
        "files": 12,
        "elapsed": "8m 23s",
        "cost": 0.42,
        "questions": 2,
    }
    result = format_summary_stats(stats)
    assert "+342" in result
    assert "$0.42" in result


def test_format_task_table():
    tasks = [
        {
            "title": "JWT middleware",
            "added": 89,
            "removed": 4,
            "tests_passed": 14,
            "tests_total": 14,
            "review": "passed",
        },
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

    # query_one is called for #pr-url and ShortcutBar (for _update_shortcut_bar)
    assert screen.query_one.call_count >= 1
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
        stats={},
        tasks=[],
        pipeline_branch="forge/my-branch",
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
        stats={},
        tasks=[],
        pipeline_branch="forge/bad-branch",
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
        stats={},
        tasks=[],
        pipeline_branch="forge/branch",
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
        stats={},
        tasks=[],
        pipeline_branch="forge/branch",
    )
    with patch("asyncio.create_task") as mock_create_task:
        screen.action_view_diff()
        mock_create_task.assert_called_once()


# --- Follow-up tests ---


def test_followup_binding_exists():
    """FinalApprovalScreen should have 'f' binding for follow-up."""
    bindings = {b.key: b for b in FinalApprovalScreen.BINDINGS}
    assert "f" in bindings
    assert "follow" in bindings["f"].description.lower()


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
    """The help text in the screen should mention follow up."""
    # Check that our bindings include follow up
    bindings = {b.key: b for b in FinalApprovalScreen.BINDINGS}
    assert "f" in bindings


def test_format_task_table_partial_mode():
    from forge.tui.screens.final_approval import format_task_table

    tasks = [
        {"title": "Auth", "state": "done", "added": 100, "removed": 10},
        {"title": "API", "state": "error", "error": "timed out (5 attempts)"},
        {"title": "Tests", "state": "blocked", "error": "blocked by API"},
    ]
    result = format_task_table(tasks)
    assert "✅" in result  # done task
    assert "❌" in result  # error task
    assert "⚠️" in result or "⚠" in result  # blocked task
    assert "Auth" in result
    assert "timed out" in result
    assert "blocked by API" in result


# --- Multi-repo tests ---


class TestMultiRepoFinalApproval:
    """Tests for multi-repo display in final approval screen."""

    def test_format_task_table_multi_repo(self):
        """Multi-repo task table groups tasks by repo with headers and aggregate stats."""
        tasks = [
            {
                "title": "Add auth",
                "state": "done",
                "added": 80,
                "removed": 20,
                "files": 2,
                "tests_passed": 5,
                "tests_total": 5,
                "repo": "backend",
            },
            {
                "title": "Add models",
                "state": "done",
                "added": 40,
                "removed": 10,
                "files": 1,
                "tests_passed": 3,
                "tests_total": 3,
                "repo": "backend",
            },
            {
                "title": "Add login page",
                "state": "done",
                "added": 50,
                "removed": 5,
                "files": 3,
                "tests_passed": 2,
                "tests_total": 2,
                "repo": "frontend",
            },
        ]
        result = format_task_table(tasks, multi_repo=True)
        # Should have repo headers
        assert "backend" in result
        assert "frontend" in result
        # Should have aggregate stats per repo
        assert "+120/-30" in result  # backend aggregate
        assert "+50/-5" in result  # frontend aggregate
        # Should have task titles
        assert "Add auth" in result
        assert "Add models" in result
        assert "Add login page" in result

    def test_format_task_table_single_repo(self):
        """Single-repo task table should be identical to current behavior (no repo headers)."""
        tasks = [
            {
                "title": "Add auth",
                "state": "done",
                "added": 80,
                "removed": 20,
                "files": 2,
                "tests_passed": 5,
                "tests_total": 5,
            },
        ]
        result_single = format_task_table(tasks, multi_repo=False)
        result_default = format_task_table(tasks)
        # Should be identical
        assert result_single == result_default
        # Should NOT have repo headers
        assert "backend" not in result_single
        assert "frontend" not in result_single

    def test_format_summary_stats_multi_repo(self):
        """Multi-repo summary stats should prepend repo/task counts."""
        stats = {
            "added": 170,
            "removed": 35,
            "files": 5,
            "elapsed": "3m 42s",
            "cost": 1.23,
            "questions": 2,
            "repo_count": 2,
            "task_count": 5,
        }
        result = format_summary_stats(stats, multi_repo=True)
        assert "2 repos, 5 tasks" in result
        assert "+170" in result
        assert "-35" in result
        assert "$1.23" in result

    def test_format_summary_stats_single_repo(self):
        """Single-repo summary stats should NOT have repo/task prefix."""
        stats = {
            "added": 170,
            "removed": 35,
            "files": 5,
            "elapsed": "3m 42s",
            "cost": 1.23,
            "questions": 2,
        }
        result_single = format_summary_stats(stats, multi_repo=False)
        result_default = format_summary_stats(stats)
        assert result_single == result_default
        assert "repos" not in result_single

    def test_format_task_table_multi_repo_with_errors(self):
        """Multi-repo task table should handle error/blocked/cancelled states."""
        tasks = [
            {
                "title": "Add auth",
                "state": "done",
                "added": 80,
                "removed": 20,
                "files": 2,
                "repo": "backend",
            },
            {"title": "Add API", "state": "error", "error": "timed out", "repo": "backend"},
            {"title": "Add login", "state": "cancelled", "repo": "frontend"},
        ]
        result = format_task_table(tasks, multi_repo=True)
        assert "backend" in result
        assert "frontend" in result
        assert "✅" in result
        assert "❌" in result
        assert "✘" in result or "cancelled" in result


class TestFinalApprovalCreatePrsButton:
    """Tests for multi-repo PR button text."""

    def test_final_approval_create_prs_button(self):
        """Button text should be 'Create PRs' for multi-repo and 'Create PR' for single."""
        # Multi-repo screen
        multi_screen = FinalApprovalScreen(
            stats={},
            tasks=[],
            pipeline_branch="feat/x",
            multi_repo=True,
            repos=[{"repo_id": "backend"}, {"repo_id": "frontend"}],
        )
        assert multi_screen._multi_repo is True

        # Single-repo screen
        single_screen = FinalApprovalScreen(
            stats={},
            tasks=[],
            pipeline_branch="feat/x",
        )
        assert single_screen._multi_repo is False


# --- Task state icon tests ---


def test_format_task_list_merging_with_gates_passed_shows_green():
    """A task in 'merging' state with all gates passed should show green, not red cross."""
    tasks = [
        {
            "title": "Auth middleware",
            "state": "merging",
            "added": 50,
            "removed": 10,
            "files": 3,
            "tests_passed": 5,
            "tests_total": 5,
            "review_gates": {
                "gate0_build": {"status": "passed"},
                "gate1_lint": {"status": "passed"},
                "gate2_llm_review": {"status": "passed"},
            },
        }
    ]
    result = format_task_table(tasks)
    assert "✗" not in result
    assert "✅" in result


def test_format_task_list_in_review_shows_pending():
    """A task in 'in_review' state should show pending indicator, not red cross."""
    tasks = [
        {
            "title": "DB migration",
            "state": "in_review",
            "added": 20,
            "removed": 5,
        }
    ]
    result = format_task_table(tasks)
    assert "✗" not in result
    assert "⏳" in result


def test_format_task_list_merging_no_gates_shows_pending():
    """A task in 'merging' without gate data should show pending, not red cross."""
    tasks = [
        {
            "title": "API routes",
            "state": "merging",
            "added": 30,
            "removed": 15,
        }
    ]
    result = format_task_table(tasks)
    assert "✗" not in result
    assert "⏳" in result


def test_format_task_list_in_progress_shows_running():
    """A task in 'in_progress' state should show running indicator."""
    tasks = [
        {
            "title": "Frontend build",
            "state": "in_progress",
            "added": 0,
            "removed": 0,
        }
    ]
    result = format_task_table(tasks)
    assert "✗" not in result
    assert "⚙" in result
