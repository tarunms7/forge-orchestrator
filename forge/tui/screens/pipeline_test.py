"""Tests for PipelineScreen."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App

from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.state import TuiState


class PipelineTestApp(App):
    NOTIFICATIONS: list = []

    def __init__(self, state: TuiState | None = None, read_only: bool = False) -> None:
        super().__init__()
        self._tui_state = state or TuiState()
        self._read_only = read_only
        self._bus = MagicMock()
        self._bus.emit = AsyncMock()

    def on_mount(self) -> None:
        self.push_screen(PipelineScreen(self._tui_state, read_only=self._read_only))


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
        await pilot.pause()
        screen = app.screen
        with patch.object(screen, "_refresh_all") as mock_refresh:
            state.apply_event("task:agent_output", {"task_id": "t1", "line": "streaming line"})
            await pilot.pause()
            # _refresh_all should NOT have been called for agent_output
            mock_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_agent_output_fast_path_calls_append_unified():
    """agent_output fast path uses append_unified on AgentOutput widget."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
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
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        with patch.object(agent_output, "append_unified") as mock_append:
            state.apply_event("task:agent_output", {"task_id": "t1", "line": "hello"})
            await pilot.pause()
            mock_append.assert_called_once_with("agent", "hello")


@pytest.mark.asyncio
async def test_agent_output_fast_path_enables_streaming():
    """First agent_output event sets streaming to True."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
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


# ── Key binding tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t_key_opens_chat_view():
    """Pressing 't' should switch to chat view (relocated from 'c')."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        screen = app.screen
        await pilot.press("t")
        assert screen._active_view == "chat"


@pytest.mark.asyncio
async def test_o_key_opens_output_view():
    """Pressing 'o' should switch to output view (unchanged)."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.press("d")  # Switch away to diff first
        await pilot.press("o")
        assert app.screen._active_view == "output"


@pytest.mark.asyncio
async def test_d_key_opens_diff_view():
    """Pressing 'd' should switch to diff view (unchanged)."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.press("d")
        assert app.screen._active_view == "diff"


# ── Error recovery action tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_retry_noop_when_not_error():
    """action_retry_task is a no-op when selected task is not in error state."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "in_progress"})
    app = PipelineTestApp(state=state)
    async with app.run_test():
        screen = app.screen
        # Should not crash or emit anything
        screen.action_retry_task()
        # Bus emit should not have been called with task:retry
        app._bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_skip_noop_when_not_error():
    """action_skip_task is a no-op when selected task is not in error state."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "done"})
    app = PipelineTestApp(state=state)
    async with app.run_test():
        screen = app.screen
        screen.action_skip_task()
        app._bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_retry_emits_when_error():
    """action_retry_task emits task:retry when task is in error state."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "error", "error": "fail"})
    app = PipelineTestApp(state=state)
    async with app.run_test():
        screen = app.screen
        screen.action_retry_task()
        app._bus.emit.assert_called_once_with("task:retry", {"task_id": "t1"})


@pytest.mark.asyncio
async def test_skip_emits_when_error():
    """action_skip_task emits task:skip when task is in error state."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "error", "error": "fail"})
    app = PipelineTestApp(state=state)
    async with app.run_test():
        screen = app.screen
        screen.action_skip_task()
        app._bus.emit.assert_called_once_with("task:skip", {"task_id": "t1"})


# ── Read-only mode tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_only_mode_disables_retry():
    """In read-only mode, retry is a no-op even for error tasks."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "error", "error": "fail"})
    app = PipelineTestApp(state=state, read_only=True)
    async with app.run_test():
        screen = app.screen
        screen.action_retry_task()
        app._bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_mode_disables_skip():
    """In read-only mode, skip is a no-op even for error tasks."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "error", "error": "fail"})
    app = PipelineTestApp(state=state, read_only=True)
    async with app.run_test():
        screen = app.screen
        screen.action_skip_task()
        app._bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_shows_banner():
    """In read-only mode, PhaseBanner shows the read-only banner text."""
    from forge.tui.screens.pipeline import PhaseBanner

    state = TuiState()
    state._replay_date = "2026-03-10T12:00:00"
    app = PipelineTestApp(state=state, read_only=True)
    async with app.run_test():
        banner = app.screen.query_one(PhaseBanner)
        rendered = banner.render()
        assert "Viewing pipeline" in rendered


# ── Contracts display tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_contracts_streaming_output():
    """contracts:output events stream into AgentOutput during contracts phase."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "contracts"})
        state.apply_event("contracts:output", {"line": "Building contracts..."})
        state.apply_event("contracts:output", {"line": "Analyzing interfaces..."})
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        assert agent_output._task_id == "contracts"
        assert len(agent_output._lines) == 2
        assert "Building contracts..." in agent_output._lines[0]


@pytest.mark.asyncio
async def test_contracts_fallback_placeholder():
    """Without contracts_output, contracts phase shows fallback placeholder."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "contracts"})
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        assert agent_output._task_id == "contracts"
        assert any("Building API contracts" in line for line in agent_output._lines)


# ── Error detail display tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_error_task_shows_error_detail():
    """Selecting an errored task renders error detail view."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": "t1",
                    "title": "Test Task",
                    "description": "",
                    "files": [],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event(
        "task:state_changed", {"task_id": "t1", "state": "error", "error": "Build failed"}
    )
    app = PipelineTestApp(state=state)
    async with app.run_test():
        agent_output = app.screen.query_one("AgentOutput")
        assert agent_output.is_error_mode


# ── PhaseBanner wide-spacing tests ──────────────────────────────


def test_phase_banner_wide_spacing():
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    banner._phase = "planning"
    rendered = banner.render()
    # Should contain wide-spaced "P L A N N I N G"
    assert "P  L  A  N  N  I  N  G" in rendered


def test_phase_banner_multiword_wide_spacing():
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    banner._phase = "planned"
    rendered = banner.render()
    # "Plan Ready" → "P L A N   R E A D Y" (triple-space between words)
    assert "P  L  A  N" in rendered
    assert "R  E  A  D  Y" in rendered


def test_phase_banner_icon_preserved():
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    banner._phase = "executing"
    rendered = banner.render()
    assert "⚡" in rendered
    assert "E  X  E  C  U  T  I  O  N" in rendered


# ── Dynamic sidebar tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_sidebar_hidden_during_planning():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert split_pane.has_class("full-width")


@pytest.mark.asyncio
async def test_sidebar_shown_during_execution():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert not split_pane.has_class("full-width")


@pytest.mark.asyncio
async def test_sidebar_hidden_during_complete():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "complete"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert split_pane.has_class("full-width")


@pytest.mark.asyncio
async def test_sidebar_shown_during_error():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "error"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert not split_pane.has_class("full-width")
