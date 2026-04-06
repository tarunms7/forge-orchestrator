"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from forge.tui.theme import (
    ACCENT_CYAN,
    ACCENT_GOLD,
    ACCENT_ORANGE,
    ACCENT_PURPLE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from forge.tui.widgets.search_overlay import apply_highlights

logger = logging.getLogger("forge.tui.agent_output")

# Breathing pulse — size cycles with color
_SPINNER_FRAMES = [
    ("[#58a6ff]●[/]", "#58a6ff"),  # Bright, full
    ("[#3b82c4]◉[/]", "#3b82c4"),  # Medium
    ("[#484f58]○[/]", "#484f58"),  # Dim, hollow
    ("[#3b82c4]◉[/]", "#3b82c4"),  # Medium
    ("[#58a6ff]●[/]", "#58a6ff"),  # Bright, full
]

# Legacy typing frames (kept for backward compatibility with tests)
_TYPING_FRAMES = ["▍", "▌", "▍", " "]

# Shimmer forging animation
_FORGING_WORD = "forging"
_SHIMMER_COLORS = [
    "#484f58",  # Dim base (TEXT_MUTED)
    "#6e7681",  # Mid step 1
    "#8b949e",  # Mid step 2
    "#d6a85f",  # Bright (ACCENT_GOLD)
    "#e8c48a",  # Peak (brighter gold)
]


def _render_forging_shimmer(frame: int, width: int = 72) -> str:
    """Render the 'forging' shimmer animation with a brightness wave.

    Args:
        frame: Current animation frame number
        width: Total visual width for right-alignment (default 72)

    Returns:
        Rich markup string with right-aligned shimmer text
    """
    word = _FORGING_WORD
    hotspot_pos = frame % (len(word) + 3)  # +3 for pause between sweeps

    chars = []
    for i, char in enumerate(word):
        distance = abs(i - hotspot_pos)
        # 3-character glow: distance 0=peak, 1=bright, 2=mid, else=dim base
        if distance == 0:
            color = _SHIMMER_COLORS[4]  # Peak: #e8c48a
        elif distance == 1:
            color = _SHIMMER_COLORS[3]  # Bright: #d6a85f
        elif distance == 2:
            color = _SHIMMER_COLORS[2]  # Mid: #8b949e
        else:
            color = _SHIMMER_COLORS[0]  # Dim base: #484f58
        chars.append(f"[{color}]{char}[/]")

    shimmer_text = "".join(chars)
    # Calculate padding for right alignment (account for visual chars only)
    padding = max(0, width - len(word))
    return " " * padding + shimmer_text


# Dim→bright fade-in for new lines
_FADE_STEPS = ["#30363d", "#484f58", "#6e7681", "#8b949e", "#c9d1d9"]
_FADE_INTERVAL = 0.06  # 60ms per step, 300ms total

_SECTION_COLORS = {
    "agent": TEXT_PRIMARY,  # Bright text for agent output — primary content
    "review": ACCENT_PURPLE,  # Purple for LLM review
    "gate": ACCENT_CYAN,  # Light blue for gate results (lint/test/build)
    "system": TEXT_SECONDARY,  # Gray for system messages
}

_SECTION_HEADER_COLORS = {
    "agent": ACCENT_ORANGE,  # Orange header for AGENT sections
    "review": ACCENT_PURPLE,  # Purple header for REVIEW sections
    "gate": ACCENT_CYAN,  # Blue header for gate sections
    "system": TEXT_SECONDARY,  # Gray header for system sections
}

_ERROR_TAIL_LINES = 20

_IN_CODE_BLOCK = False  # Module-level state for code block tracking


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def _format_state_chip(state: str | None) -> str:
    """Render a compact state chip for the output header."""
    if not state:
        return ""
    colors = {
        "planning": ACCENT_PURPLE,
        "planned": ACCENT_PURPLE,
        "running": ACCENT_ORANGE,
        "in_progress": ACCENT_ORANGE,
        "awaiting_input": ACCENT_ORANGE,
        "in_review": ACCENT_PURPLE,
        "awaiting_approval": ACCENT_CYAN,
        "merging": ACCENT_CYAN,
        "done": "#3fb950",
        "error": "#f85149",
        "cancelled": TEXT_SECONDARY,
    }
    color = colors.get(state.lower(), TEXT_SECONDARY)
    label = _escape(state.replace("_", " ").upper())
    return f"[bold #0d1117 on {color}] {label} [/]"


def _iter_logical_lines(chunk: str) -> list[str]:
    """Split streamed chunks into logical lines for incremental markdown rendering."""
    return chunk.splitlines() or [chunk]


