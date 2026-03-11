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


@pytest.mark.asyncio
async def test_agent_output_fast_path_skips_refresh_all():
    """agent_output field triggers fast path, not full _refresh_all."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        # Set up a plan with a selected task
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
        })
        await pilot.pause()
        screen = app.screen
        with patch.object(screen, "_refresh_all") as mock_refresh:
            state.apply_event("task:agent_output", {"task_id": "t1", "line": "streaming line"})
            await pilot.pause()
            # _refresh_all should NOT have been called for agent_output
            mock_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_agent_output_fast_path_calls_append_line():
    """agent_output fast path uses append_line on AgentOutput widget."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
        })
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        with patch.object(agent_output, "append_line") as mock_append:
            state.apply_event("task:agent_output", {"task_id": "t1", "line": "hello"})
            await pilot.pause()
            mock_append.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_agent_output_fast_path_enables_streaming():
    """First agent_output event sets streaming to True."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
        })
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        with patch.object(agent_output, "set_streaming") as mock_stream:
            state.apply_event("task:agent_output", {"task_id": "t1", "line": "first"})
            await pilot.pause()
            mock_stream.assert_called_with(True)


@pytest.mark.asyncio
async def test_review_output_fast_path_skips_refresh_all():
    """review_output field triggers fast path, not full _refresh_all."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
        })
        await pilot.pause()
        screen = app.screen
        with patch.object(screen, "_refresh_all") as mock_refresh:
            state.apply_event("review:llm_output", {"task_id": "t1", "line": "reviewing..."})
            await pilot.pause()
            mock_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_stops_on_task_done():
    """Streaming indicator stops when task state changes to done."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
        })
        await pilot.pause()
        # Start streaming
        state.apply_event("task:agent_output", {"task_id": "t1", "line": "working..."})
        await pilot.pause()
        screen = app.screen
        assert "t1" in screen._agent_streaming_tasks
        # Complete the task
        agent_output = app.screen.query_one("AgentOutput")
        with patch.object(agent_output, "set_streaming") as mock_stream:
            state.apply_event("task:state_changed", {"task_id": "t1", "state": "done"})
            await pilot.pause()
            mock_stream.assert_called_with(False)
        assert "t1" not in screen._agent_streaming_tasks
