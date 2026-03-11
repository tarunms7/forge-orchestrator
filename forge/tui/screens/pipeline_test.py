"""Tests for PipelineScreen."""

from __future__ import annotations

import pytest
from unittest.mock import patch
from textual.app import App

from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.state import TuiState


class PipelineTestApp(App):
    NOTIFICATIONS: list = []

    def __init__(self, state: TuiState | None = None) -> None:
        super().__init__()
        self._tui_state = state or TuiState()

    def on_mount(self) -> None:
        self.push_screen(PipelineScreen(self._tui_state))


@pytest.mark.asyncio
async def test_pipeline_screen_mounts():
    app = PipelineTestApp()
    async with app.run_test():
        assert app.screen.query_one("TaskList") is not None
        assert app.screen.query_one("AgentOutput") is not None
        assert app.screen.query_one("PipelineProgress") is not None
        assert app.screen.query_one("PhaseBanner") is not None


@pytest.mark.asyncio
async def test_phase_banner_is_outside_split_pane():
    """PhaseBanner must be a direct child of PipelineScreen, not inside #split-pane."""
    from forge.tui.screens.pipeline import PhaseBanner
    app = PipelineTestApp()
    async with app.run_test():
        screen = app.screen
        phase_banner = screen.query_one(PhaseBanner)
        split_pane = screen.query_one("#split-pane")
        # PhaseBanner should NOT be a descendant of split-pane
        assert phase_banner not in split_pane.query(PhaseBanner)
        # PhaseBanner's parent should be the screen itself
        assert phase_banner.parent is screen


@pytest.mark.asyncio
async def test_phase_banner_not_in_left_panel():
    """PhaseBanner must not be inside #left-panel."""
    from forge.tui.screens.pipeline import PhaseBanner
    app = PipelineTestApp()
    async with app.run_test():
        left_panel = app.screen.query_one("#left-panel")
        # left-panel should not contain any PhaseBanner
        assert len(left_panel.query(PhaseBanner)) == 0


@pytest.mark.asyncio
async def test_pipeline_screen_dag_toggle():
    app = PipelineTestApp()
    async with app.run_test() as pilot:
        dag = app.screen.query_one("DagOverlay")
        assert not dag.has_class("visible")
        await pilot.press("g")
        assert dag.has_class("visible")
        await pilot.press("g")
        assert not dag.has_class("visible")


@pytest.mark.asyncio
async def test_pipeline_error_shows_notification():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        with patch.object(app, "notify") as mock_notify:
            state.apply_event("pipeline:error", {"error": "Something went wrong"})
            await pilot.pause()
            mock_notify.assert_called_once_with(
                "Pipeline error: Something went wrong",
                severity="error",
                timeout=10,
            )


@pytest.mark.asyncio
async def test_pipeline_error_no_notification_when_none():
    """No notification when error field is set but value is None/empty."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        with patch.object(app, "notify") as mock_notify:
            # Manually set error to empty string and fire callback
            state.error = ""
            state._notify("error")
            await pilot.pause()
            mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_shows_planner_output_during_planning():
    """Planner output streams into AgentOutput during planning phase."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        state.apply_event("planner:output", {"line": "Reading forge/core/daemon.py..."})
        state.apply_event("planner:output", {"line": "Analyzing task dependencies..."})
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        assert agent_output._task_id == "planner"
        assert len(agent_output._lines) == 2
        assert "Reading forge/core/daemon.py..." in agent_output._lines[0]