def _render_markdown(line: str) -> str | None:
    """Convert common markdown patterns to Rich markup for TUI display.

    Handles headers, bold, inline code, code fences, and bullets.
    """
    import re

    global _IN_CODE_BLOCK
    stripped = line.strip()

    # Code fence toggle
    if stripped.startswith("```"):
        _IN_CODE_BLOCK = not _IN_CODE_BLOCK
        return None

    # Inside code block — render as dim monospace, escape markup
    if _IN_CODE_BLOCK:
        return f"[#8b949e]  {_escape(line)}[/]"

    # Headers
    if stripped.startswith("### "):
        return f"[bold #58a6ff]{_escape(stripped[4:])}[/]"
    if stripped.startswith("## "):
        return f"[bold #58a6ff]{_escape(stripped[3:])}[/]"
    if stripped.startswith("# "):
        return f"[bold #58a6ff]{_escape(stripped[2:])}[/]"

    # Bullets
    if stripped.startswith("- "):
        inner = _escape(stripped[2:])
        # Apply inline formatting to already-escaped bullet content
        inner = re.sub(r"\*\*(.+?)\*\*", r"[bold]\1[/]", inner)
        inner = re.sub(r"`([^`]+)`", r"[#79c0ff]\1[/]", inner)
        return f"  • {inner}"

    # Escape all text first to prevent Rich markup injection
    line = _escape(line)
    # Bold: **text**
    line = re.sub(r"\*\*(.+?)\*\*", r"[bold]\1[/]", line)
    # Inline code: `text`
    line = re.sub(r"`([^`]+)`", r"[#79c0ff]\1[/]", line)

    return line


def _render_unified_chunk(source_type: str, chunk: str) -> list[str]:
    """Render a streamed chunk, preserving markdown state across logical lines."""
    rendered_lines: list[str] = []
    for line in _iter_logical_lines(chunk):
        if source_type == "gate":
            rendered_lines.append(f"  [#79c0ff]{_escape(line)}[/]" if line else "")
            continue

        rendered = _render_markdown(line)
        if rendered is None:
            continue
        if source_type == "review":
            rendered_lines.append(f"[#a371f7]{rendered}[/]" if rendered else "")
        else:
            rendered_lines.append(rendered)
    return rendered_lines


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    if not task_id:
        return (
            f"[bold {ACCENT_GOLD}]LIVE OUTPUT[/]\n"
            f"[{TEXT_SECONDARY}]No task selected. Select a task to inspect agent, "
            "review, and gate output.[/]"
        )
    if task_id == "planner":
        return (
            f"[bold {ACCENT_PURPLE}]Planner Deck[/]\n"
            f"[{TEXT_SECONDARY}]Planner exploring codebase & building task graph...[/]"
        )
    header = f"[bold {ACCENT_GOLD}]LIVE OUTPUT[/]  [{TEXT_SECONDARY}]task {_escape(task_id)}[/]"
    title_line = f"[bold {TEXT_PRIMARY}]{_escape(title or 'Untitled')}[/]"
    state_chip = _format_state_chip(state)
    if state_chip:
        title_line += f"  {state_chip}"
    return f"{header}\n{title_line}"


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
        frame_data = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        spinner = frame_data[0] if isinstance(frame_data, tuple) else frame_data
        return (
            f"{spinner} [#8b949e]Waiting for output...[/]\n"
            "[#484f58]Forge will stream agent, review, and gate logs here.[/]"
        )
    global _IN_CODE_BLOCK
    _IN_CODE_BLOCK = False  # Reset at start of full render
    parts: list[str] = []
    for chunk in lines:
        for line in _iter_logical_lines(chunk):
            rendered = _render_markdown(line)
            if rendered is None:
                continue
            parts.append(rendered)
    if streaming:
        parts.append(_render_forging_shimmer(typing_frame))
    return "\n".join(parts)


