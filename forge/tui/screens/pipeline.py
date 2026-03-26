"""Pipeline screen — 2-panel layout: task list on left, view switching on right."""

from __future__ import annotations

import asyncio
import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input

from forge.core.async_utils import safe_create_task
from forge.tui.state import TuiState
from forge.tui.theme import PHASE_DISPLAY as _PHASE_BANNER
from forge.tui.widgets.agent_output import AgentOutput
from forge.tui.widgets.chat_thread import ChatThread
from forge.tui.widgets.copy_overlay import CopyOverlay
from forge.tui.widgets.dag import DagOverlay
from forge.tui.widgets.diff_viewer import DiffViewer
from forge.tui.widgets.progress_bar import PipelineProgress
from forge.tui.widgets.search_overlay import SearchOverlay
from forge.tui.widgets.shortcut_bar import ShortcutBar
from forge.tui.widgets.suggestion_chips import SuggestionChips
from forge.tui.widgets.task_list import TaskList

logger = logging.getLogger("forge.tui.screens.pipeline")

_VIEW_NAMES = ("output", "chat", "diff")

_SIDEBAR_HIDDEN_PHASES = frozenset(
    {
        "idle",
        "planning",
        "planned",
        "contracts",
        "final_approval",
        "complete",
        "pr_creating",
        "pr_created",
        "cancelled",
    }
)


