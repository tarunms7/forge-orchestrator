"""Tests for FinalApprovalScreen."""

from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from rich.console import Console
from textual.app import App, ComposeResult

from forge.tui.screens.final_approval import (
    DiffScreen,
    FinalApprovalScreen,
    _format_launch_status,
    format_summary_stats,
    format_task_table,
)
from forge.tui.widgets.followup_input import FollowUpInput, FollowUpTextArea


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


def test_format_task_table_escapes_validation_errors_without_markup_error():
    tasks = [
        {
            "title": "Retry summary [review]",
            "state": "error",
            "error": (
                "Input should be 'completed' or 'failed' "
                "[type=literal_error, input_value='in_progress', input_type=str]"
            ),
        }
    ]
    result = format_task_table(tasks)
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)
    assert "\\[type=literal_error" in result
    assert "Retry summary \\[review\\]" in result


def test_format_launch_status_escapes_dynamic_values():
    result = _format_launch_status(
        "forge/topic[debug]",
        "main[release]",
        per_repo_pr_urls={"repo[one]": "https://example.com/pr/[42]"},
    )
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)
    assert "repo\\[one\\]" in result
    assert "https://example.com/pr/\\[42\\]" in result


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
    """show_pr_url should update the launch status with the PR URL."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    # Mock query_one to return a mock Static widget
    mock_widget = MagicMock()
    screen.query_one = MagicMock(return_value=mock_widget)

    screen.show_pr_url("https://github.com/org/repo/pull/42")

    # query_one is called for #pr-url and ShortcutBar (for _update_shortcut_bar)
    assert screen.query_one.call_count >= 1
    assert mock_widget.update.call_count >= 1
    call_arg = mock_widget.update.call_args_list[-1].args[0]
    assert "https://github.com/org/repo/pull/42" in call_arg
    assert "PR live" in call_arg


def test_show_pr_url_handles_missing_widget():
    """show_pr_url should not raise if widget is missing."""
    screen = FinalApprovalScreen(stats={}, tasks=[])
    screen.query_one = MagicMock(side_effect=Exception("no widget"))
    # Should not raise
    screen.show_pr_url("https://example.com/pull/1")


def test_show_pipeline_target_updates_widgets():
    """show_pipeline_target should refresh banner/status copy once branch resolves."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="")
    mock_widget = MagicMock()
    screen.query_one = MagicMock(return_value=mock_widget)

    screen.show_pipeline_target("forge/ready", "main")

    updated = " ".join(call.args[0] for call in mock_widget.update.call_args_list)
    assert "forge/ready" in updated
    assert "main" in updated


