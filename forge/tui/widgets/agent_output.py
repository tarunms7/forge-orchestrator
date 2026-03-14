"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import VerticalScroll

from forge.tui.widgets.search_overlay import apply_highlights

logger = logging.getLogger("forge.tui.agent_output")

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_TYPING_FRAMES = ["▍", "▌", "▍", " "]

_SECTION_COLORS = {
    "agent": "#f0883e",
    "review": "#a371f7",
    "gate": "#79c0ff",
    "system": "#8b949e",
}

_ERROR_TAIL_LINES = 20


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    if not task_id:
        return "[#8b949e]No task selected[/]"
    if task_id == "planner":
        return "[bold #a371f7]⚙ Planner[/] [#8b949e]exploring codebase & building task graph...[/]"
    state_label = f" [{_escape(state)}]" if state else ""
    return f"[bold #58a6ff]{_escape(task_id)}[/]: {_escape(title or 'Untitled')} [#8b949e]{state_label}[/]"


def format_error_detail(task_id: str, task: dict, output_lines: list[str]) -> str:
    """Render the error detail combined view as a single Rich markup string."""
    title = task.get("title", "Untitled")
    error = task.get("error") or "Unknown error"
    files_changed = task.get("files_changed", [])

    parts: list[str] = []
    # Header
    parts.append(f"[bold #f85149]✖ {_escape(title)} — ERROR[/]")
    parts.append("[#30363d]" + "─" * 60 + "[/]")
    # Error message
    parts.append(f"[#f85149]{_escape(error)}[/]")
    # File list
    if files_changed:
        parts.append("")
        for f in files_changed:
            parts.append(f"[#8b949e]  {_escape(f)}[/]")
    # Separator and last output
    parts.append("")
    parts.append("[#8b949e]── Last output ──[/]")
    tail = output_lines[-_ERROR_TAIL_LINES:] if output_lines else []
    if tail:
        parts.extend(tail)
    else:
        parts.append("[#8b949e]No output captured[/]")
    # Action bar
    parts.append("")
    parts.append("[#8b949e]\\[R] retry  \\[s] skip  \\[Esc] dismiss[/]")
    return "\n".join(parts)


def format_output(
    lines: list[str],
    spinner_frame: int = 0,
    streaming: bool = False,
    typing_frame: int = 0,
) -> str:
    if not lines:
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        return f"[#58a6ff]{frame}[/] [#8b949e]Waiting for output...[/]"
    parts = list(lines)
    if streaming:
        cursor = _TYPING_FRAMES[typing_frame % len(_TYPING_FRAMES)]
        parts.append(f"[#58a6ff]● Typing{cursor}[/]")
    return "\n".join(parts)


def format_unified_output(
    entries: list[tuple[str, str]],
    spinner_frame: int = 0,
    streaming: bool = False,
    typing_frame: int = 0,
) -> str:
    """Render unified log with section headers when source type changes."""
    if not entries:
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        return f"[#58a6ff]{frame}[/] [#8b949e]Waiting for output...[/]"

    parts: list[str] = []
    current_section: str | None = None
    review_count = 0

    for source_type, line in entries:
        # Gate lines merge into review section
        effective = "review" if source_type == "gate" else source_type

        if effective != current_section:
            current_section = effective
            color = _SECTION_COLORS.get(effective, "#8b949e")
            if effective == "review":
                review_count += 1
                label = f"REVIEW {review_count}"
            else:
                label = "AGENT"
            header = f"[{color}]───── {label} " + "─" * max(1, 50 - len(label)) + "[/]"
            if parts:
                parts.append("")  # blank line before new section
            parts.append(header)

        if source_type == "gate":
            parts.append(f"  [#79c0ff]{line}[/]")
        elif source_type == "review":
            parts.append(f"[#a371f7]{line}[/]")
        else:
            parts.append(line)

    if streaming:
        cursor = _TYPING_FRAMES[typing_frame % len(_TYPING_FRAMES)]
        parts.append(f"[#58a6ff]● Typing{cursor}[/]")

    return "\n".join(parts)


