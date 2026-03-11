"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import VerticalScroll

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_TYPING_FRAMES = ["▍", "▌", "▍", " "]


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    if not task_id:
        return "[#8b949e]No task selected[/]"
    if task_id == "planner":
        return "[bold #a371f7]⚙ Planner[/] [#8b949e]exploring codebase & building task graph...[/]"
    state_label = f" [{state}]" if state else ""
    return f"[bold #58a6ff]{task_id}[/]: {title or 'Untitled'} [#8b949e]{state_label}[/]"


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

    def compose(self) -> ComposeResult:
        yield Static(format_header(None, None, None), id="agent-header")
        yield Static("[#30363d]" + "─" * 60 + "[/]", id="agent-separator")
        with VerticalScroll(id="agent-scroll"):
            yield Static("", id="agent-content")

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._lines:
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

    def append_line(self, line: str) -> None:
        """Efficiently append a single line of streaming output.

        Appends to self._lines, updates only the #agent-content Static widget
        (NOT the header), and auto-scrolls via call_after_refresh +
        scroll_end(animate=False). Does NOT re-render the full output — only
        the incremental addition. This is the hot-path method for streaming.
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

        # Reset streaming state on full refresh
        self.set_streaming(False)

        try:
            self.query_one("#agent-header", Static).update(
                format_header(task_id, title, state)
            )
            self.query_one("#agent-content", Static).update(
                format_output(lines, self._spinner_frame)
            )
            if lines:
                self.call_after_refresh(self._scroll_to_end)
        except Exception:
            pass  # Not yet composed

    def _scroll_to_end(self) -> None:
        try:
            self.query_one("#agent-scroll", VerticalScroll).scroll_end(
                animate=False
            )
        except Exception:
            pass
