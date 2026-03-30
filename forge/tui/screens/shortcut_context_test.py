"""Tests for context-aware shortcut bars across all screens."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App

from forge.tui.screens.final_approval import FinalApprovalScreen
from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.screens.plan_approval import PlanApprovalScreen
from forge.tui.screens.review import ReviewScreen
from forge.tui.state import TuiState
from forge.tui.widgets.shortcut_bar import ShortcutBar


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class PipelineTestApp(App):
    def __init__(self, state: TuiState | None = None, read_only: bool = False) -> None:
        super().__init__()
        self._tui_state = state or TuiState()
        self._read_only = read_only
        self._bus = MagicMock()
        self._bus.emit = AsyncMock()

    def on_mount(self) -> None:
        self.push_screen(PipelineScreen(self._tui_state, read_only=self._read_only))


def _make_task(tid: str, title: str = "Task") -> dict:
    return {
        "id": tid,
        "title": title,
        "description": "",
        "files": ["f.py"],
        "depends_on": [],
        "complexity": "low",
    }


def _setup_task_with_state(state: TuiState, tid: str, title: str, task_state: str) -> None:
    """Set up a task via plan_ready then change its state."""
    state.apply_event("pipeline:plan_ready", {"tasks": [_make_task(tid, title)]})
    if task_state != "todo":
        state.apply_event("task:state_changed", {"task_id": tid, "state": task_state})


# ---------------------------------------------------------------------------
# Pipeline screen: shortcuts change based on selected task state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_shortcuts_executing_in_progress():
    """During executing phase with in_progress task, shortcuts include interject/diff/output."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "in_progress")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "i" in keys  # interject
        assert "d" in keys  # diff
        assert "o" in keys  # output
        assert "Tab" in keys  # next active


@pytest.mark.asyncio
async def test_pipeline_shortcuts_executing_error():
    """During executing phase with error task, shortcuts include retry/skip."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "error")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "R" in keys  # retry
        assert "s" in keys  # skip


@pytest.mark.asyncio
async def test_pipeline_shortcuts_executing_done():
    """During executing phase with done task, shortcuts include diff/output but not interject."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "done")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "d" in keys
        assert "o" in keys
        assert "i" not in keys  # no interject for done tasks


@pytest.mark.asyncio
async def test_pipeline_shortcuts_planning_phase():
    """During planning phase, shortcuts are minimal (no task-specific actions)."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        # Should not have task-specific actions
        assert "i" not in keys
        assert "R" not in keys
        assert "r" not in keys
        # Should still have basics
        assert "g" in keys  # DAG
        assert "q" in keys  # quit


@pytest.mark.asyncio
async def test_pipeline_shortcuts_in_review():
    """During executing phase with in_review task, shortcuts include review/diff."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "in_review")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "r" in keys  # review
        assert "d" in keys  # diff


# ---------------------------------------------------------------------------
# Pipeline screen: guarded action handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_review_guard_shows_notification():
    """Pressing r when task is not in_review shows a warning notification."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "in_progress")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_open_review()
            await pilot.pause()
            mock_notify.assert_called_once()
            assert "not available" in str(mock_notify.call_args)


@pytest.mark.asyncio
async def test_pipeline_retry_guard_shows_notification():
    """Pressing R when task is not error shows a warning notification."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "done")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_retry_task()
            await pilot.pause()
            mock_notify.assert_called_once()
            assert "not available" in str(mock_notify.call_args)


@pytest.mark.asyncio
async def test_pipeline_skip_guard_shows_notification():
    """Pressing s when task is not error shows a warning notification."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "in_progress")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_skip_task()
            await pilot.pause()
            mock_notify.assert_called_once()
            assert "not available" in str(mock_notify.call_args)


@pytest.mark.asyncio
async def test_pipeline_diff_guard_shows_notification():
    """Pressing d when task is in todo state shows a warning notification."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        _setup_task_with_state(state, "t1", "Build", "todo")
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_view_diff()
            await pilot.pause()
            mock_notify.assert_called_once()
            assert "not available" in str(mock_notify.call_args)


# ---------------------------------------------------------------------------
# Final approval screen: shortcuts change between partial and full mode
# ---------------------------------------------------------------------------


class FinalApprovalTestApp(App):
    def __init__(self, partial: bool = False) -> None:
        super().__init__()
        self._partial = partial

    def on_mount(self) -> None:
        self.push_screen(
            FinalApprovalScreen(
                stats={"added": 10, "removed": 2, "files": 3, "elapsed": "1m", "cost": 0.1, "questions": 0},
                tasks=[{"title": "Task A", "state": "done", "added": 10, "removed": 2, "files": 3, "tests_passed": 5, "tests_total": 5}],
                pipeline_branch="feat/test",
                partial=self._partial,
            )
        )