class AgentOutput(Widget):
    """Scrollable agent output with fixed header and auto-scrolling body."""

    DEFAULT_CSS = """
    AgentOutput {
        layout: vertical;
    }
    #agent-header {
        height: auto;
        max-height: 3;
    }
    #agent-separator {
        height: 1;
    }
    #agent-scroll {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._task_id: str | None = None
        self._title: str | None = None
        self._state: str | None = None
        self._lines: list[str] = []
        self._spinner_frame: int = 0
        self._streaming: bool = False
        self._typing_frame: int = 0
        self._typing_timer = None
        self._error_mode: bool = False
        self._unified_entries: list[tuple[str, str]] = []
        self._search_pattern: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(format_header(None, None, None), id="agent-header")
        yield Static("[#30363d]" + "─" * 60 + "[/]", id="agent-separator")
        with VerticalScroll(id="agent-scroll"):
            yield Static("", id="agent-content")

    def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def on_unmount(self) -> None:
        if hasattr(self, "_spinner_timer") and self._spinner_timer:
            self._spinner_timer.stop()
        if self._typing_timer is not None:
            self._typing_timer.stop()
            self._typing_timer = None

    def _tick_spinner(self) -> None:
        if self._lines or self._unified_entries:
            return
        self._spinner_frame += 1
        try:
            content = self.query_one("#agent-content", Static)
            content.update(format_output([], self._spinner_frame))
        except Exception:
            pass

    def _tick_typing(self) -> None:
        """Animate the typing indicator cursor."""
        if not self._streaming:
            return
        self._typing_frame += 1
        try:
            content = self.query_one("#agent-content", Static)
            if self._unified_entries:
                content.update(
                    format_unified_output(
                        self._unified_entries,
                        self._spinner_frame,
                        streaming=True,
                        typing_frame=self._typing_frame,
                    )
                )
            else:
                content.update(
                    format_output(
                        self._lines,
                        self._spinner_frame,
                        streaming=True,
                        typing_frame=self._typing_frame,
                    )
                )
        except Exception:
            pass

    def set_streaming(self, active: bool) -> None:
        """Show or hide the typing indicator.

        When active=True, a pulsing cursor is appended to the rendered output
        and animates via a timer. When active=False, the indicator is removed
        and the animation stops. Safe to call before the widget is composed.
        """
        if active == self._streaming:
            return
        self._streaming = active
        if active:
            self._typing_frame = 0
            try:
                self._typing_timer = self.set_interval(0.3, self._tick_typing)
            except Exception:
                pass  # Not yet composed
        else:
            if self._typing_timer is not None:
                self._typing_timer.stop()
                self._typing_timer = None
            self._typing_frame = 0

        # Refresh the content widget to show/hide the indicator
        try:
            content = self.query_one("#agent-content", Static)
            if self._unified_entries:
                content.update(
                    format_unified_output(
                        self._unified_entries,
                        self._spinner_frame,
                        streaming=self._streaming,
                        typing_frame=self._typing_frame,
                    )
                )
            else:
                content.update(
                    format_output(
                        self._lines,
                        self._spinner_frame,
                        streaming=self._streaming,
                        typing_frame=self._typing_frame,
                    )
                )
        except Exception:
            pass  # Not yet composed

    def _is_near_bottom(self) -> bool:
        """Check if the scroll position is near the bottom.

        Uses a threshold of 3 content-units (roughly 3 lines). Textual's
        VerticalScroll exposes scroll_y and virtual_size in content units,
        so the subtraction gives us the distance from the bottom edge.
        """
        try:
            scroll = self.query_one("#agent-scroll", VerticalScroll)
            return scroll.scroll_y >= scroll.virtual_size.height - scroll.size.height - 3
        except Exception:
            return True  # Default to auto-scroll if widget not ready

    def append_line(self, line: str) -> None:
        """Efficiently append a single line of streaming output.

        Appends to self._lines, updates only the #agent-content Static widget
        (NOT the header), and auto-scrolls only if user is near the bottom.
        This is the hot-path method for streaming.
        """
        self._lines.append(line)
        try:
            content = self.query_one("#agent-content", Static)
            content.update(
                format_output(
                    self._lines,
                    self._spinner_frame,
                    streaming=self._streaming,
                    typing_frame=self._typing_frame,
                )
            )
            if self._is_near_bottom():
                self.call_after_refresh(self._scroll_to_end)
        except Exception:
            pass  # Not yet composed

    def update_output(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
        lines: list[str],
    ) -> None:
        """Full refresh of the output panel.

        Sets _task_id, _title, _state, _lines and re-renders both header and
        content. Used for task switching, NOT for streaming. Resets streaming
        state (calls set_streaming(False) internally).
        """
        self._task_id = task_id
        self._title = title
        self._state = state
        self._lines = list(lines)  # Copy to avoid aliasing state.agent_output[tid]
        self._unified_entries = []  # Clear unified data when switching to line-based mode

        # Reset streaming state on full refresh
        self.set_streaming(False)

        try:
            self.query_one("#agent-header", Static).update(
                format_header(task_id, title, state)
            )
            self.query_one("#agent-content", Static).update(
                format_output(lines, self._spinner_frame)
            )
            if lines and self._is_near_bottom():
                self.call_after_refresh(self._scroll_to_end)
        except Exception:
            pass  # Not yet composed

    def update_header(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
    ) -> None:
        """Update only the header line. Use during streaming to avoid replacing content."""
        self._task_id = task_id
        self._title = title
        self._state = state
        try:
            self.query_one("#agent-header", Static).update(
                format_header(task_id, title, state)
            )
        except Exception:
            pass

    def append_unified(self, source_type: str, line: str) -> None:
        """Append a single unified log entry during streaming."""
        self._unified_entries.append((source_type, line))
        try:
            content = self.query_one("#agent-content", Static)
            content.update(
                format_unified_output(
                    self._unified_entries,
                    self._spinner_frame,
                    streaming=self._streaming,
                    typing_frame=self._typing_frame,
                )
            )
            if self._is_near_bottom():
                self.call_after_refresh(self._scroll_to_end)
        except Exception:
            pass  # Not yet composed

    def update_unified(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
        entries: list[tuple[str, str]],
    ) -> None:
        """Full refresh with unified log entries.

        Replaces _unified_entries with the authoritative state from TuiState.
        """
        self._task_id = task_id
        self._title = title
        self._state = state
        self._unified_entries = list(entries)
        self._lines = []  # Clear line-based data when switching to unified mode
        self.set_streaming(False)
        try:
            self.query_one("#agent-header", Static).update(
                format_header(task_id, title, state)
            )
            self.query_one("#agent-content", Static).update(
                format_unified_output(entries, self._spinner_frame)
            )
            if entries and self._is_near_bottom():
                self.call_after_refresh(self._scroll_to_end)
        except Exception:
            pass  # Not yet composed

    def set_search_highlights(self, pattern: str | None) -> int:
        """Apply or clear search highlights on the current content.

        When pattern is not None, wraps matching text in Rich markup
        highlight tags ([on #d29922]...[/]) and returns match count.
        When pattern is None, clears all highlights. Re-renders content
        after applying.
        """
        self._search_pattern = pattern
        return self._apply_search_highlights()

    def _apply_search_highlights(self) -> int:
        """Re-render content with current search highlights applied."""
        pattern = self._search_pattern

        # Build the base rendered content
        if self._error_mode:
            base = format_error_detail(
                self._task_id or "",
                {"title": self._title, "error": "", "state": self._state},
                self._lines,
            )
        elif self._unified_entries:
            base = format_unified_output(
                self._unified_entries,
                self._spinner_frame,
                streaming=self._streaming,
                typing_frame=self._typing_frame,
            )
        else:
            base = format_output(
                self._lines,
                self._spinner_frame,
                streaming=self._streaming,
                typing_frame=self._typing_frame,
            )

        if pattern:
            highlighted, count = apply_highlights(base, pattern)
        else:
            highlighted, count = base, 0

        try:
            content = self.query_one("#agent-content", Static)
            content.update(highlighted)
        except Exception:
            pass
        return count

    def _scroll_to_end(self) -> None:
        try:
            self.query_one("#agent-scroll", VerticalScroll).scroll_end(
                animate=False
            )
        except Exception:
            pass

    @property
    def is_error_mode(self) -> bool:
        """Whether the widget is currently displaying an error detail view."""
        return self._error_mode

    def render_error_detail(self, task_id: str, task: dict, output_lines: list[str]) -> None:
        """Render the error detail combined view, replacing normal output."""
        self._error_mode = True
        self._task_id = task_id
        self._title = task.get("title")
        self._state = "error"
        self._lines = list(output_lines)
        self.set_streaming(False)

        rendered = format_error_detail(task_id, task, output_lines)
        try:
            self.query_one("#agent-header", Static).update(
                f"[bold #f85149]✖ {_escape(task.get('title', 'Untitled'))} — ERROR[/]"
            )
            self.query_one("#agent-content", Static).update(rendered)
            self.call_after_refresh(self._scroll_to_end)
        except Exception:
            pass  # Not yet composed

    def clear_error_detail(self) -> None:
        """Exit error detail view mode and return to normal output rendering."""
        self._error_mode = False
        # Re-render normal view — use unified mode if we have unified entries,
        # otherwise fall back to line-based rendering
        try:
            self.query_one("#agent-header", Static).update(
                format_header(self._task_id, self._title, self._state)
            )
            if self._unified_entries:
                self.query_one("#agent-content", Static).update(
                    format_unified_output(self._unified_entries, self._spinner_frame)
                )
            else:
                self.query_one("#agent-content", Static).update(
                    format_output(self._lines, self._spinner_frame)
                )
        except Exception:
            pass  # Not yet composed