def format_unified_output(
    entries: list[tuple[str, str]],
    spinner_frame: int = 0,
    streaming: bool = False,
    typing_frame: int = 0,
) -> str:
    """Render unified log with section headers when source type changes."""
    if not entries:
        frame_data = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        spinner = frame_data[0] if isinstance(frame_data, tuple) else frame_data
        return (
            f"{spinner} [#8b949e]Waiting for output...[/]\n"
            "[#484f58]Forge will stream agent, review, and gate logs here.[/]"
        )

    parts: list[str] = []
    current_section: str | None = None
    review_count = 0

    global _IN_CODE_BLOCK
    _IN_CODE_BLOCK = False  # Reset at start of full render

    for source_type, line in entries:
        # Gate lines merge into review section
        effective = "review" if source_type == "gate" else source_type

        if effective != current_section:
            current_section = effective
            _IN_CODE_BLOCK = False  # Reset code block state on section change
            header_color = _SECTION_HEADER_COLORS.get(effective, "#8b949e")
            if effective == "review":
                review_count += 1
                label = f"REVIEW {review_count}"
            else:
                label = "AGENT STREAM"
            # Full-width separator with bold label for clear visual breaks
            bar = "─" * max(1, 64 - len(label))
            header = f"[{header_color} bold]───── {label} {bar}[/]"
            if parts:
                parts.append("")  # blank line before new section
            parts.append(header)

        parts.extend(_render_unified_chunk(source_type, line))

    if streaming:
        parts.append(_render_forging_shimmer(typing_frame))

    return "\n".join(parts)


def format_unified_incremental(
    source_type: str,
    line: str,
    current_section: str | None,
    review_count: int,
    is_first: bool,
) -> tuple[str, str, int]:
    """Format a SINGLE unified log entry incrementally.

    Returns (rendered_text, new_current_section, new_review_count).
    """
    parts: list[str] = []
    effective = "review" if source_type == "gate" else source_type

    if effective != current_section:
        current_section = effective
        global _IN_CODE_BLOCK
        _IN_CODE_BLOCK = False  # Reset code block state on section change
        header_color = _SECTION_HEADER_COLORS.get(effective, "#8b949e")
        if effective == "review":
            review_count += 1
            label = f"REVIEW {review_count}"
        else:
            label = "AGENT STREAM"
        bar = "─" * max(1, 64 - len(label))
        header = f"[{header_color} bold]───── {label} {bar}[/]"
        if not is_first:
            parts.append("")
        parts.append(header)

    parts.extend(_render_unified_chunk(source_type, line))

    return "\n".join(parts), current_section, review_count


