"""Pipeline screen — 2-panel layout: task list on left, view switching on right."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Static

from forge.core.async_utils import safe_create_task
from forge.tui.state import TuiState
from forge.tui.theme import (
    PHASE_DISPLAY as _PHASE_BANNER,
)
from forge.tui.theme import (
    TEXT_MUTED,
)
from forge.tui.widgets.agent_output import AgentOutput
from forge.tui.widgets.chat_thread import ChatThread, format_review_progress
from forge.tui.widgets.copy_overlay import CopyOverlay
from forge.tui.widgets.dag import DagOverlay
from forge.tui.widgets.diff_viewer import DiffViewer
from forge.tui.widgets.evidence_panel import EvidencePanel
from forge.tui.widgets.progress_bar import PipelineProgress
from forge.tui.widgets.queue_status import QueueStatus
from forge.tui.widgets.search_overlay import SearchOverlay
from forge.tui.widgets.shortcut_bar import ShortcutBar
from forge.tui.widgets.task_list import TaskList

logger = logging.getLogger("forge.tui.screens.pipeline")

_VIEW_NAMES = ("output", "chat", "diff")

_SIDEBAR_HIDDEN_PHASES = frozenset(
    {
        "idle",
        "planning",
        "planned",
        "final_approval",
        "complete",
        "pr_creating",
        "pr_created",
        "cancelled",
    }
)


_SCRAMBLE_CHARS = "░▒▓█▀▄▌▐"


class PhaseBanner(Widget):
    """Full-width centered phase indicator displayed above the split pane."""

    DEFAULT_CSS = """
    PhaseBanner {
        width: 1fr;
        height: 3;
        content-align: center middle;
        text-align: center;
        background: #11161d;
        border-bottom: tall #263041;
    }
    """

    class CountdownComplete(Message):
        """Emitted when the launch countdown reaches zero."""

    def __init__(self) -> None:
        super().__init__()
        self._phase = "idle"
        self._read_only_banner: str | None = None
        self._planner_detail: str = ""
        # Scramble-resolve animation state
        self._target_text: str = ""
        self._target_colour: str = "#8b949e"
        self._target_icon: str = ""
        self._resolved_count: int = 0
        self._scramble_timer = None
        self._animating: bool = False
        # Countdown state
        self._countdown_value: int = 0
        self._countdown_timer = None

    def update_phase(self, phase: str) -> None:
        old_phase = self._phase
        self._phase = phase
        if old_phase == phase:
            self.refresh()
            return
        # Clear planner detail when phase changes away from planning
        if old_phase == "planning" and phase != "planning":
            self._planner_detail = ""
        # Start scramble-resolve animation
        label, colour = _PHASE_BANNER.get(phase, ("Unknown", "#8b949e"))
        icon, _, text = label.partition(" ")
        if not text:
            text, icon = icon, ""
        # Build the spaced text (same as render)
        words = text.upper().split()
        spaced_words = ["  ".join(w) for w in words]
        spaced = "   ".join(spaced_words)

        self._target_text = spaced
        self._target_colour = colour
        self._target_icon = f"{icon}  " if icon else ""
        self._resolved_count = 0
        self._animating = True

        if self._scramble_timer is not None:
            self._scramble_timer.stop()
            self._scramble_timer = None
        if self.is_mounted:
            self._scramble_timer = self.set_interval(0.045, self._tick_scramble)
        else:
            self._animating = False
        self.refresh()

    def _tick_scramble(self) -> None:
        """Resolve one character from left to right."""
        self._resolved_count += 1
        if self._resolved_count >= len(self._target_text):
            self._animating = False
            if self._scramble_timer is not None:
                self._scramble_timer.stop()
                self._scramble_timer = None
        self.refresh()

    def on_unmount(self) -> None:
        if self._scramble_timer is not None:
            self._scramble_timer.stop()
            self._scramble_timer = None
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None

    def set_read_only_banner(self, text: str | None) -> None:
        self._read_only_banner = text
        self.refresh()

    def update_planner_detail(self, status: str, files_examined: int, candidate_tasks: int) -> None:
        """Update the planner detail summary line."""
        if status == "waiting for human input":
            self._planner_detail = "⏳ waiting for your answer"
        elif status:
            parts = [status]
            if files_examined > 0:
                parts.append(f"{files_examined} files examined")
            if candidate_tasks > 0:
                parts.append(f"{candidate_tasks} tasks")
            self._planner_detail = " · ".join(parts)
        else:
            self._planner_detail = ""
        self.refresh()

    def stop_countdown(self) -> None:
        """Cancel any running countdown without firing CountdownComplete."""
        self._countdown_value = 0
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None
        self.refresh()

    def start_countdown(self, seconds: int = 5) -> None:
        """Start a visual countdown before execution."""
        self._countdown_value = seconds
        # Stop any scramble animation
        self._animating = False
        if self._scramble_timer is not None:
            self._scramble_timer.stop()
            self._scramble_timer = None
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
        self._countdown_timer = self.set_interval(1.0, self._tick_countdown)
        self.refresh()

    def _tick_countdown(self) -> None:
        """Tick the countdown down by one second."""
        self._countdown_value -= 1
        if self._countdown_value <= 0:
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
                self._countdown_timer = None
            self.post_message(self.CountdownComplete())
        self.refresh()

    def render(self) -> str:
        def _vertically_center(text: str) -> str:
            return f"\n{text}" if text else text

        # Countdown takes priority over everything
        if self._countdown_value > 0:
            return _vertically_center(
                f"[bold #e3b341]⚡  L A U N C H I N G   I N[/]\n"
                f"[bold #f0883e]  {self._countdown_value}  [/]"
            )

        if self._animating and self._target_text:
            # Build partially-resolved text
            resolved = self._target_text[: self._resolved_count]
            remaining_len = len(self._target_text) - self._resolved_count
            scrambled = "".join(random.choice(_SCRAMBLE_CHARS) for _ in range(remaining_len))
            display = f"[bold {self._target_colour}]{self._target_icon}{resolved}[/][#484f58]{scrambled}[/]"
            if self._read_only_banner:
                display += f"\n[dim]{self._read_only_banner}[/]"
            elif self._phase == "planning" and self._planner_detail:
                display += f"\n[dim]{self._planner_detail}[/]"
            return _vertically_center(display)

        # Normal static render
        label, colour = _PHASE_BANNER.get(self._phase, ("Unknown", "#8b949e"))
        icon, _, text = label.partition(" ")
        if not text:
            text, icon = icon, ""
        words = text.upper().split()
        spaced_words = ["  ".join(w) for w in words]
        spaced = "   ".join(spaced_words)
        icon_prefix = f"{icon}  " if icon else ""
        banner = f"[bold {colour}]{icon_prefix}{spaced}[/]"
        if self._read_only_banner:
            banner += f"\n[dim]{self._read_only_banner}[/]"
        elif self._phase == "planning" and self._planner_detail:
            banner += f"\n[dim]{self._planner_detail}[/]"
        return _vertically_center(banner)


class RoutingAuditBanner(Static):
    """Persistent routing audit summary shown below the phase banner."""

    DEFAULT_CSS = """
    RoutingAuditBanner {
        height: auto;
        padding: 0 2;
        background: #11161d;
        border-bottom: tall #263041;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._routing_summary = ""

    def update_routing_summary(self, routing_summary: str) -> None:
        self._routing_summary = routing_summary.strip()
        self.refresh()

    @staticmethod
    def _colorize_provider_names(summary: str) -> str:
        return summary.replace("Claude", "[#22c55e]Claude[/]").replace("Codex", "[#58a6ff]Codex[/]")

    def render(self) -> str:
        if not self._routing_summary:
            return ""
        summary = self._routing_summary
        if summary.startswith("⚠ "):
            return f"[bold #d29922]⚠[/] {self._colorize_provider_names(summary[2:])}"
        return self._colorize_provider_names(summary)


class RetrievalDiagnosticsBanner(Static):
    """Compact banner showing retrieval vs fallback status per pipeline stage."""

    DEFAULT_CSS = """
    RetrievalDiagnosticsBanner {
        height: auto;
        padding: 0 2;
        background: #11161d;
        border-bottom: tall #263041;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._diagnostics: dict[str, dict] = {}

    def update_diagnostics(self, diagnostics: dict[str, dict]) -> None:
        self._diagnostics = diagnostics
        self.refresh()

    def render(self) -> str:
        if not self._diagnostics:
            return ""
        parts: list[str] = []
        for stage in ("planner", "agent", "reviewer"):
            diag = self._diagnostics.get(stage)
            if not diag:
                continue
            used = diag.get("used_retrieval", False)
            if used:
                conf = diag.get("confidence")
                conf_str = f" {conf:.0%}" if conf is not None else ""
                files = diag.get("top_files", [])
                file_count = f" ({len(files)} files)" if files else ""
                label = f"[#22c55e]retrieval{conf_str}{file_count}[/]"
            else:
                label = "[#8b949e]snapshot fallback[/]"
            parts.append(f"[bold]{stage}[/]: {label}")

        if not parts:
            return ""
        line = "  │  ".join(parts)
        # Add matched/missed terms from whichever stage has them
        term_parts: list[str] = []
        for stage in ("planner", "agent", "reviewer"):
            diag = self._diagnostics.get(stage)
            if not diag or not diag.get("used_retrieval"):
                continue
            matched = diag.get("matched_terms", [])
            missed = diag.get("missed_terms", [])
            if matched:
                term_parts.append(f"[#22c55e]✓ {' '.join(matched)}[/]")
            if missed:
                term_parts.append(f"[#d29922]✗ {' '.join(missed)}[/]")
        if term_parts:
            line += f"  │  {'  '.join(term_parts)}"
        return f"\U0001f50d {line}"


class DecisionBadge(Widget):
    """Shows count of pending questions/decisions at bottom of left panel."""

    DEFAULT_CSS = """
    DecisionBadge {
        height: 1;
        padding: 0 1;
        background: #11161d;
        border-top: tall #263041;
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
            return f"[{TEXT_MUTED}]No decisions waiting on you[/]"
        return f"[bold #d6a85f]◆ {self._count} decision{'s' if self._count != 1 else ''} pending[/]"


class IntegrationBadge(Widget):
    """Shows integration health status below DecisionBadge."""

    DEFAULT_CSS = """
    IntegrationBadge {
        height: 1;
        padding: 0 1;
        background: #11161d;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._degraded = False
        self._checking = False
        self._check_label = "Integration check running…"

    def update(
        self, *, degraded: bool = False, checking: bool = False, label: str | None = None
    ) -> None:
        self._degraded = degraded
        self._checking = checking
        if label:
            self._check_label = label
        self.refresh()

    def render(self) -> str:
        if self._checking:
            return f"[#d2a8ff]⧗ {self._check_label}[/]"
        if self._degraded:
            return "[bold #d29922]⚠ Integration risk detected[/]"
        return f"[{TEXT_MUTED}]Integrations nominal[/]"


class PipelineScreen(Screen):
    """Main pipeline execution screen with full-width phase banner + dynamic layout.

    Phase banner (full width, centered):
      - PhaseBanner — 5-line wide-spaced label

    Left panel (hidden during planning, shown during execution):
      - TaskList
      - QueueStatus
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
        height: 3;
        content-align: center middle;
        text-align: center;
        background: #11161d;
        border-bottom: tall #263041;
    }
    PipelineScreen > RoutingAuditBanner {
        width: 100%;
    }
    PipelineScreen > RetrievalDiagnosticsBanner {
        width: 100%;
    }
    #split-pane {
        height: 1fr;
        background: #0d1117;
    }
    #split-pane.full-width #left-panel {
        display: none;
    }
    #split-pane.full-width #right-panel {
        width: 100%;
    }
    #left-panel {
        width: 42;
        min-width: 34;
        max-width: 52;
        border-right: tall #263041;
        background: #11161d;
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
        background: #0d1117;
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
        background: #11161d;
        border-top: tall #263041;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "toggle_dag", "Toggle DAG"),
        Binding("w", "toggle_evidence", "Why files?", show=True),
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
        yield EvidencePanel(id="evidence-panel")
        yield PhaseBanner()
        yield RoutingAuditBanner(id="routing-audit-banner")
        yield RetrievalDiagnosticsBanner(id="retrieval-diagnostics-banner")
        with Horizontal(id="split-pane"):
            with Vertical(id="left-panel"):
                yield TaskList()
                yield QueueStatus()
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
                ("w", "Why Files?"),
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
            chat = self.query_one(ChatThread)
            chat.set_read_only(
                True,
                "This pipeline replay is read-only. Press Esc to return. "
                "To continue it, go back to history and use Shift+R.",
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
            self._refresh_interjection_chat_if_active()
            return
        if field == "review_output":
            self._handle_review_output_fast()
            return
        if field == "integration":
            self._refresh_integration_badge()
            return
        if field == "retrieval_diagnostics":
            self._update_retrieval_diagnostics()
            return
        if field == "task_retrieval_diagnostics":
            self._update_evidence_panel()
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
        # Fast path: cost/elapsed only need progress bar update
        if field == "cost":
            try:
                state = self._state
                self.query_one(PipelineProgress).update_progress(
                    state.done_count,
                    state.total_count,
                    state.total_cost_usd,
                    state.elapsed_seconds,
                    state.phase,
                )
            except Exception:
                pass
            return
        if field == "elapsed":
            try:
                state = self._state
                self.query_one(PipelineProgress).update_progress(
                    state.done_count,
                    state.total_count,
                    state.total_cost_usd,
                    state.elapsed_seconds,
                    state.phase,
                )
            except Exception:
                pass
            return
        if field == "planner_status":
            # Update planner detail banner
            try:
                state = self._state
                self.query_one(PhaseBanner).update_planner_detail(
                    state.planner_status,
                    state.planner_files_examined,
                    state.planner_candidate_tasks,
                )
            except Exception:
                pass
            return
        if field in (
            "tasks",
            "phase",
            "planner_output",
            "contracts_output",
            "planning",
            "scheduling",
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
            # Update shortcut bar hints based on pipeline phase or task state
            self._update_shortcut_bar()

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

    def _update_shortcut_bar(self, phase: str | None = None) -> None:
        """Update shortcut bar based on current pipeline phase and selected task state."""
        if phase is None:
            phase = self._state.phase
        shortcuts: list[tuple[str, str]] = [("j/k", "Tasks"), ("q", "Quit")]

        if phase in ("planning", "contracts", "countdown"):
            # Minimal shortcuts during non-interactive phases
            pass
        elif phase in ("executing", "retrying"):
            shortcuts.insert(0, ("Tab", "Next Active"))
            task = self._get_selected_task()
            if task:
                state = task.get("state", "")
                if state == "in_progress":
                    shortcuts.extend(
                        [("o", "Output"), ("t", "Chat"), ("i", "Interject"), ("d", "Diff")]
                    )
                elif state == "in_review":
                    shortcuts.extend([("r", "Review"), ("d", "Diff"), ("o", "Output")])
                elif state == "awaiting_input":
                    shortcuts.extend([("t", "Answer"), ("o", "Output")])
                elif state == "done":
                    shortcuts.extend([("d", "Diff"), ("o", "Output")])
                elif state == "error":
                    shortcuts.extend([("R", "Retry"), ("s", "Skip"), ("o", "Output")])
                else:  # todo, blocked
                    shortcuts.append(("o", "Output"))
        elif phase == "awaiting_input":
            shortcuts.insert(0, ("Enter", "Answer Question"))
            shortcuts.append(("d", "View Diff"))
        elif phase in ("partial_success",):
            shortcuts.insert(0, ("Enter", "View Results"))

        shortcuts.append(("g", "DAG"))
        try:
            bar = self.query_one(ShortcutBar)
            bar.update_shortcuts(shortcuts)
        except Exception:
            pass

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
            # Inject review strategy header before first streaming line
            task = state.tasks.get(tid, {})
            progress_header = format_review_progress(
                strategy=task.get("review_strategy"),
                diff_lines=task.get("review_diff_lines"),
                chunks=task.get("review_chunks", {}),
                current_chunk=task.get("review_current_chunk"),
                chunk_count=task.get("review_chunk_count"),
            )
            if progress_header:
                agent_output.append_unified("review", progress_header)
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
        # Check if any integration check is currently running + set descriptive label
        label = "Integration check running…"
        checking = any(c.get("status") == "running" for c in state.integration_checks.values())
        if checking:
            label = "Running post-merge integration check…"
        if not checking and state.integration_baseline:
            checking = state.integration_baseline.get("status") == "running"
            if checking:
                label = "Running baseline check (pre-execution)…"
        if not checking and state.integration_final_gate:
            checking = state.integration_final_gate.get("status") == "running"
            if checking:
                label = "Running final integration gate…"
        badge.update(degraded=state.integration_degraded, checking=checking, label=label)

    def _refresh_all(self) -> None:
        state = self._state
        task_list = self.query_one(TaskList)
        agent_output = self.query_one(AgentOutput)
        progress = self.query_one(PipelineProgress)
        queue_status = self.query_one(QueueStatus)
        dag = self.query_one(DagOverlay)
        phase_banner = self.query_one(PhaseBanner)
        decision_badge = self.query_one(DecisionBadge)
        self._refresh_integration_badge()
        queue_status.update(state.scheduling)

        ordered_tasks = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        scheduling_tasks = (state.scheduling or {}).get("tasks", {})
        # Inject merge substatus and preparing flag into task dicts for display
        is_preparing = state.phase in ("contracts", "countdown")
        for t in ordered_tasks:
            substatus = state.merge_substatus.get(t["id"])
            if substatus:
                t["merge_substatus"] = substatus
            else:
                t.pop("merge_substatus", None)
            if is_preparing:
                t["_preparing"] = True
            else:
                t.pop("_preparing", None)
            scheduling_info = scheduling_tasks.get(t["id"], {})
            if scheduling_info:
                t["_queue_status"] = scheduling_info.get("status", "")
                t["_priority_rank"] = scheduling_info.get("priority_rank")
                t["_blocked_reason"] = scheduling_info.get("reason", "")
                t["_blocking_task_ids"] = scheduling_info.get("blocking_task_ids", [])
            else:
                t.pop("_queue_status", None)
                t.pop("_priority_rank", None)
                t.pop("_blocked_reason", None)
                t.pop("_blocking_task_ids", None)
        task_list.update_tasks(
            ordered_tasks, state.selected_task_id, phase=state.phase, multi_repo=state.is_multi_repo
        )

        tid = state.selected_task_id

        # Show error detail view for errored tasks
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            unified = state.unified_log.get(tid, [])
            if task.get("state") == "error":
                error_task, error_lines = self._build_error_detail_context(tid, task)
                agent_output.render_error_detail(tid, error_task, error_lines)
            elif tid in self._agent_streaming_tasks or tid in self._review_streaming_tasks:
                # Streaming active — sync data WITHOUT toggling streaming off/on
                # (update_unified calls set_streaming(False) which causes double render)
                agent_output.sync_streaming(tid, task.get("title"), task.get("state"), unified)
            else:
                agent_output._error_mode = False  # Exit error mode without double-render

                # Generate blocked detail if task is blocked/waiting
                blocked_detail = None
                if task.get("_blocked_reason") and task.get("state") in ("todo", "blocked"):
                    from forge.core.blocked_reason import format_blocked_detail

                    blocked_detail = format_blocked_detail(task["_blocked_reason"])

                agent_output.update_unified(
                    tid,
                    task.get("title"),
                    task.get("state"),
                    unified,
                    blocked_detail=blocked_detail,
                )

                # Auto-switch to chat view when the selected task is awaiting input
                if task.get("state") == "awaiting_input":
                    self._auto_switch_chat(tid, task)
        elif state.phase == "planning" and state.planner_output:
            agent_output.clear_error_detail()
            agent_output.update_output(
                "planner", "Planning", "planning", state.planner_collapsed_output
            )
            agent_output.set_streaming(True)
            # Auto-switch to chat view when a planning question is pending
            planning_q = state.pending_questions.get("__planning__")
            if planning_q:
                self._auto_switch_planning_chat(planning_q)
        elif state.phase in ("contracts", "countdown"):
            agent_output.clear_error_detail()
            agent_output.update_output(
                "contracts",
                "Preparing",
                "contracts",
                ["⚙ Building task contracts for parallel execution…"],
            )
        else:
            agent_output.clear_error_detail()
            # During planning/contracts phases, show planner output even if
            # transiently empty — never show "No task selected" before tasks exist.
            if state.phase in ("planning", "planned", "contracts", "countdown"):
                agent_output.update_output(
                    "planner",
                    "Planning",
                    "planning",
                    state.planner_collapsed_output or ["⚙ Initializing planner..."],
                )
                agent_output.set_streaming(state.phase == "planning")
            else:
                agent_output.update_output(None, None, None, [])

        # Update diff for selected task
        diff_viewer = self.query_one(DiffViewer)
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            if self._active_view == "diff":
                # Prefer daemon-computed diff (always accurate)
                daemon_diff = state.task_diffs.get(tid, "")
                if daemon_diff:
                    self._diff_cache[tid] = daemon_diff
                    diff_viewer.update_diff(tid, task.get("title", ""), daemon_diff)
                elif tid in self._diff_cache:
                    diff_viewer.update_diff(tid, task.get("title", ""), self._diff_cache[tid])
                else:
                    diff_viewer.update_diff(tid, task.get("title", ""), "⏳ Loading diff...")
                    safe_create_task(
                        self._refresh_diff_async(tid), logger=logger, name="refresh-diff"
                    )
            else:
                # Not in diff view — update cache from daemon if available
                daemon_diff = state.task_diffs.get(tid, "")
                if daemon_diff:
                    self._diff_cache[tid] = daemon_diff
                diff_text = self._diff_cache.get(tid, "")
                diff_viewer.update_diff(tid, task.get("title", ""), diff_text)

        progress.update_progress(
            state.done_count,
            state.total_count,
            state.total_cost_usd,
            state.elapsed_seconds,
            state.phase,
        )
        progress.update_tasks(ordered_tasks)
        dag.update_tasks(ordered_tasks)
        phase_banner.update_phase(state.phase)
        self._clear_stale_chat_question()

        split_pane = self.query_one("#split-pane")
        if state.phase in _SIDEBAR_HIDDEN_PHASES:
            split_pane.add_class("full-width")
        else:
            split_pane.remove_class("full-width")

        decision_badge.update_count(len(state.pending_questions))
        self._refresh_interjection_chat_if_active()
        self._update_evidence_panel()

    def _build_error_detail_context(self, task_id: str, task: dict) -> tuple[dict, list[str]]:
        """Choose the most useful failure context for the selected errored task.

        Review failures should show reviewer feedback and review-stream lines rather
        than generic agent output, otherwise the UI says "review error" without
        exposing the actual review error in the active pane.
        """
        state = self._state
        review_data = task.get("review") or {}
        review_gates = state.review_gates.get(task_id) or task.get("review_gates") or {}
        review_failed = bool(
            review_data.get("passed") is False
            or review_gates.get("gate2_llm_review", {}).get("status") == "failed"
        )

        error_task = dict(task)
        if review_failed:
            review_details = (
                review_data.get("details")
                or review_gates.get("gate2_llm_review", {}).get("details")
                or task.get("error")
            )
            if review_details:
                error_task["error"] = review_details
            review_lines = state.review_output.get(task_id, [])
            if review_lines:
                return error_task, review_lines

        return error_task, state.agent_output.get(task_id, [])

    def _update_routing_audit(self, routing_summary: str) -> None:
        """Update the persistent routing audit banner."""
        self.query_one("#routing-audit-banner", RoutingAuditBanner).update_routing_summary(
            routing_summary
        )

    def _update_retrieval_diagnostics(self) -> None:
        """Update the retrieval diagnostics banner from state."""
        try:
            banner = self.query_one("#retrieval-diagnostics-banner", RetrievalDiagnosticsBanner)
            banner.update_diagnostics(self._state.retrieval_diagnostics)
        except Exception:
            pass

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
            cached = self._diff_cache[tid]
            # Don't return cached error messages — they may be stale
            if not cached.startswith(("No pipeline", "git diff failed", "Error")):
                return cached
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

        if view != "chat":
            try:
                chat_input = self.query_one("#chat-input", Input)
                if chat_input.has_focus:
                    chat_input.blur()
            except Exception:
                pass
            focus_target = AgentOutput if view == "output" else DiffViewer
            self.call_after_refresh(lambda: self._focus_panel_widget(focus_target))

    def _focus_panel_widget(self, widget_cls: type[Widget]) -> None:
        """Move focus onto the visible right-panel widget after layout refresh."""
        try:
            self.app.set_focus(self.query_one(widget_cls))
        except Exception:
            pass

    def _auto_switch_planning_chat(self, question: dict) -> None:
        """Switch to chat view for a planning question from the Architect."""
        state = self._state
        chat = self.query_one(ChatThread)
        chat.set_mode("answer")
        chat.task_id = "__planning__"
        work_lines = state.planner_output
        history = state.question_history.get("__planning__", [])
        chat.update_question(question, work_lines, history)

        if self._active_view != "chat":
            self._set_view("chat")
        # Always focus the input — use a short delay to ensure layout is settled
        if not self._read_only:
            self.set_timer(0.15, self._focus_chat_input)

    def _focus_chat_input(self) -> None:
        """Focus the chat input after layout settles."""
        try:
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def _show_interjection_chat(self, task_id: str, task: dict, *, focus_input: bool) -> None:
        """Populate the chat panel with steering context for a running task."""
        chat = self.query_one(ChatThread)
        chat.task_id = task_id
        chat.update_interjection_context(
            self._state.agent_output.get(task_id, []),
            task.get("interjections", []),
        )
        if self._active_view != "chat":
            self._set_view("chat")
        if focus_input and not self._read_only:
            self.set_timer(0.15, self._focus_chat_input)

    def _refresh_interjection_chat_if_active(self) -> None:
        """Keep the interjection panel synced while tasks stream or selection changes."""
        if self._active_view != "chat":
            return
        try:
            chat = self.query_one(ChatThread)
        except Exception:
            return
        if chat.mode != "interjection":
            return
        tid = self._state.selected_task_id
        if not tid:
            return
        task = self._state.tasks.get(tid)
        if not task:
            return
        if task.get("state") not in ("in_progress", "awaiting_input"):
            self._set_view("output")
            return
        self._show_interjection_chat(tid, task, focus_input=False)

    def _auto_switch_chat(self, task_id: str, task: dict) -> None:
        """Switch to chat view and populate question when task needs input."""
        state = self._state
        question = state.pending_questions.get(task_id)
        if not question:
            return
        chat = self.query_one(ChatThread)
        chat.set_mode("answer")
        chat.task_id = task_id
        work_lines = state.agent_output.get(task_id, [])
        history = state.question_history.get(task_id, [])
        chat.update_question(question, work_lines, history)

        # Only auto-switch if we're not already in chat view
        if self._active_view != "chat":
            self._set_view("chat")
        # Always focus input — delay to let layout settle
        if not self._read_only:
            self.set_timer(0.15, self._focus_chat_input)

    def _clear_stale_chat_question(self) -> None:
        """Drop answer UI once there is no longer a pending question."""
        chat = self.query_one(ChatThread)
        if chat.mode != "answer" or not chat.has_question:
            return

        state = self._state
        planning_pending = state.pending_questions.get("__planning__") is not None
        selected_pending = (
            state.selected_task_id is not None
            and state.pending_questions.get(state.selected_task_id) is not None
        )
        if planning_pending or selected_pending:
            return

        chat.clear_question()
        if self._active_view == "chat" and not self._read_only:
            self._set_view("output")

    def _selected_pending_question(self) -> tuple[str, dict] | None:
        """Return the live question that should drive the chat panel, if any."""
        planning_q = self._state.pending_questions.get("__planning__")
        if planning_q:
            return "__planning__", planning_q

        tid = self._state.selected_task_id
        if tid and tid in self._state.tasks:
            question = self._state.pending_questions.get(tid)
            if question:
                return tid, question
        return None

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
        self._update_shortcut_bar()

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

    def action_toggle_evidence(self) -> None:
        panel = self.query_one("#evidence-panel", EvidencePanel)
        if panel.is_open:
            panel.toggle()
        else:
            tid = self._state.selected_task_id
            diag = self._state.task_retrieval_diagnostics.get(tid or "", {})
            title = self._state.tasks.get(tid or "", {}).get("title", tid or "")
            panel.update_evidence(diag, title)
            panel.toggle()

    def _update_evidence_panel(self) -> None:
        """Refresh evidence panel data if it is currently visible."""
        try:
            panel = self.query_one("#evidence-panel", EvidencePanel)
        except Exception:
            return
        if not panel.is_open:
            return
        tid = self._state.selected_task_id
        diag = self._state.task_retrieval_diagnostics.get(tid or "", {})
        title = self._state.tasks.get(tid or "", {}).get("title", tid or "")
        panel.update_evidence(diag, title)

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
        self._refresh_all()

    def action_view_chat(self) -> None:
        question_target = self._selected_pending_question()
        if question_target:
            task_id, question = question_target
            if task_id == "__planning__":
                self._auto_switch_planning_chat(question)
            else:
                task = self._state.tasks.get(task_id, {})
                self._auto_switch_chat(task_id, task)
            self._refresh_all()
            return

        selected = self._get_selected_task()
        if selected and selected.get("state") in ("in_progress", "awaiting_input"):
            self.app.notify("No question pending — press i to interject", timeout=4)
        else:
            self.app.notify("No question pending", severity="warning", timeout=4)
        self._refresh_all()

    def action_view_diff(self) -> None:
        task = self._get_selected_task()
        if not task:
            self.app.notify("No task selected", severity="warning")
            return
        state = task.get("state", "")
        if state not in ("in_review", "done", "merging", "in_progress", "awaiting_approval"):
            self.app.notify(f"Diff not available — task is {state}", severity="warning")
            return
        self._set_view("diff")
        self._refresh_all()

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
        task = self._state.tasks[tid]
        state = task.get("state", "")
        if state not in ("in_review", "awaiting_approval"):
            self.app.notify(f"Review not available — task is {state}", severity="warning")
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
            self.app.notify("No task selected", severity="warning")
            return
        task = self._state.tasks[tid]
        state = task.get("state", "")
        if state not in ("in_progress", "awaiting_input"):
            self.app.notify(f"Interject not available — task is {state}", severity="warning")
            return
        self._show_interjection_chat(tid, task, focus_input=True)

    def action_retry_task(self) -> None:
        """Retry the selected task (only active for error-state tasks)."""
        if self._read_only:
            return
        task = self._get_selected_task()
        if not task:
            return
        if task.get("state") != "error":
            self.app.notify(
                f"Retry not available — task is {task.get('state', 'unknown')}", severity="warning"
            )
            return
        tid = task["id"]
        safe_create_task(self._retry_task(tid), name=f"retry-{tid}")

    async def _sync_pipeline_tasks_from_db(self, pipeline_id: str) -> None:
        """Refresh local task states from the DB after an inline recovery action."""
        db = getattr(self.app, "_db", None)
        if not db:
            return
        tasks = await db.list_tasks_by_pipeline(pipeline_id)
        updated = False
        for task in tasks:
            if task.id not in self._state.tasks:
                continue
            local = self._state.tasks[task.id]
            local["state"] = task.state
            error = getattr(task, "error", None)
            if task.state == "error" and error:
                local["error"] = error
            else:
                local.pop("error", None)
            updated = True
        if updated:
            self._state._notify("tasks")

    async def _retry_task(self, task_id: str) -> None:
        """Reset an errored task to 'todo' and let the daemon re-dispatch it."""
        try:
            db = getattr(self.app, "_db", None)
            if not db:
                logger.warning("Cannot retry task %s: no database connection", task_id)
                return
            pipeline_id = getattr(self.app, "_pipeline_id", None)
            daemon = getattr(self.app, "_daemon", None)
            if daemon and pipeline_id:
                await daemon.retry_task(task_id, db, pipeline_id)
                await self._sync_pipeline_tasks_from_db(pipeline_id)
            else:
                await db.update_task_state(task_id, "todo")
                # Update TUI state immediately so user sees the change
                if task_id in self._state.tasks:
                    self._state.tasks[task_id]["state"] = "todo"
                    self._state.tasks[task_id].pop("error", None)
                    self._state._notify("tasks")
            logger.info("Task %s reset to 'todo' for retry", task_id)
        except Exception:
            logger.exception("Failed to retry task %s", task_id)

    def action_skip_task(self) -> None:
        """Skip the selected errored task (only active for error-state tasks)."""
        if self._read_only:
            return
        task = self._get_selected_task()
        if not task:
            return
        if task.get("state") != "error":
            self.app.notify(
                f"Skip not available — task is {task.get('state', 'unknown')}", severity="warning"
            )
            return
        tid = task["id"]
        safe_create_task(self._skip_task(tid), name=f"skip-{tid}")

    async def _cancel_task_subgraph(self, task_id: str, pipeline_id: str) -> list[str]:
        """Cancel a skipped task plus all unfinished transitive dependents."""
        db = getattr(self.app, "_db", None)
        if not db:
            return []

        tasks = await db.list_tasks_by_pipeline(pipeline_id)
        dependents_map: dict[str, list[str]] = {}
        cancellable_ids = {task.id for task in tasks if task.state not in ("done", "cancelled")}
        for task in tasks:
            for dep_id in task.depends_on or []:
                dependents_map.setdefault(dep_id, []).append(task.id)

        cancelled: list[str] = []
        queue: deque[str] = deque([task_id])
        seen: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            if current in cancellable_ids:
                await db.update_task_state(current, "cancelled")
                cancelled.append(current)
            for dependent_id in dependents_map.get(current, []):
                queue.append(dependent_id)
        return cancelled

    async def _skip_task(self, task_id: str) -> None:
        """Mark an errored task as cancelled so the pipeline can proceed."""
        try:
            db = getattr(self.app, "_db", None)
            if not db:
                logger.warning("Cannot skip task %s: no database connection", task_id)
                return
            pipeline_id = getattr(self.app, "_pipeline_id", None)
            if pipeline_id:
                await self._cancel_task_subgraph(task_id, pipeline_id)
                await self._sync_pipeline_tasks_from_db(pipeline_id)
            else:
                await db.update_task_state(task_id, "cancelled")
                if task_id in self._state.tasks:
                    self._state.tasks[task_id]["state"] = "cancelled"
                    self._state.tasks[task_id].pop("error", None)
                    self._state._notify("tasks")
            logger.info("Task %s skipped (cancelled)", task_id)
        except Exception:
            logger.exception("Failed to skip task %s", task_id)

    def action_pop_screen(self) -> None:
        """Esc — only active in read-only mode."""
        if self._read_only:
            self.app.pop_screen()
            return
        if self._active_view != "chat":
            return
        try:
            chat = self.query_one(ChatThread)
        except Exception:
            return
        if chat.mode == "interjection":
            self._set_view("output")
            self._refresh_all()

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
