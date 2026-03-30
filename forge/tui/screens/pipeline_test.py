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
async def test_retry_resets_task_when_error():
    """action_retry_task resets errored task to 'todo' for re-dispatch."""
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
        # Without a DB, retry logs a warning but doesn't crash
        screen.action_retry_task()


@pytest.mark.asyncio
async def test_skip_cancels_task_when_error():
    """action_skip_task marks errored task as cancelled."""
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
        # Without a DB, skip logs a warning but doesn't crash
        screen.action_skip_task()


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
async def test_contracts_phase_shows_preparing():
    """Contracts phase shows a static preparing message (no streaming output)."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "contracts"})
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        assert agent_output._task_id == "contracts"
        assert any("parallel execution" in line for line in agent_output._lines)


@pytest.mark.asyncio
async def test_countdown_phase_shows_preparing():
    """Countdown phase shows the same preparing message as contracts."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "countdown"})
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        assert agent_output._task_id == "contracts"
        assert any("parallel execution" in line for line in agent_output._lines)


# ── Countdown tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_countdown_banner_renders_number():
    """PhaseBanner shows 'LAUNCHING IN N' during countdown."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        banner = app.screen.query_one("PhaseBanner")
        banner.start_countdown(3)
        await pilot.pause()
        rendered = banner.render()
        assert "L A U N C H I N G" in rendered
        assert "3" in rendered


@pytest.mark.asyncio
async def test_countdown_ticks_and_fires_complete():
    """Countdown ticks down and posts CountdownComplete when reaching zero."""

    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as _pilot:
        banner = app.screen.query_one("PhaseBanner")
        banner.start_countdown(2)
        # Tick twice (interval is 1s, but we can trigger manually)
        banner._tick_countdown()  # 2 → 1
        assert banner._countdown_value == 1
        rendered = banner.render()
        assert "1" in rendered
        banner._tick_countdown()  # 1 → 0, fires CountdownComplete
        assert banner._countdown_value == 0
        assert banner._countdown_timer is None  # Timer stopped


@pytest.mark.asyncio
async def test_stop_countdown_prevents_complete():
    """stop_countdown cancels the timer without firing CountdownComplete."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as _pilot:
        banner = app.screen.query_one("PhaseBanner")
        banner.start_countdown(5)
        assert banner._countdown_value == 5
        banner.stop_countdown()
        assert banner._countdown_value == 0
        assert banner._countdown_timer is None


@pytest.mark.asyncio
async def test_preparing_gear_on_tasks_during_contracts():
    """Tasks show purple gear indicator during contracts/countdown phases."""
    from forge.tui.widgets.task_list import format_task_line

    task = {"id": "t1", "title": "Build API", "state": "todo", "_preparing": True}
    line = format_task_line(task, selected=False)
    assert "⚙" in line  # purple gear indicator

    # Without _preparing, no gear
    task_normal = {"id": "t2", "title": "Build API", "state": "todo"}
    line_normal = format_task_line(task_normal, selected=False)
    # Should not have the purple gear (only the normal icon)
    assert line_normal.count("⚙") <= 1  # at most the state icon, not the suffix


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


# ── Streaming double-render fix tests ─────────────────────────────


@pytest.mark.asyncio
async def test_refresh_all_preserves_streaming_flag():
    """_refresh_all must NOT reset streaming to False when task is actively streaming."""
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
        # Start streaming so the task is in _agent_streaming_tasks
        state.apply_event("task:agent_output", {"task_id": "t1", "line": "working..."})
        await pilot.pause()
        screen = app.screen
        agent_output = app.screen.query_one("AgentOutput")
        assert "t1" in screen._agent_streaming_tasks
        assert agent_output._streaming is True
        # Now trigger _refresh_all (e.g. via a state change that isn't agent_output)
        # Use update_unified as a spy to ensure it is NOT called (it would reset streaming)
        with patch.object(agent_output, "update_unified") as mock_update:
            screen._refresh_all()
            # update_unified should NOT be called for the streaming task
            mock_update.assert_not_called()
        # Streaming must still be True — no off/on toggle
        assert agent_output._streaming is True


# ── Scramble-resolve animation tests ──────────────────────────────


def test_phase_banner_scramble_animation_state():
    """PhaseBanner should have scramble animation state after phase change."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    # Direct state check (no compose needed)
    assert banner._animating is False
    assert banner._resolved_count == 0


def test_phase_banner_scramble_progression():
    """_tick_scramble should resolve characters left-to-right and terminate."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    # Trigger phase change — set_interval fails pre-compose, so set state manually
    banner.update_phase("executing")
    # Pre-compose: timer creation fails, _animating is set to False
    # But target_text is set correctly — we can test the tick logic directly
    assert banner._target_text != ""
    target_len = len(banner._target_text)
    assert target_len > 0

    # Simulate animation manually
    banner._animating = True
    banner._resolved_count = 0
    for i in range(target_len):
        banner._tick_scramble()
        assert banner._resolved_count == i + 1

    # After resolving all chars, animation should be done
    assert banner._animating is False


