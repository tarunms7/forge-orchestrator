"""Pipeline screen — 2-panel layout: task list on left, view switching on right."""

from __future__ import annotations

import asyncio
import logging

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
from forge.tui.widgets.copy_overlay import CopyOverlay

logger = logging.getLogger("forge.tui.screens.pipeline")

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
    "cancelled":      ("✘ Cancelled",       "#8b949e"),
    "paused":         ("⏸ Paused",          "#d29922"),
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
        self._read_only_banner: str | None = None

    def update_phase(self, phase: str) -> None:
        self._phase = phase
        self.refresh()

    def set_read_only_banner(self, text: str | None) -> None:
        self._read_only_banner = text
        self.refresh()

    def render(self) -> str:
        label, colour = _PHASE_BANNER.get(self._phase, ("Unknown", "#8b949e"))
        banner = f"[bold {colour}]{label}[/]"
        if self._read_only_banner:
            banner += f"\n[dim]{self._read_only_banner}[/]"
        return banner


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
            + _item("chat", "t", "Chat")
            + "  "
            + _item("diff", "d", "Diff")
            + "  "
            + _item("review", "v", "Review")
            + "  "
            + _item("copy", "c", "Copy")
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
        Binding("c", "copy_mode", "Copy", show=True),
        Binding("t", "view_chat", "Chat", show=True),
        Binding("d", "view_diff", "Diff", show=True),
        Binding("r", "retry_task", "Retry", show=False),
        Binding("v", "view_review", "Review", show=True),
        Binding("s", "skip_task", "Skip", show=False),
        Binding("C", "copy_all", "Copy All", show=False),
        Binding("escape", "pop_screen", "Back", show=False),
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

    def __init__(self, state: TuiState, *, read_only: bool = False) -> None:
        super().__init__()
        self._state = state
        self._read_only = read_only
        self._active_view: str = "output"
        self._agent_streaming_tasks: set[str] = set()  # tasks with active agent streaming
        self._review_streaming_tasks: set[str] = set()  # tasks with active review streaming
        self._diff_cache: dict[str, str] = {}  # task_id -> diff text
        self._copy_overlay: CopyOverlay | None = None

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

        # Set up read-only mode banner
        if self._read_only:
            created = getattr(self._state, "_replay_date", None) or ""
            date_str = str(created)[:10] if created else "unknown date"
            banner = self.query_one(PhaseBanner)
            banner.set_read_only_banner(f"📖 Viewing pipeline from {date_str} — press Esc to return")

    # ------------------------------------------------------------------
    # State change handling
    # ------------------------------------------------------------------

    def _on_state_change(self, field: str) -> None:
        # Fast path: streaming fields only update the relevant widget
        if field == "agent_output":
            self._handle_agent_output_fast()
            return
        if field == "review_output":
            self._handle_review_output_fast()
            return
        if field in ("tasks", "cost", "phase", "elapsed", "planner_output",
                     "contracts_output"):
            # Invalidate diff cache for tasks whose state changed (new merge → new diff)
            if field == "tasks":
                for cache_tid in list(self._diff_cache):
                    if cache_tid in self._state.tasks:
                        task_state = self._state.tasks[cache_tid].get("state")
                        if task_state in ("in_progress", "in_review", "merging"):
                            del self._diff_cache[cache_tid]
            # On task state changes, also update streaming lifecycle
            self._update_streaming_lifecycle()
            self._refresh_all()

            # Feature 7: auto-transition to review screen
            if field == "tasks" and not self._read_only:
                self._check_review_auto_transition()

        if field == "error":
            error = self._state.error
            if error:
                self.app.notify(f"Pipeline error: {error}", severity="error", timeout=10)

    def _check_review_auto_transition(self) -> None:
        """Auto-select task entering review and push ReviewScreen."""
        state = self._state
        for tid in state.task_order:
            if tid not in state.tasks:
                continue
            task = state.tasks[tid]
            if task.get("state") == "in_review":
                # Guard: check if user is typing in chat
                try:
                    chat_input = self.query_one("#chat-input")
                    if chat_input.has_focus:
                        title = task.get("title", tid)
                        self.app.notify(
                            f"Task {title} entered review — press v to view",
                            timeout=5,
                        )
                        return
                except Exception:
                    pass

                # Auto-select and push review
                state.selected_task_id = tid
                self._refresh_all()
                try:
                    from forge.tui.screens.review import ReviewScreen
                    self.app.push_screen(ReviewScreen(state))
                except Exception:
                    logger.debug("Failed to push ReviewScreen", exc_info=True)
                return

    def _handle_agent_output_fast(self) -> None:
        """Fast path for agent_output: only update AgentOutput widget."""
        state = self._state
        tid = state.selected_task_id
        if not tid:
            return
        lines = state.agent_output.get(tid, [])
        if not lines:
            return
        agent_output = self.query_one(AgentOutput)
        # Start streaming if not already active for this task
        if tid not in self._agent_streaming_tasks:
            self._agent_streaming_tasks.add(tid)
            agent_output.set_streaming(True)
        # Append only the latest line
        agent_output.append_line(lines[-1])

    def _handle_review_output_fast(self) -> None:
        """Fast path for review_output: only update ReviewGates widget."""
        state = self._state
        tid = state.selected_task_id
        if not tid:
            return
        lines = state.review_output.get(tid, [])
        review_gates = self.query_one(ReviewGates)
        review_gates.update_streaming_output(lines)
        # Start streaming indicator if not already active
        if tid not in self._review_streaming_tasks and lines:
            self._review_streaming_tasks.add(tid)
            review_gates.set_streaming(True)

    def _update_streaming_lifecycle(self) -> None:
        """Stop streaming indicators for tasks that are done/error."""
        state = self._state
        tid = state.selected_task_id
        if not tid:
            return
        # Check if the selected task is no longer streaming
        if tid not in state.streaming_task_ids:
            if tid in self._agent_streaming_tasks:
                self._agent_streaming_tasks.discard(tid)
                try:
                    ao = self.query_one(AgentOutput)
                    ao.set_streaming(False)
                    # Final sync: full refresh to pick up lines accumulated during guard
                    lines = state.agent_output.get(tid, [])
                    task = state.tasks.get(tid, {})
                    ao.update_output(tid, task.get("title"), task.get("state"), lines)
                except Exception:
                    pass
            if tid in self._review_streaming_tasks:
                self._review_streaming_tasks.discard(tid)
                try:
                    review_gates = self.query_one(ReviewGates)
                    review_gates.set_streaming(False)
                except Exception:
                    pass

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

        # Show error detail view for errored tasks
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            lines = state.agent_output.get(tid, [])
            if task.get("state") == "error":
                agent_output.render_error_detail(tid, task, lines)
            elif tid in self._agent_streaming_tasks:
                # Streaming active — only update header, not content/scroll
                agent_output.update_header(tid, task.get("title"), task.get("state"))
            else:
                agent_output.clear_error_detail()
                agent_output.update_output(tid, task.get("title"), task.get("state"), lines)

                # Auto-switch to chat view when the selected task is awaiting input
                if task.get("state") == "awaiting_input":
                    self._auto_switch_chat(tid, task)
        elif state.phase == "planning" and state.planner_output:
            agent_output.clear_error_detail()
            agent_output.update_output("planner", "Planning", "planning", state.planner_output)
        elif state.phase == "contracts":
            agent_output.clear_error_detail()
            # Feature 2: stream contracts output like planner output
            if state.contracts_output:
                agent_output.update_output(
                    "contracts", "⚙ Contracts", "contracts", state.contracts_output,
                )
            else:
                agent_output.update_output(
                    "contracts", "Generating Contracts", "contracts",
                    ["⚙ Building API contracts...",
                     "  This enables tasks to run in parallel instead of sequentially."],
                )
        else:
            agent_output.clear_error_detail()
            agent_output.update_output(None, None, None, [])

        # Update diff and review for selected task
        diff_viewer = self.query_one(DiffViewer)
        review_gates = self.query_one(ReviewGates)
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            # On-demand diff: use cache or trigger async load when diff view is active
            if self._active_view == "diff":
                if tid in self._diff_cache:
                    diff_viewer.update_diff(tid, task.get("title", ""), self._diff_cache[tid])
                else:
                    diff_viewer.update_diff(tid, task.get("title", ""), "Loading diff...")
                    asyncio.create_task(self._refresh_diff_async(tid))
            else:
                # Not viewing diff — show cached if available, empty otherwise
                diff_text = self._diff_cache.get(tid, "")
                diff_viewer.update_diff(tid, task.get("title", ""), diff_text)
            gates = state.review_gates.get(tid, {})
            review_gates.update_gates(gates)
            # Show streaming review output if available
            review_lines = state.review_output.get(tid, [])
            review_gates.update_streaming_output(review_lines)
        else:
            review_gates.update_gates({})
            review_gates.update_streaming_output([])

        progress.update_progress(
            state.done_count, state.total_count,
            state.total_cost_usd, state.elapsed_seconds, state.phase,
        )
        dag.update_tasks(ordered_tasks)
        phase_banner.update_phase(state.phase)
        decision_badge.update_count(len(state.pending_questions))

    # ------------------------------------------------------------------
    # On-demand diff loading
    # ------------------------------------------------------------------

    async def _load_task_diff(self, tid: str) -> str:
        """Generate diff for a task from the pipeline branch."""
        if tid in self._diff_cache:
            return self._diff_cache[tid]
        branch = self._state.pipeline_branch or ""
        if not branch:
            return "No pipeline branch available yet."
        cmd = ["git", "diff", f"main...{branch}"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                diff = stdout.decode(errors="replace")
            else:
                diff = f"git diff failed: {stderr.decode(errors='replace')}"
        except Exception as e:
            diff = f"Error running git diff: {e}"
        self._diff_cache[tid] = diff
        return diff

    async def _refresh_diff_async(self, tid: str) -> None:
        """Fetch diff async and update the viewer."""
        diff = await self._load_task_diff(tid)
        # Guard: only update if user is still viewing this task
        if self._state.selected_task_id != tid:
            return
        try:
            diff_viewer = self.query_one(DiffViewer)
            task = self._state.tasks.get(tid, {})
            diff_viewer.update_diff(tid, task.get("title", ""), diff)
        except Exception:
            pass

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
    # Helpers
    # ------------------------------------------------------------------

    def _get_selected_task(self) -> dict | None:
        """Return the currently selected task dict, or None."""
        tid = self._state.selected_task_id
        if tid and tid in self._state.tasks:
            return self._state.tasks[tid]
        return None

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

    def on_copy_overlay_copy_complete(self, event: CopyOverlay.CopyComplete) -> None:
        """Remove overlay after copy."""
        self._dismiss_copy_overlay()
        if event.success:
            self.app.notify("Copied to clipboard!", timeout=3)
        else:
            self.app.notify(
                "Clipboard unavailable — install xclip or xsel",
                severity="warning", timeout=5,
            )

    def on_copy_overlay_cancelled(self, event: CopyOverlay.Cancelled) -> None:
        """Remove overlay on Esc."""
        self._dismiss_copy_overlay()

    def _dismiss_copy_overlay(self) -> None:
        """Remove the CopyOverlay widget if mounted."""
        if self._copy_overlay is not None:
            try:
                self._copy_overlay.remove()
            except Exception:
                pass
            self._copy_overlay = None

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

    def action_copy_mode(self) -> None:
        """Enter copy mode — mount CopyOverlay on AgentOutput."""
        agent_output = self.query_one(AgentOutput)
        lines = list(agent_output._lines)
        self._copy_overlay = CopyOverlay(lines=lines)
        try:
            self.mount(self._copy_overlay)
            self._copy_overlay.focus()
        except Exception:
            logger.debug("Failed to mount CopyOverlay", exc_info=True)

    def action_copy_all(self) -> None:
        """Instant copy of all visible output to clipboard."""
        from forge.tui.widgets.copy_overlay import copy_to_clipboard
        agent_output = self.query_one(AgentOutput)
        lines = list(agent_output._lines)
        if not lines:
            self.app.notify("No output to copy", timeout=3)
            return
        text = "\n".join(lines)
        success = copy_to_clipboard(text)
        if not success:
            try:
                self.app.copy_to_clipboard(text)
                success = True
            except Exception:
                pass
        if success:
            self.app.notify("Copied all output to clipboard!", timeout=3)
        else:
            self.app.notify(
                "Clipboard unavailable — install xclip or xsel",
                severity="warning", timeout=5,
            )

    def action_retry_task(self) -> None:
        """Retry the selected task (only active for error-state tasks)."""
        if self._read_only:
            return
        task = self._get_selected_task()
        if not task or task.get("state") != "error":
            return
        tid = task["id"]
        try:
            self.app._bus.emit("task:retry", {"task_id": tid})
        except Exception:
            logger.debug("Failed to emit task:retry", exc_info=True)

    def action_skip_task(self) -> None:
        """Skip the selected errored task (only active for error-state tasks)."""
        if self._read_only:
            return
        task = self._get_selected_task()
        if not task or task.get("state") != "error":
            return
        tid = task["id"]
        try:
            self.app._bus.emit("task:skip", {"task_id": tid})
        except Exception:
            logger.debug("Failed to emit task:skip", exc_info=True)

    def action_pop_screen(self) -> None:
        """Esc — only active in read-only mode."""
        if self._read_only:
            self.app.pop_screen()

    def action_jump_task(self, index: int) -> None:
        """Jump to task by 1-based position in the task list."""
        idx = int(index) - 1
        if 0 <= idx < len(self._state.task_order):
            self._state.selected_task_id = self._state.task_order[idx]
            self._refresh_all()
