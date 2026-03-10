"""Pipeline screen — split-pane task list + agent output."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal
from textual.binding import Binding

from forge.tui.state import TuiState
from forge.tui.widgets.task_list import TaskList
from forge.tui.widgets.agent_output import AgentOutput
from forge.tui.widgets.progress_bar import PipelineProgress
from forge.tui.widgets.dag import DagOverlay


class PipelineScreen(Screen):
    """Main pipeline execution screen with split-pane layout."""

    DEFAULT_CSS = """
    PipelineScreen {
        layout: vertical;
    }
    #split-pane {
        height: 1fr;
    }
    TaskList {
        width: 1fr;
        min-width: 30;
        max-width: 45;
        border-right: tall #30363d;
        padding: 1 1;
    }
    AgentOutput {
        width: 3fr;
        padding: 1 1;
        border-left: tall #30363d;
    }
    PipelineProgress {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        border-top: tall #30363d;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "toggle_dag", "Toggle DAG"),
        Binding("tab", "cycle_agent", "Next agent", show=False),
    ]

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        yield DagOverlay()
        with Horizontal(id="split-pane"):
            yield TaskList()
            yield AgentOutput()
        yield PipelineProgress()

    def on_mount(self) -> None:
        self._state.on_change(self._on_state_change)
        self._refresh_all()

    def _on_state_change(self, field: str) -> None:
        if field in ("tasks", "agent_output", "cost", "phase", "elapsed", "planner_output"):
            self._refresh_all()
        if field == "error":
            error = self._state.error
            if error:
                self.app.notify(f"Pipeline error: {error}", severity="error", timeout=10)

    def _refresh_all(self) -> None:
        state = self._state
        task_list = self.query_one(TaskList)
        agent_output = self.query_one(AgentOutput)
        progress = self.query_one(PipelineProgress)
        dag = self.query_one(DagOverlay)

        ordered_tasks = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        task_list.update_tasks(ordered_tasks, state.selected_task_id, phase=state.phase)

        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            lines = state.agent_output.get(tid, [])
            agent_output.update_output(tid, task.get("title"), task.get("state"), lines)
        elif state.phase == "planning" and state.planner_output:
            agent_output.update_output("planner", "Planning", "planning", state.planner_output)
        else:
            agent_output.update_output(None, None, None, [])

        progress.update_progress(
            state.done_count, state.total_count,
            state.total_cost_usd, state.elapsed_seconds, state.phase,
        )
        dag.update_tasks(ordered_tasks)

    def on_task_list_selected(self, event: TaskList.Selected) -> None:
        self._state.selected_task_id = event.task_id
        self._refresh_all()

    def action_cursor_down(self) -> None:
        self.query_one(TaskList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(TaskList).action_cursor_up()

    def action_toggle_dag(self) -> None:
        self.query_one(DagOverlay).toggle()

    def action_cycle_agent(self) -> None:
        active = self._state.active_task_ids
        if not active:
            return
        current = self._state.selected_task_id
        if current in active:
            idx = (active.index(current) + 1) % len(active)
        else:
            idx = 0
        self._state.selected_task_id = active[idx]
        self._refresh_all()
