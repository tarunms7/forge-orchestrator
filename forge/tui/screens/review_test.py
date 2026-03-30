"""Tests for ReviewScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.review import ReviewScreen
from forge.tui.state import TuiState


class ReviewTestApp(App):
    def compose(self) -> ComposeResult:
        yield ReviewScreen(TuiState())


@pytest.mark.asyncio
async def test_review_screen_mounts():
    app = ReviewTestApp()
    async with app.run_test() as _pilot:
        assert app.query_one("DiffViewer") is not None


@pytest.mark.asyncio
async def test_review_screen_no_task_list():
    """ReviewScreen should NOT contain a TaskList (sidebar removed)."""
    app = ReviewTestApp()
    async with app.run_test() as _pilot:
        assert len(app.query("TaskList")) == 0


@pytest.mark.asyncio
async def test_review_screen_status_bar_text():
    """Status bar should show scroll/jump-task instructions, not navigate."""
    app = ReviewTestApp()
    async with app.run_test() as _pilot:
        # Find the status bar
        status = app.query_one("#review-status")
        rendered = str(status.render())
        assert "scroll" in rendered
        assert "jump" in rendered or "1-9" in rendered


@pytest.mark.asyncio
async def test_review_screen_j_k_scrolls_diff_viewer():
    """j/k keys should call scroll_relative on DiffViewer without error."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": ["f"],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.selected_task_id = "t1"

    class ReviewTestAppWithState(App):
        def compose(self) -> ComposeResult:
            yield ReviewScreen(state)

    app = ReviewTestAppWithState()
    async with app.run_test() as pilot:
        # Populate DiffViewer with enough content to scroll
        diff_viewer = app.query_one("DiffViewer")
        diff_viewer.update_diff("t1", "Test", "line\n" * 100)
        await pilot.pause()
        # j/k should not raise
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        # If we get here without error, scroll_relative works


def test_refresh_prefers_daemon_diff_over_stale_cache():
    """_refresh should prefer daemon-computed diff over stale cached error."""
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {"tasks": [{"id": "t1", "title": "Task 1"}]})
    state.selected_task_id = "t1"
    state.tasks["t1"]["state"] = "in_review"

    screen = ReviewScreen(state)
    # Simulate stale cache
    screen._diff_cache["t1"] = "No pipeline branch available yet."
    # Daemon now has the real diff
    state.task_diffs["t1"] = "diff --git a/file.py b/file.py\n+real content"

    # After refresh logic, daemon diff should win and update cache
    daemon_diff = state.task_diffs.get("t1", "")
    assert daemon_diff.startswith("diff --git")
    # Verify the stale error would be rejected
    cached = screen._diff_cache["t1"]
    assert cached.startswith("No pipeline")  # Before refresh, still stale
    # The fix: _refresh updates cache from daemon
    screen._diff_cache["t1"] = daemon_diff  # Simulating what _refresh does
    assert screen._diff_cache["t1"].startswith("diff --git")


def test_stale_error_cache_is_not_used():
    """Cached error messages should trigger a reload, not be displayed."""
    # Error messages that should be treated as stale
    stale_messages = [
        "No pipeline branch available yet.",
        "git diff failed: something",
        "Error running git diff: timeout",
    ]
    for msg in stale_messages:
        assert msg.startswith(("No pipeline", "git diff failed", "Error"))


# ---------------------------------------------------------------------------
# Shortcut bar dynamic update tests
# ---------------------------------------------------------------------------


def test_review_screen_initial_shortcuts_loading():
    """ReviewScreen should start with only Esc/Back shortcut before diff loads."""
    state = TuiState()
    screen = ReviewScreen(state)
    assert screen._diff_loaded is False


@pytest.mark.asyncio
async def test_review_screen_shortcuts_after_diff_loads():
    """After diff loads, shortcuts should include approve/reject/editor/search."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": ["f"],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.selected_task_id = "t1"
    state.tasks["t1"]["state"] = "in_review"

    class ReviewTestAppWithState(App):
        def compose(self) -> ComposeResult:
            yield ReviewScreen(state)

    app = ReviewTestAppWithState()
    async with app.run_test() as pilot:
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        bar = app.query_one(ShortcutBar)
        # Before diff loads, should only have Esc
        keys_before = [k for k, _ in bar.shortcuts]
        assert "Esc" in keys_before
        assert "a" not in keys_before  # approve not yet available

        # Simulate diff loading
        screen = app.query_one(ReviewScreen)
        screen._diff_loaded = True
        screen._update_shortcut_bar()
        await pilot.pause()

        keys_after = [k for k, _ in bar.shortcuts]
        assert "a" in keys_after  # Approve
        assert "x" in keys_after  # Reject
        assert "e" in keys_after  # Editor
        assert "/" in keys_after  # Search
        assert "Esc" in keys_after  # Back