class PhaseBanner(Widget):
    """Full-width centered phase indicator displayed above the split pane."""

    DEFAULT_CSS = """
    PhaseBanner {
        width: 1fr;
        height: 5;
        content-align: center middle;
        text-align: center;
        background: #0d1117;
        border-bottom: tall #21262d;
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
        # Extract icon prefix and text
        icon, _, text = label.partition(" ")
        if not text:
            text, icon = icon, ""
        # During planning, the unified planner streams output via planner:output
        # No per-stage label needed — the banner just shows "PLANNING"
        # Wide-space: double-space within words, triple-space between words
        words = text.upper().split()
        spaced_words = ["  ".join(w) for w in words]
        spaced = "   ".join(spaced_words)
        icon_prefix = f"{icon}  " if icon else ""
        banner = f"[bold {colour}]{icon_prefix}{spaced}[/]"
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


class IntegrationBadge(Widget):
    """Shows integration health status below DecisionBadge."""

    DEFAULT_CSS = """
    IntegrationBadge {
        height: 1;
        padding: 0 1;
        background: #161b22;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._degraded = False
        self._checking = False

    def update(self, *, degraded: bool = False, checking: bool = False) -> None:
        self._degraded = degraded
        self._checking = checking
        self.refresh()

    def render(self) -> str:
        if self._checking:
            return "[#d2a8ff]⧗ Integration check running...[/]"
        if self._degraded:
            return "[bold #d29922]⚠ Pipeline Degraded[/]"
        return ""


class PipelineScreen(Screen):
    """Main pipeline execution screen with full-width phase banner + dynamic layout.

    Phase banner (full width, centered):
      - PhaseBanner — 5-line wide-spaced label

    Left panel (hidden during planning, shown during execution):
      - TaskList
      - DecisionBadge
      - IntegrationBadge

    Right panel (fills remaining or full width):
      - AgentOutput   (unified log stream — agent + review + gates)
      - ChatThread    (auto-shown for questions)
      - DiffViewer    (toggled with 'd')
    """

    DEFAULT_CSS = """
    PipelineScreen {
        layout: vertical;
    }
    PipelineScreen > PhaseBanner {
        width: 100%;
        height: 5;
        content-align: center middle;
        text-align: center;
        background: #0d1117;
        border-bottom: tall #21262d;
    }
    #split-pane {
        height: 1fr;
    }
    #split-pane.full-width #left-panel {
        display: none;
    }
    #split-pane.full-width #right-panel {
        width: 100%;
    }
    #left-panel {
        width: 38;
        min-width: 32;
        max-width: 48;
        border-right: tall #21262d;
        background: #0d1117;
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
        padding: 1 2;
    }
    #right-panel ChatThread {
        width: 1fr;
        height: 1fr;
    }
    #right-panel DiffViewer {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    PipelineProgress {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        border-top: tall #21262d;
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
        Binding("i", "interject", "Interject", show=True),
        Binding("r", "open_review", "Review", show=True),
        Binding("R", "retry_task", "Retry", show=False),
        Binding("s", "skip_task", "Skip", show=False),
        Binding("C", "copy_all", "Copy All", show=False),
        Binding("slash", "toggle_search", "Search", show=False),
        Binding("n", "search_next", "Next match", show=False),
        Binding("N", "search_prev", "Prev match", show=False),
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
        self._review_notified_tasks: set[str] = set()  # tasks already notified for review
        self._agent_output_len: dict[str, int] = {}  # tid -> last seen len of agent_output[tid]
        self._review_output_len: dict[str, int] = {}  # tid -> last seen len of review_output[tid]

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
                yield IntegrationBadge()
            with Vertical(id="right-panel"):
                yield AgentOutput()
                yield ChatThread()
                yield DiffViewer()
        yield SearchOverlay()
        yield ShortcutBar(
            [
                ("d", "View Diff"),
                ("↑↓", "Select Task"),
                ("q", "Quit (tasks saved)"),
            ]
        )
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
            banner.set_read_only_banner(
                f"📖 Viewing pipeline from {date_str} — press Esc to return"
            )

    def on_unmount(self) -> None:
        self._state.remove_change_callback(self._on_state_change)

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
        if field == "integration":
            self._refresh_integration_badge()
            return
        if field == "task_diffs":
            # New diff arrived from daemon — update diff viewer if showing
            if self._active_view == "diff":
                tid = self._state.selected_task_id
                if tid and tid in self._state.task_diffs:
                    self._diff_cache[tid] = self._state.task_diffs[tid]
                    try:
                        task = self._state.tasks.get(tid, {})
                        self.query_one(DiffViewer).update_diff(
                            tid,
                            task.get("title", ""),
                            self._state.task_diffs[tid],
                        )
                    except Exception:
                        pass
            return
        if field in (
            "tasks",
            "cost",
            "phase",
            "elapsed",
            "planner_output",
            "contracts_output",
            "planning",
        ):
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
            # Update shortcut bar hints based on pipeline phase
            if field == "phase":
                self._update_shortcut_bar(self._state.phase)

            # Notify user when task enters review (press 'r' to open)
            if field == "tasks" and not self._read_only:
                self._check_review_notification()

        if field == "error":
            error = self._state.error
            if error:
                from forge.tui.app import _escape_markup

                self.app.notify(
                    f"Pipeline error: {_escape_markup(error)}", severity="error", timeout=10
                )
        if field == "auto_decided":
            info = self._state.last_auto_decided
            if info:
                from forge.tui.app import _escape_markup

                self.app.notify(
                    f"Question auto-answered for task {_escape_markup(info['task_id'])} "
                    f"(reason: {info['reason']}). Agent resumed with best judgment.",
                    severity="warning",
                    timeout=15,
                )

    def _update_shortcut_bar(self, phase: str) -> None:
        """Update shortcut bar based on current pipeline phase."""
        try:
            bar = self.query_one(ShortcutBar)
        except Exception:
            return
        if phase == "awaiting_input":
            bar.shortcuts = [
                ("Enter", "Answer Question"),
                ("d", "View Diff"),
                ("↑↓", "Select Task"),
                ("q", "Quit (tasks saved)"),
            ]
        elif phase in ("partial_success", "retrying"):
            bar.shortcuts = [
                ("Enter", "View Results"),
                ("q", "Quit (tasks saved)"),
            ]
        else:
            bar.shortcuts = [
                ("d", "View Diff"),
                ("↑↓", "Select Task"),
                ("q", "Quit (tasks saved)"),
            ]

    def _check_review_notification(self) -> None:
        """Notify user when a task enters review — they can press 'r' to open ReviewScreen."""
        state = self._state
        for tid in state.task_order:
            if tid not in state.tasks:
                continue
            task = state.tasks[tid]
            if task.get("state") == "in_review" and tid not in self._review_notified_tasks:
                self._review_notified_tasks.add(tid)
                title = task.get("title", tid)
                state.selected_task_id = tid
                self.app.notify(
                    f"Task {title} ready for review — press [bold]r[/bold] to open",
                    timeout=8,
                )
                return

    def _handle_agent_output_fast(self) -> None:
        """Fast path for agent_output: append only NEW lines to unified log."""
        state = self._state
        tid = state.selected_task_id
        if not tid:
            return
        lines = state.agent_output.get(tid, [])
        if not lines:
            return
        prev_len = self._agent_output_len.get(tid, 0)
        cur_len = len(lines)
        if cur_len <= prev_len:
            return  # No new lines for this task — event was for a different task
        self._agent_output_len[tid] = cur_len
        agent_output = self.query_one(AgentOutput)
        if tid not in self._agent_streaming_tasks:
            self._agent_streaming_tasks.add(tid)
            agent_output.set_streaming(True)
        # Append only the new lines
        for line in lines[prev_len:]:
            agent_output.append_unified("agent", line)

    def _handle_review_output_fast(self) -> None:
        """Fast path for review_output: append only NEW lines to unified log."""
        state = self._state
        tid = state.selected_task_id
        if not tid:
            return
        lines = state.review_output.get(tid, [])
        if not lines:
            return
        prev_len = self._review_output_len.get(tid, 0)
        cur_len = len(lines)
        if cur_len <= prev_len:
            return  # No new lines for this task
        self._review_output_len[tid] = cur_len
        agent_output = self.query_one(AgentOutput)
        if tid not in self._review_streaming_tasks:
            self._review_streaming_tasks.add(tid)
            agent_output.set_streaming(True)
        for line in lines[prev_len:]:
            agent_output.append_unified("review", line)

    def _update_streaming_lifecycle(self) -> None:
        """Stop streaming indicators for tasks that are done/error."""
        state = self._state
        tid = state.selected_task_id
        if not tid:
            return
        if tid not in state.streaming_task_ids:
            needs_reconcile = False
            if tid in self._agent_streaming_tasks:
                self._agent_streaming_tasks.discard(tid)
                needs_reconcile = True
            if tid in self._review_streaming_tasks:
                self._review_streaming_tasks.discard(tid)
                needs_reconcile = True
            if needs_reconcile:
                try:
                    ao = self.query_one(AgentOutput)
                    ao.set_streaming(False)
                    unified = state.unified_log.get(tid, [])
                    task = state.tasks.get(tid, {})
                    ao.update_unified(tid, task.get("title"), task.get("state"), unified)
                    # Reset tracking lengths so fast-path doesn't re-append
                    # lines that are already in the reconciled unified log
                    self._agent_output_len[tid] = len(state.agent_output.get(tid, []))
                    self._review_output_len[tid] = len(state.review_output.get(tid, []))
                except Exception:
                    pass

    def _refresh_integration_badge(self) -> None:
        """Update the integration badge based on current state."""
        try:
            badge = self.query_one(IntegrationBadge)
        except Exception:
            return
        state = self._state
        # Check if any integration check is currently running
        checking = any(c.get("status") == "running" for c in state.integration_checks.values())
        if not checking and state.integration_baseline:
            checking = state.integration_baseline.get("status") == "running"
        if not checking and state.integration_final_gate:
            checking = state.integration_final_gate.get("status") == "running"
        badge.update(degraded=state.integration_degraded, checking=checking)

    def _refresh_all(self) -> None:
        state = self._state
        task_list = self.query_one(TaskList)
        agent_output = self.query_one(AgentOutput)
        progress = self.query_one(PipelineProgress)
        dag = self.query_one(DagOverlay)
        phase_banner = self.query_one(PhaseBanner)
        decision_badge = self.query_one(DecisionBadge)
        self._refresh_integration_badge()

        ordered_tasks = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        task_list.update_tasks(
            ordered_tasks, state.selected_task_id, phase=state.phase, multi_repo=state.is_multi_repo
        )

        tid = state.selected_task_id

        # Show error detail view for errored tasks
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            unified = state.unified_log.get(tid, [])
            if task.get("state") == "error":
                agent_output.render_error_detail(tid, task, state.agent_output.get(tid, []))
            elif tid in self._agent_streaming_tasks or tid in self._review_streaming_tasks:
                # Streaming active — sync data WITHOUT toggling streaming off/on
                # (update_unified calls set_streaming(False) which causes double render)
                agent_output._task_id = tid
                agent_output._title = task.get("title")
                agent_output._state = task.get("state")
                agent_output._unified_entries = list(unified)
                agent_output._lines = []
                # Reset incremental state so next append starts fresh
                agent_output._rendered_parts = []
                agent_output._rendered_section = None
                agent_output._rendered_review_count = 0
                try:
                    from textual.widgets import Static
                    from forge.tui.widgets.agent_output import format_header, format_unified_output
                    agent_output.query_one("#agent-header", Static).update(
                        format_header(tid, task.get("title"), task.get("state"))
                    )
                    agent_output.query_one("#agent-content", Static).update(
                        format_unified_output(unified, agent_output._spinner_frame, streaming=True, typing_frame=agent_output._typing_frame)
                    )
                except Exception:
                    pass
                # Ensure streaming stays on without toggling
                if not agent_output._streaming:
                    agent_output.set_streaming(True)
            else:
                agent_output._error_mode = False  # Exit error mode without double-render
                agent_output.update_unified(tid, task.get("title"), task.get("state"), unified)

                # Auto-switch to chat view when the selected task is awaiting input
                if task.get("state") == "awaiting_input":
                    self._auto_switch_chat(tid, task)
        elif state.phase == "planning" and state.planner_output:
            agent_output.clear_error_detail()
            agent_output.update_output("planner", "Planning", "planning", state.planner_output)
            # Auto-switch to chat view when a planning question is pending
            planning_q = state.pending_questions.get("__planning__")
            if planning_q:
                self._auto_switch_planning_chat(planning_q)
        elif state.phase == "contracts":
            agent_output.clear_error_detail()
            if state.contracts_output:
                agent_output.update_output(
                    "contracts",
                    "⚙ Contracts",
                    "contracts",
                    state.contracts_output,
                )
            else:
                agent_output.update_output(
                    "contracts",
                    "Generating Contracts",
                    "contracts",
                    [
                        "⚙ Building API contracts...",
                        "  This enables tasks to run in parallel instead of sequentially.",
                    ],
                )
        else:
            agent_output.clear_error_detail()
            agent_output.update_output(None, None, None, [])

        # Update diff for selected task (ReviewGates removed — data flows through unified log)
        diff_viewer = self.query_one(DiffViewer)
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            if self._active_view == "diff":
                if tid in self._diff_cache:
                    diff_viewer.update_diff(tid, task.get("title", ""), self._diff_cache[tid])
                else:
                    diff_viewer.update_diff(tid, task.get("title", ""), "Loading diff...")
                    safe_create_task(
                        self._refresh_diff_async(tid), logger=logger, name="refresh-diff"
                    )
            else:
                diff_text = self._diff_cache.get(tid, "")
                diff_viewer.update_diff(tid, task.get("title", ""), diff_text)

        progress.update_progress(
            state.done_count,
            state.total_count,
            state.total_cost_usd,
            state.elapsed_seconds,
            state.phase,
        )
        dag.update_tasks(ordered_tasks)
        phase_banner.update_phase(state.phase)

        split_pane = self.query_one("#split-pane")
        if state.phase in _SIDEBAR_HIDDEN_PHASES:
            split_pane.add_class("full-width")
        else:
            split_pane.remove_class("full-width")

        decision_badge.update_count(len(state.pending_questions))

    # ------------------------------------------------------------------
    # On-demand diff loading
    # ------------------------------------------------------------------

    async def _resolve_branch(self) -> str:
        """Resolve the pipeline branch — from state or git fallback."""
        branch = self._state.pipeline_branch or ""
        if branch:
            return branch
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                name = stdout.decode().strip()
                if name and name not in ("main", "master", "HEAD"):
                    self._state.pipeline_branch = name
                    return name
        except Exception:
            pass
        return ""

    async def _load_task_diff(self, tid: str) -> str:
        """Get diff for a task, preferring daemon-computed diff from worktree.

        Priority:
        1. state.task_diffs[tid] — computed by daemon in the worktree (always accurate)
        2. self._diff_cache[tid] — previously loaded
        3. git diff base...pipeline_branch — fallback for merged tasks
        """
        # Prefer daemon-computed diff (available as soon as task enters review)
        daemon_diff = self._state.task_diffs.get(tid, "")
        if daemon_diff:
            self._diff_cache[tid] = daemon_diff
            return daemon_diff
        if tid in self._diff_cache:
            return self._diff_cache[tid]
        # Fallback: git diff on the pipeline branch (works for already-merged tasks)
        branch = await self._resolve_branch()
        if not branch:
            return "No pipeline branch available yet."
        base = getattr(self._state, "base_branch", "main") or "main"
        cmd = ["git", "diff", f"{base}...{branch}"]
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
        # Guard: screen may have been destroyed while awaiting diff
        if not self.is_running:
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
        }

        for name, cls in widget_map.items():
            w = self.query_one(cls)
            w.display = name == view

    def _auto_switch_planning_chat(self, question: dict) -> None:
        """Switch to chat view for a planning question from the Architect."""
        state = self._state
        chat = self.query_one(ChatThread)
        chat.task_id = "__planning__"
        work_lines = state.planner_output
        history = state.question_history.get("__planning__", [])
        chat.update_question(question, work_lines, history)

        if self._active_view != "chat":
            self._set_view("chat")
        # Always focus the input — use a short delay to ensure layout is settled
        self.set_timer(0.15, self._focus_chat_input)

    def _focus_chat_input(self) -> None:
        """Focus the chat input after layout settles."""
        try:
            self.query_one("#chat-input").focus()
        except Exception:
            pass

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
        # Always focus input — delay to let layout settle
        self.set_timer(0.15, self._focus_chat_input)

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

    def on_chat_thread_interjection_submitted(
        self, event: ChatThread.InterjectionSubmitted
    ) -> None:
        """Forward interjection to the TUI app for DB persistence."""
        self.post_message(event)

    def on_copy_overlay_copy_complete(self, event: CopyOverlay.CopyComplete) -> None:
        """Remove overlay after copy."""
        self._dismiss_copy_overlay()
        if event.success:
            self.app.notify("Copied to clipboard!", timeout=3)
        else:
            self.app.notify(
                "Clipboard unavailable — install xclip or xsel",
                severity="warning",
                timeout=5,
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

    def action_copy_mode(self) -> None:
        """Enter copy mode — mount CopyOverlay on AgentOutput."""
        # Guard: dismiss existing overlay first
        if self._copy_overlay is not None:
            self._dismiss_copy_overlay()
            return  # Toggle off
        lines = self._get_copyable_lines()
        if not lines:
            self.app.notify("No output to copy", timeout=3)
            return
        self._copy_overlay = CopyOverlay(lines=lines)
        try:
            self.mount(self._copy_overlay)
            self._copy_overlay.focus()
        except Exception:
            logger.debug("Failed to mount CopyOverlay", exc_info=True)

    def _get_copyable_lines(self) -> list[str]:
        """Get lines from agent output, with fallback to rendered text."""
        agent_output = self.query_one(AgentOutput)
        if agent_output._unified_entries:
            return [line for _, line in agent_output._unified_entries]
        if agent_output._lines:
            return list(agent_output._lines)
        # Fallback: read rendered text from the Static widget
        try:
            from textual.widgets import Static

            content = agent_output.query_one("#agent-content", Static)
            text = content.renderable
            if isinstance(text, str) and text.strip():
                return text.split("\n")
        except Exception:
            pass
        return []

    def action_copy_all(self) -> None:
        """Instant copy of all visible output to clipboard."""
        from forge.tui.widgets.copy_overlay import copy_to_clipboard

        lines = self._get_copyable_lines()
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
                severity="warning",
                timeout=5,
            )

    def action_open_review(self) -> None:
        """Open ReviewScreen for the selected task."""
        if self._read_only:
            return
        tid = self._state.selected_task_id
        if not tid or tid not in self._state.tasks:
            self.app.notify("No task selected", timeout=3)
            return
        try:
            from forge.tui.screens.review import ReviewScreen

            self.app.push_screen(ReviewScreen(self._state))
        except Exception:
            logger.debug("Failed to push ReviewScreen", exc_info=True)

    def action_interject(self) -> None:
        """Open chat thread in interjection mode for the selected task."""
        if self._read_only:
            return
        tid = self._state.selected_task_id
        if not tid or tid not in self._state.tasks:
            self.notify("No task selected", severity="warning")
            return
        task = self._state.tasks[tid]
        state = task.get("state", "")
        if state not in ("in_progress", "awaiting_input"):
            self.notify(f"Cannot interject — task is {state}", severity="warning")
            return
        # Replace the existing ChatThread with one in interjection mode
        chat = self.query_one(ChatThread)
        chat.task_id = tid
        chat._mode = "interjection"
        # Update the input placeholder and hide suggestion chips
        try:
            inp = chat.query_one("#chat-input", Input)
            inp.placeholder = "Type a message to the agent..."
            inp.value = ""
        except Exception:
            pass
        try:
            chips = chat.query_one(SuggestionChips)
            chips.display = False
        except Exception:
            pass
        self._set_view("chat")
        try:
            self.query_one("#chat-input").focus()
        except Exception:
            pass

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

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def action_toggle_search(self) -> None:
        """Show the search overlay."""
        try:
            self.query_one(SearchOverlay).show()
        except Exception:
            logger.debug("Failed to show SearchOverlay", exc_info=True)

    def action_search_next(self) -> None:
        """Navigate to next search match."""
        try:
            self.query_one(SearchOverlay).navigate(+1)
        except Exception:
            pass

    def action_search_prev(self) -> None:
        """Navigate to previous search match."""
        try:
            self.query_one(SearchOverlay).navigate(-1)
        except Exception:
            pass

    def on_search_overlay_search_changed(self, event: SearchOverlay.SearchChanged) -> None:
        """Apply search highlights to the active view widget."""
        count = 0
        try:
            if self._active_view == "output":
                count = self.query_one(AgentOutput).set_search_highlights(event.pattern)
            elif self._active_view == "diff":
                count = self.query_one(DiffViewer).set_search_highlights(event.pattern)
        except Exception:
            logger.debug("Failed to apply search highlights", exc_info=True)
        try:
            self.query_one(SearchOverlay).update_match_count(count)
        except Exception:
            pass

    def on_search_overlay_search_dismissed(self, event: SearchOverlay.SearchDismissed) -> None:
        """Clear highlights when search is fully dismissed."""
        try:
            self.query_one(AgentOutput).set_search_highlights(None)
        except Exception:
            pass
        try:
            self.query_one(DiffViewer).set_search_highlights(None)
        except Exception:
            pass