class AgentOutput(Widget):
    """Scrollable agent output with fixed header and auto-scrolling body."""

    can_focus = True

    DEFAULT_CSS = """
    AgentOutput {
        layout: vertical;
        background: #0d1117;
    }
    #agent-header {
        height: 3;
        max-height: 3;
        padding: 1 2 0 2;
        background: #11161d;
        border-bottom: tall #263041;
    }
    #agent-separator {
        height: 1;
        padding: 0 2;
        background: #11161d;
    }
    #agent-scroll {
        height: 1fr;
        padding: 1 2;
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
        # Incremental rendering state
        self._rendered_parts: list[str] = []
        self._rendered_section: str | None = None
        self._rendered_review_count: int = 0
        # Fade-in animation state
        self._fade_step: int = len(_FADE_STEPS)  # Start at max (no fade active)
        self._fade_timer = None
        # Scroll debounce
        self._scroll_pending: bool = False
        # Content change detection for _tick_typing
        self._last_content_hash: int = 0

    def compose(self) -> ComposeResult:
        yield Static(format_header(None, None, None), id="agent-header")
        yield Static("[#263041]" + "━" * 72 + "[/]", id="agent-separator")
        with VerticalScroll(id="agent-scroll"):
            yield Static("", id="agent-content")

    def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(0.2, self._tick_spinner)

    def on_unmount(self) -> None:
        if hasattr(self, "_spinner_timer") and self._spinner_timer:
            self._spinner_timer.stop()
        if self._typing_timer is not None:
            self._typing_timer.stop()
            self._typing_timer = None
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None

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
        if self._rendered_parts:
            # Always update for shimmer animation since every frame changes colors
            self._update_content()
        else:
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

    def _tick_fade(self) -> None:
        """Step the fade-in animation for the last appended line."""
        self._fade_step += 1
        if self._fade_step >= len(_FADE_STEPS):
            if self._fade_timer is not None:
                self._fade_timer.stop()
                self._fade_timer = None
            self._fade_step = len(_FADE_STEPS)
        self._update_content()

    def _update_content(self) -> None:
        """Re-render the content panel, applying fade to the last line if active."""
        try:
            content = self.query_one("#agent-content", Static)
            if self._rendered_parts:
                if self._fade_step < len(_FADE_STEPS) and len(self._rendered_parts) > 0:
                    fade_color = _FADE_STEPS[self._fade_step]
                    parts = list(self._rendered_parts)
                    parts[-1] = f"[{fade_color}]{parts[-1]}[/]"
                    full = "\n".join(parts)
                else:
                    full = "\n".join(self._rendered_parts)
                if self._streaming:
                    full += f"\n{_render_forging_shimmer(self._typing_frame)}"
                content.update(full)
                if self._is_near_bottom():
                    self._request_scroll()
            elif self._unified_entries:
                content.update(format_unified_output(self._unified_entries, self._spinner_frame))
            else:
                content.update(format_output(self._lines, self._spinner_frame))
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
                self._typing_timer = self.set_interval(0.12, self._tick_typing)
            except Exception:
                pass  # Not yet composed
        else:
            if self._typing_timer is not None:
                self._typing_timer.stop()
                self._typing_timer = None
            self._typing_frame = 0

        # Refresh the content widget to show/hide the indicator
        if self._rendered_parts:
            self._update_content()
        else:
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
                self._request_scroll()
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
            self.query_one("#agent-header", Static).update(format_header(task_id, title, state))
            self.query_one("#agent-content", Static).update(
                format_output(lines, self._spinner_frame)
            )
            if lines and self._is_near_bottom():
                self._request_scroll()
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
            self.query_one("#agent-header", Static).update(format_header(task_id, title, state))
        except Exception:
            pass

    def append_unified(self, source_type: str, line: str) -> None:
        """Append a single unified log entry using incremental rendering."""
        self._unified_entries.append((source_type, line))
        is_first = len(self._rendered_parts) == 0

        text, self._rendered_section, self._rendered_review_count = format_unified_incremental(
            source_type,
            line,
            current_section=self._rendered_section,
            review_count=self._rendered_review_count,
            is_first=is_first,
        )
        self._rendered_parts.append(text)

        # Start fade-in for the last line (reuse timer to avoid churn)
        self._fade_step = 0
        if self._fade_timer is None:
            try:
                self._fade_timer = self.set_interval(_FADE_INTERVAL, self._tick_fade)
            except Exception:
                self._fade_step = len(_FADE_STEPS)  # Skip fade if not composed

        self._update_content()

    def sync_streaming(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
        entries: list[tuple[str, str]],
    ) -> None:
        """Sync unified entries during active streaming WITHOUT toggling streaming off/on.

        Unlike update_unified(), this preserves the streaming indicator and avoids
        the double-render caused by set_streaming(False) then set_streaming(True).
        Use this when _refresh_all() is called while the task is actively streaming.

        IMPORTANT: Rebuilds _rendered_parts from entries so subsequent append_unified
        calls correctly append to existing content instead of starting from empty.
        """
        self._task_id = task_id
        self._title = title
        self._state = state
        self._unified_entries = list(entries)
        self._lines = []
        # Reset fade animation
        self._fade_step = len(_FADE_STEPS)
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None
        # Rebuild incremental buffer from entries so append_unified doesn't
        # start from empty and lose all previous content
        self._rendered_parts = []
        self._rendered_section = None
        self._rendered_review_count = 0
        for i, (src, line) in enumerate(entries):
            text, self._rendered_section, self._rendered_review_count = format_unified_incremental(
                src,
                line,
                current_section=self._rendered_section,
                review_count=self._rendered_review_count,
                is_first=(i == 0),
            )
            self._rendered_parts.append(text)
        try:
            self.query_one("#agent-header", Static).update(format_header(task_id, title, state))
            full = "\n".join(self._rendered_parts)
            if self._streaming:
                full += f"\n{_render_forging_shimmer(self._typing_frame)}"
            self.query_one("#agent-content", Static).update(full)
        except Exception:
            pass
        # Ensure streaming stays on without toggling
        if not self._streaming:
            self.set_streaming(True)

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
        # Reset incremental rendering state
        self._rendered_parts = []
        self._rendered_section = None
        self._rendered_review_count = 0
        # Reset fade animation
        self._fade_step = len(_FADE_STEPS)
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None
        self.set_streaming(False)
        try:
            self.query_one("#agent-header", Static).update(format_header(task_id, title, state))
            self.query_one("#agent-content", Static).update(
                format_unified_output(entries, self._spinner_frame)
            )
            if entries and self._is_near_bottom():
                self._request_scroll()
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
        elif self._rendered_parts:
            base = "\n".join(self._rendered_parts)
            if self._streaming:
                base += f"\n{_render_forging_shimmer(self._typing_frame)}"
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

    def _request_scroll(self) -> None:
        """Request a scroll-to-end, debounced to avoid stacking."""
        if self._scroll_pending:
            return
        self._scroll_pending = True
        self.call_after_refresh(self._do_scroll)

    def _do_scroll(self) -> None:
        """Execute the debounced scroll."""
        self._scroll_pending = False
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        try:
            self.query_one("#agent-scroll", VerticalScroll).scroll_end(animate=False)
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
            self._request_scroll()
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