def test_action_view_diff_no_branch_notifies():
    """action_view_diff with no branch should notify a warning."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="")
    screen.notify = MagicMock()
    screen.action_view_diff()
    screen.notify.assert_called_once()
    assert "No pipeline branch" in screen.notify.call_args[0][0]


class FinalApprovalRerunApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.rerun_count = 0

    def compose(self) -> ComposeResult:
        yield FinalApprovalScreen(
            stats={},
            tasks=[{"title": "Fix auth", "state": "error", "error": "limit reached"}],
            pipeline_branch="",
            partial=True,
        )

    def on_final_approval_screen_rerun(self, event) -> None:
        self.rerun_count += 1


@pytest.mark.asyncio
async def test_r_binding_emits_rerun_message_in_partial_mode():
    app = FinalApprovalRerunApp()
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert app.rerun_count == 1


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


def test_action_create_pr_submits_followup_when_composer_focused():
    """Enter should submit the follow-up instead of opening the PR when composer is focused."""
    screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
    screen.action_submit_followup = MagicMock()
    screen.post_message = MagicMock()

    with patch.object(
        FinalApprovalScreen, "focused", new_callable=PropertyMock, return_value=FollowUpTextArea()
    ):
        screen.action_create_pr()

    screen.action_submit_followup.assert_called_once()
    screen.post_message.assert_not_called()


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


# ---------------------------------------------------------------------------
# Shortcut bar dynamic update tests
# ---------------------------------------------------------------------------


class TestFinalApprovalShortcutBar:
    """Test that shortcut bar updates based on mode and PR state."""

    def test_full_mode_shortcuts(self):
        """Full mode (not partial) should include Create PR, Diff, Follow-up, New Task."""
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")

        captured: list[list[tuple[str, str]]] = []
        mock_bar = MagicMock(spec=ShortcutBar)
        mock_bar.update_shortcuts = lambda s: captured.append(list(s))
        screen.query_one = MagicMock(return_value=mock_bar)

        screen._update_shortcut_bar()
        shortcuts = captured[-1]
        keys = [k for k, _ in shortcuts]
        labels = [label for _, label in shortcuts]
        assert "Enter" in keys
        assert "Create PR" in labels
        assert "d" in keys
        assert "n" in keys  # New Task
        assert "Esc" in keys

    def test_partial_mode_shortcuts(self):
        """Partial mode should include Retry Failed and Skip & Finish."""
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x", partial=True)

        captured: list[list[tuple[str, str]]] = []
        mock_bar = MagicMock(spec=ShortcutBar)
        mock_bar.update_shortcuts = lambda s: captured.append(list(s))
        screen.query_one = MagicMock(return_value=mock_bar)

        screen._update_shortcut_bar()
        shortcuts = captured[-1]
        keys = [k for k, _ in shortcuts]
        labels = [label for _, label in shortcuts]
        assert "r" in keys  # Retry Failed
        assert "s" in keys  # Skip & Finish
        assert "Retry Failed" in labels
        assert "Skip & Finish" in labels

    def test_followup_focus_shortcuts(self):
        """When the composer is focused, the shortcut bar should show follow-up actions."""
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")

        captured: list[list[tuple[str, str]]] = []
        mock_bar = MagicMock(spec=ShortcutBar)
        mock_bar.update_shortcuts = lambda s: captured.append(list(s))
        screen.query_one = MagicMock(return_value=mock_bar)

        with patch.object(
            FinalApprovalScreen,
            "focused",
            new_callable=PropertyMock,
            return_value=FollowUpTextArea(),
        ):
            screen._update_shortcut_bar()

        shortcuts = captured[-1]
        assert ("Enter", "Submit follow-up") in shortcuts
        assert ("Ctrl+S", "Submit follow-up") in shortcuts
        assert ("Ctrl+U", "Clear") in shortcuts
        assert ("Esc", "Back to actions") in shortcuts

    def test_shortcuts_after_pr_created(self):
        """After PR is created, shortcuts should remove Create PR and add Done."""
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")

        captured: list[list[tuple[str, str]]] = []
        mock_bar = MagicMock(spec=ShortcutBar)
        mock_bar.update_shortcuts = lambda s: captured.append(list(s))
        screen.query_one = MagicMock(return_value=mock_bar)

        # Before PR creation
        screen._update_shortcut_bar()
        before = captured[-1]
        before_keys = [k for k, _ in before]
        assert "Enter" in before_keys  # Create PR available

        # After PR creation
        screen._pr_created = True
        screen._update_shortcut_bar()
        after = captured[-1]
        after_keys = [k for k, _ in after]
        after_labels = [label for _, label in after]
        assert "Enter" not in after_keys  # Create PR removed
        assert "Done" in after_labels  # Done added

    def test_create_pr_action_disabled_after_pr_created(self):
        """Create PR should be disabled once the PR already exists."""
        screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
        assert screen.check_action("create_pr", ()) is True
        screen._pr_created = True
        assert screen.check_action("create_pr", ()) is False

    def test_show_pr_url_updates_shortcuts(self):
        """show_pr_url should set _pr_created and update shortcut bar."""
        screen = FinalApprovalScreen(stats={}, tasks=[], pipeline_branch="feat/x")
        mock_widget = MagicMock()
        screen.query_one = MagicMock(return_value=mock_widget)

        assert screen._pr_created is False
        screen.show_pr_url("https://github.com/org/repo/pull/42")
        assert screen._pr_created is True


# --- Task enrichment tests ---


def test_compact_successful_task_single_line():
    """Done task with retry_count=0 should have exactly one line per task."""
    tasks = [
        {
            "title": "Auth middleware",
            "state": "done",
            "added": 89,
            "removed": 4,
            "retry_count": 0,
        },
    ]
    result = format_task_table(tasks)
    lines = result.split('\n')
    # Should have exactly one line (no detail line for successful task with no retries)
    assert len(lines) == 1
    assert "Auth middleware" in result
    assert "succeeded after" not in result


def test_successful_task_with_retries_shows_detail():
    """Done task with retry_count > 0 should show retry detail line."""
    tasks = [
        {
            "title": "Cache layer",
            "state": "done",
            "added": 45,
            "removed": 2,
            "retry_count": 2,
        },
    ]
    result = format_task_table(tasks)
    lines = result.split('\n')
    # Should have two lines (main + detail)
    assert len(lines) == 2
    assert "Cache layer" in result
    assert "succeeded after 2 retries" in result


def test_blocked_task_no_duplicate_detail():
    """Blocked task should show blocked reason but no duplicate second line."""
    tasks = [
        {
            "title": "API endpoints",
            "state": "blocked",
            "error": "dependency failed",
            "retry_count": 1,
        },
    ]
    result = format_task_table(tasks)
    lines = result.split('\n')
    # Should have exactly one line (no detail line for blocked)
    assert len(lines) == 1
    assert "API endpoints" in result
    assert "dependency failed" in result


def test_in_review_task_shows_substatus():
    """in_review task with review_substatus should show substatus detail line."""
    tasks = [
        {
            "title": "Database schema",
            "state": "in_review",
            "added": 23,
            "removed": 1,
            "review_substatus": "🔨 Build running",
        },
    ]
    result = format_task_table(tasks)
    lines = result.split('\n')
    # Should have two lines (main + substatus detail)
    assert len(lines) == 2
    assert "Database schema" in result
    assert "🔨 Build running" in result


def test_merging_task_shows_substatus():
    """merging task with merge_substatus should show substatus detail line."""
    tasks = [
        {
            "title": "Test framework",
            "state": "merging",
            "added": 78,
            "removed": 5,
            "merge_substatus": "rebasing",
            "review_gates": {},
        },
    ]
    result = format_task_table(tasks)
    lines = result.split('\n')
    # Should have two lines (main + substatus detail)
    assert len(lines) == 2
    assert "Test framework" in result
    assert "rebasing" in result


def test_error_task_with_retries_shows_count():
    """Error task with retry_count > 0 should show retry count detail line."""
    tasks = [
        {
            "title": "Config parser",
            "state": "error",
            "error": "validation failed",
            "retry_count": 3,
        },
    ]
    result = format_task_table(tasks)
    lines = result.split('\n')
    # Should have two lines (main + retry detail)
    assert len(lines) == 2
    assert "Config parser" in result
    assert "validation failed" in result
    assert "3 retries attempted" in result


def test_summary_stats_with_retries():
    """Stats with total_retries > 0 should show retries in cost line."""
    stats = {
        "added": 100,
        "removed": 20,
        "files": 8,
        "elapsed": "5m 30s",
        "cost": 0.25,
        "questions": 1,
        "total_retries": 5,
    }
    result = format_summary_stats(stats)
    assert "5 retries" in result
    assert "$0.25 cost" in result


def test_summary_stats_with_blocked():
    """Stats with blocked_count > 0 should show blocked in cost line."""
    stats = {
        "added": 50,
        "removed": 10,
        "files": 3,
        "elapsed": "3m 15s",
        "cost": 0.15,
        "questions": 0,
        "blocked_count": 2,
    }
    result = format_summary_stats(stats)
    assert "2 blocked" in result


def test_summary_stats_clean_no_extra_chips():
    """Stats with no retries/blocked should not show those stats."""
    stats = {
        "added": 75,
        "removed": 5,
        "files": 4,
        "elapsed": "2m 45s",
        "cost": 0.10,
        "questions": 2,
        "total_retries": 0,
        "blocked_count": 0,
        "skipped_count": 0,
    }
    result = format_summary_stats(stats)
    assert "retries" not in result
    assert "blocked" not in result
    assert "skipped" not in result
    assert "$0.10 cost" in result