@pytest.mark.asyncio
async def test_final_approval_full_mode_shortcuts():
    """Full mode shows Create PR, Diff, Follow-up, New Task, Back."""
    app = FinalApprovalTestApp(partial=False)
    async with app.run_test() as pilot:
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "Enter" in keys
        assert "d" in keys
        assert "n" in keys
        # Should not have retry/skip
        assert "r" not in keys
        assert "s" not in keys


@pytest.mark.asyncio
async def test_final_approval_partial_mode_shortcuts():
    """Partial mode shows Create PR, Retry, Skip, etc."""
    app = FinalApprovalTestApp(partial=True)
    async with app.run_test() as pilot:
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "Enter" in keys
        assert "r" in keys
        assert "s" in keys
        # Should not have new task
        assert "n" not in keys


@pytest.mark.asyncio
async def test_final_approval_shortcuts_after_pr_creation():
    """After PR created, shortcuts remove Create PR and add Done."""
    app = FinalApprovalTestApp(partial=False)
    async with app.run_test() as pilot:
        screen = app.screen
        screen.show_pr_url("https://github.com/test/repo/pull/1")
        await pilot.pause()
        bar = screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        labels = [l for _, l in bar.shortcuts]
        # Create PR should be removed
        assert "Enter" not in keys
        # Done should be present
        assert "Done" in labels


# ---------------------------------------------------------------------------
# Plan approval screen: shortcuts change in edit mode
# ---------------------------------------------------------------------------


class PlanApprovalTestApp(App):
    def on_mount(self) -> None:
        tasks = [
            {"id": "t1", "title": "Add auth", "description": "JWT auth", "files": ["auth.py"], "complexity": "high", "depends_on": []},
            {"id": "t2", "title": "Add routes", "description": "REST endpoints", "files": ["routes.py"], "complexity": "medium", "depends_on": ["t1"]},
        ]
        self.push_screen(PlanApprovalScreen(tasks))


@pytest.mark.asyncio
async def test_plan_approval_normal_mode_shortcuts():
    """Normal mode shows Approve, Edit, Files, etc."""
    app = PlanApprovalTestApp()
    async with app.run_test() as pilot:
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "Enter" in keys
        assert "e" in keys
        assert "x" in keys
        assert "a" in keys


@pytest.mark.asyncio
async def test_plan_approval_edit_mode_shortcuts():
    """Edit mode shows only Ctrl+S and Cancel Edit."""
    app = PlanApprovalTestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        # Enter edit mode
        screen.action_edit_task()
        await pilot.pause()
        bar = screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        labels = [l for _, l in bar.shortcuts]
        assert "Ctrl+S" in keys
        assert "Cancel Edit" in labels
        # Normal mode keys should be gone
        assert "Enter" not in keys
        assert "e" not in keys


@pytest.mark.asyncio
async def test_plan_approval_back_to_normal_after_cancel():
    """Canceling edit restores normal mode shortcuts."""
    app = PlanApprovalTestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        # Enter edit mode
        screen.action_edit_task()
        await pilot.pause()
        # Cancel edit
        screen.action_cancel_or_close()
        await pilot.pause()
        bar = screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "Enter" in keys
        assert "Ctrl+S" not in keys


# ---------------------------------------------------------------------------
# Review screen: shortcuts change after diff loads
# ---------------------------------------------------------------------------


class ReviewTestApp(App):
    def __init__(self, state: TuiState | None = None) -> None:
        super().__init__()
        self._state = state or TuiState()

    def on_mount(self) -> None:
        self.push_screen(ReviewScreen(self._state))


@pytest.mark.asyncio
async def test_review_screen_loading_shortcuts():
    """Before diff loads, only Esc/Back is shown."""
    state = TuiState()
    app = ReviewTestApp(state=state)
    async with app.run_test() as pilot:
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "Esc" in keys
        # Should not have review actions yet
        assert "a" not in keys
        assert "x" not in keys


@pytest.mark.asyncio
async def test_review_screen_loaded_shortcuts():
    """After diff loads, approve/reject/search shortcuts appear."""
    state = TuiState()
    # Set up a task with a diff already available
    _setup_task_with_state(state, "t1", "Build", "in_review")
    state.task_diffs["t1"] = "diff --git a/f.py b/f.py\n+hello"
    app = ReviewTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "a" in keys  # approve
        assert "x" in keys  # reject
        assert "/" in keys  # search
        assert "Esc" in keys