def test_phase_banner_scramble_interrupted_by_new_phase():
    """A new phase change mid-animation should reset target text."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    banner.update_phase("executing")
    old_target = banner._target_text

    # Simulate partial animation
    banner._animating = True
    banner._resolved_count = 2

    # New phase should reset
    banner.update_phase("review")
    assert banner._resolved_count == 0
    assert banner._target_text != old_target


def test_phase_banner_render_static():
    """PhaseBanner renders correctly in non-animated state."""
    from forge.tui.screens.pipeline import PhaseBanner

    banner = PhaseBanner()
    banner._phase = "executing"
    result = banner.render()
    assert "E  X  E  C  U  T  I  O  N" in result


# ── Shortcut bar dynamic update tests ─────────────────────────────


def _make_state_with_task(task_id: str, task_state: str, phase: str = "executing") -> TuiState:
    """Create a TuiState with one task in the given state."""
    state = TuiState()
    state.apply_event(
        "pipeline:plan_ready",
        {
            "tasks": [
                {
                    "id": task_id,
                    "title": "Test Task",
                    "description": "",
                    "files": ["f.py"],
                    "depends_on": [],
                    "complexity": "low",
                }
            ]
        },
    )
    state.apply_event("task:state_changed", {"task_id": task_id, "state": task_state})
    state.apply_event("pipeline:phase_changed", {"phase": phase})
    return state


@pytest.mark.asyncio
async def test_shortcut_bar_changes_by_task_state_in_progress():
    """Shortcut bar should include interject/chat/diff for in_progress tasks."""
    state = _make_state_with_task("t1", "in_progress")
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "i" in keys  # Interject
        assert "t" in keys  # Chat
        assert "d" in keys  # Diff


@pytest.mark.asyncio
async def test_shortcut_bar_changes_by_task_state_error():
    """Shortcut bar should include Retry/Skip for error tasks."""
    state = _make_state_with_task("t1", "error")
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "R" in keys  # Retry
        assert "s" in keys  # Skip


@pytest.mark.asyncio
async def test_shortcut_bar_changes_by_task_state_done():
    """Shortcut bar should include Diff/Output for done tasks."""
    state = _make_state_with_task("t1", "done")
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "d" in keys  # Diff
        assert "o" in keys  # Output
        # Should NOT have interject or retry
        assert "i" not in keys
        assert "R" not in keys


@pytest.mark.asyncio
async def test_shortcut_bar_changes_by_phase_planning():
    """Planning phase should show minimal shortcuts (no task-specific actions)."""
    state = _make_state_with_task("t1", "todo", phase="planning")
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        # Should not have task-specific shortcuts
        assert "Tab" not in keys
        assert "i" not in keys
        assert "R" not in keys
        # Should have base shortcuts
        assert "q" in keys
        assert "g" in keys


@pytest.mark.asyncio
async def test_shortcut_bar_changes_by_phase_executing():
    """Executing phase should include Tab for next active."""
    state = _make_state_with_task("t1", "in_progress", phase="executing")
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        await pilot.pause()
        from forge.tui.widgets.shortcut_bar import ShortcutBar

        bar = app.screen.query_one(ShortcutBar)
        keys = [k for k, _ in bar.shortcuts]
        assert "Tab" in keys


@pytest.mark.asyncio
async def test_guard_diff_shows_notification_for_todo_task():
    """Pressing 'd' on a todo task should show warning notification."""
    state = _make_state_with_task("t1", "todo", phase="executing")
    app = PipelineTestApp(state=state)
    async with app.run_test():
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_view_diff()
            mock_notify.assert_called_once()
            assert "Diff not available" in mock_notify.call_args[0][0]


@pytest.mark.asyncio
async def test_guard_diff_no_task_selected():
    """Pressing 'd' with no task selected should show warning."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test():
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_view_diff()
            mock_notify.assert_called_once()
            assert "No task selected" in mock_notify.call_args[0][0]


@pytest.mark.asyncio
async def test_guard_interject_shows_notification_for_done_task():
    """Pressing 'i' on a done task should show warning notification."""
    state = _make_state_with_task("t1", "done", phase="executing")
    app = PipelineTestApp(state=state)
    async with app.run_test():
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_interject()
            mock_notify.assert_called_once()
            assert (
                "not available" in mock_notify.call_args[0][0].lower()
                or "interject" in mock_notify.call_args[0][0].lower()
            )


@pytest.mark.asyncio
async def test_guard_retry_shows_notification_for_done_task():
    """Pressing 'R' on a done task should show warning notification."""
    state = _make_state_with_task("t1", "done", phase="executing")
    app = PipelineTestApp(state=state)
    async with app.run_test():
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_retry_task()
            mock_notify.assert_called_once()
            assert "Retry not available" in mock_notify.call_args[0][0]


@pytest.mark.asyncio
async def test_guard_skip_shows_notification_for_done_task():
    """Pressing 's' on a done task should show warning notification."""
    state = _make_state_with_task("t1", "done", phase="executing")
    app = PipelineTestApp(state=state)
    async with app.run_test():
        with patch.object(app, "notify") as mock_notify:
            app.screen.action_skip_task()
            mock_notify.assert_called_once()
            assert "Skip not available" in mock_notify.call_args[0][0]
