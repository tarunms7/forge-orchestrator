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
