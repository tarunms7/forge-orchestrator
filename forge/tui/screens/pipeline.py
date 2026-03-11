"""Pipeline screen — 2-panel layout: task list on left, view switching on right."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual.widget import Widget

from forge.tui.state import TuiState
from forge.tui.widgets.task_list import TaskList
from forge.tui.widgets.agent_output import AgentOutput
from forge.tui.widgets.progress_bar import PipelineProgress
from forge.tui.widgets.dag import DagOverlay
from forge.tui.widgets.chat_thread import ChatThread
from forge.tui.widgets.review_gates import ReviewGates
from forge.tui.widgets.diff_viewer import DiffViewer

# Map phase → display label and colour
_PHASE_BANNER: dict[str, tuple[str, str]] = {
    "idle":           ("Idle",              "#8b949e"),
    "planning":       ("◌ Planning",        "#58a6ff"),
    "planned":        ("◉ Plan Approval",   "#a371f7"),
    "contracts":      ("⚙ Contracts",       "#d2a8ff"),
    "executing":      ("⚡ Execution",       "#f0883e"),
    "in_progress":    ("⚡ Execution",       "#f0883e"),
    "review":         ("🔍 Review",          "#79c0ff"),
    "in_review":      ("🔍 Review",          "#79c0ff"),
    "final_approval": ("◎ Final Approval",  "#f0883e"),
    "pr_creating":    ("⚙ Creating PR",     "#d2a8ff"),
    "pr_created":     ("✔ PR Created",      "#3fb950"),
    "complete":       ("✔ Complete",        "#3fb950"),
    "error":          ("✖ Error",           "#f85149"),
}

_VIEW_NAMES = ("output", "chat", "diff", "review")


class PhaseBanner(Widget):
    """Full-width centered phase indicator displayed above the split pane."""

    DEFAULT_CSS = """
    PhaseBanner {
        width: 1fr;
        height: 3;
        content-align: center middle;
        text-align: center;
        background: #0d1117;
        border-bottom: tall #30363d;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._phase = "idle"

    def update_phase(self, phase: str) -> None:
        self._phase = phase
        self.refresh()

    def render(self) -> str:
        label, colour = _PHASE_BANNER.get(self._phase, ("Unknown", "#8b949e"))
        return f"[bold {colour}]{label}[/]"


class DecisionBadge(Widget):
    """Shows count of pending questions/decisions at bottom of left panel."""

    DEFAULT_CSS = """
    DecisionBadge {
        height: 1;
        padding: 0 1;
        background: #161b22;
        border-top: tall #30363d;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def update_count(self, count: int) -> None:
        self._count = count
        self.refresh()

    def render(self) -> str:
        if self._count == 0:
            return "[#484f58]No pending decisions[/]"
        return f"[bold #f0883e]● {self._count} decision{'s' if self._count != 1 else ''} pending[/]"


class ViewLabel(Widget):
    """Single-line header showing current right-panel view name and available shortcuts."""

    DEFAULT_CSS = """
    ViewLabel {
        height: 1;
        padding: 0 1;
        background: #161b22;
        border-bottom: tall #30363d;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._active = "output"

    def update_active(self, view: str) -> None:
        self._active = view
        self.refresh()

    def render(self) -> str:
        def _item(name: str, key: str, abbrev: str) -> str:
            if name == self._active:
                return f"[bold #f0883e][{key}]{abbrev}[/]"
            return f"[#484f58][{key}]{abbrev}[/]"

        return (
            _item("output", "o", "Output")
            + "  "
            + _item("chat", "c", "Chat")
            + "  "
            + _item("diff", "d", "Diff")
            + "  "
            + _item("review", "r", "Review")
        )


class PipelineScreen(Screen):
    """Main pipeline execution screen with full-width phase banner + 2-panel layout.

    Phase banner (full width, centered):
      - PhaseBanner

    Left panel (fixed ~35 cols):
      - TaskList
      - DecisionBadge

    Right panel (fills remaining space):
      - ViewLabel (tab-bar replacement)
      - AgentOutput   (view=output)
      - ChatThread    (view=chat)
      - DiffViewer    (view=diff)
      - ReviewGates   (view=review)
    """

    DEFAULT_CSS = """
    PipelineScreen {
        layout: vertical;
    }
    PipelineScreen > PhaseBanner {
        width: 100%;
        height: 3;
        content-align: center middle;
        text-align: center;
        background: #0d1117;
        border-bottom: tall #30363d;
    }
    #split-pane {
        height: 1fr;
    }
    #left-panel {
        width: 35;
        min-width: 30;
        max-width: 45;
        border-right: tall #30363d;
        layout: vertical;
    }
    #left-panel TaskList {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #right-panel {
        width: 1fr;
        layout: vertical;
    }
    #right-panel AgentOutput {
        width: 1fr;
        height: 1fr;
        padding: 1 1;
    }
    #right-panel ChatThread {
        width: 1fr;
        height: 1fr;
    }
    #right-panel DiffViewer {
        width: 1fr;
        height: 1fr;
        padding: 1 1;
    }
    #right-panel ReviewGates {
        width: 1fr;
        height: 1fr;
        padding: 1 1;
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
        Binding("o", "view_output", "Output", show=True),
        Binding("c", "view_chat", "Chat", show=True),
        Binding("d", "view_diff", "Diff", show=True),
        Binding("r", "view_review", "Review", show=True),
        Binding("1", "jump_task(1)", show=False),
        Binding("2", "jump_task(2)", show=False),
        Binding("3", "jump_task(3)", show=False),
        Binding("4", "jump_task(4)", show=False),
        Binding("5", "jump_task(5)", show=False),
        Binding("6", "jump_task(6)", show=False),
        Binding("7", "jump_task(7)", show=False),
        Binding("8", "jump_task(8)", show=False),
        Binding("9", "jump_task(9)", show=False),
    ]

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state
        self._active_view: str = "output"

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield DagOverlay()
        yield PhaseBanner()
        with Horizontal(id="split-pane"):
            with Vertical(id="left-panel"):
                yield TaskList()
                yield DecisionBadge()
            with Vertical(id="right-panel"):
                yield ViewLabel()
                yield AgentOutput()
                yield ChatThread()
                yield DiffViewer()
                yield ReviewGates()
        yield PipelineProgress()

    def on_mount(self) -> None:
        # Start with output view visible; hide the rest
        self._set_view("output")
        self._state.on_change(self._on_state_change)
        self._refresh_all()

    # ------------------------------------------------------------------
    # State change handling
    # ------------------------------------------------------------------

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
        phase_banner = self.query_one(PhaseBanner)
        decision_badge = self.query_one(DecisionBadge)

        ordered_tasks = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        task_list.update_tasks(ordered_tasks, state.selected_task_id, phase=state.phase)

        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            lines = state.agent_output.get(tid, [])
            agent_output.update_output(tid, task.get("title"), task.get("state"), lines)

            # Auto-switch to chat view when the selected task is awaiting input
            if task.get("state") == "awaiting_input":
                self._auto_switch_chat(tid, task)
        elif state.phase == "planning" and state.planner_output:
            agent_output.update_output("planner", "Planning", "planning", state.planner_output)
        elif state.phase == "contracts":
            agent_output.update_output(
                "contracts", "Generating Contracts", "contracts",
                ["⚙ Building API contracts for parallel task execution...",
                 "  This enables tasks to run in parallel instead of sequentially."],
            )
        else:
            agent_output.update_output(None, None, None, [])

        # Update diff and review for selected task
        diff_viewer = self.query_one(DiffViewer)
        review_gates = self.query_one(ReviewGates)
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            diff_text = task.get("diff", "")
            diff_viewer.update_diff(tid, task.get("title", ""), diff_text)
            gates = state.review_gates.get(tid, {})
            review_gates.update_gates(gates)
        else:
            review_gates.update_gates({})

        progress.update_progress(
            state.done_count, state.total_count,
            state.total_cost_usd, state.elapsed_seconds, state.phase,
        )
        dag.update_tasks(ordered_tasks)
        phase_banner.update_phase(state.phase)
        decision_badge.update_count(len(state.pending_questions))

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def _set_view(self, view: str) -> None:
        """Show one right-panel view widget and hide the others."""
        assert view in _VIEW_NAMES, f"Unknown view: {view!r}"
        self._active_view = view

        widget_map: dict[str, type[Widget]] = {
            "output": AgentOutput,
            "chat": ChatThread,
            "diff": DiffViewer,
            "review": ReviewGates,
        }

        for name, cls in widget_map.items():
            w = self.query_one(cls)
            if name == view:
                w.display = True
            else:
                w.display = False

        self.query_one(ViewLabel).update_active(view)

    def _auto_switch_chat(self, task_id: str, task: dict) -> None:
        """Switch to chat view and populate question when task needs input."""
        state = self._state
        question = state.pending_questions.get(task_id)
        if question:
            chat = self.query_one(ChatThread)
            chat.task_id = task_id
            work_lines = state.agent_output.get(task_id, [])
            history = state.question_history.get(task_id, [])
            chat.update_question(question, work_lines, history)

        # Only auto-switch if we're not already in chat view
        if self._active_view != "chat":
            self._set_view("chat")
            try:
                self.query_one("#chat-input").focus()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_task_list_selected(self, event: TaskList.Selected) -> None:
        self._state.selected_task_id = event.task_id
        self._refresh_all()

    def on_chat_thread_answer_submitted(self, event: ChatThread.AnswerSubmitted) -> None:
        """Forward answer to the TUI app for dispatch to the daemon."""
        # Bubble up — the main App should handle sending the answer back.
        # We post as a regular message so the App can catch it.
        self.post_message(event)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

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

    def action_view_output(self) -> None:
        self._set_view("output")

    def action_view_chat(self) -> None:
        self._set_view("chat")
        try:
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def action_view_diff(self) -> None:
        self._set_view("diff")

    def action_view_review(self) -> None:
        self._set_view("review")

    def action_jump_task(self, index: int) -> None:
        """Jump to task by 1-based position in the task list."""
        idx = int(index) - 1
        if 0 <= idx < len(self._state.task_order):
            self._state.selected_task_id = self._state.task_order[idx]
            self._refresh_all()
