"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import VerticalScroll

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    if not task_id:
        return "[#8b949e]No task selected[/]"
    if task_id == "planner":
        return "[bold #a371f7]⚙ Planner[/] [#8b949e]exploring codebase & building task graph...[/]"
    state_label = f" [{state}]" if state else ""
    return f"[bold #58a6ff]{task_id}[/]: {title or 'Untitled'} [#8b949e]{state_label}[/]"


def format_output(lines: list[str], spinner_frame: int = 0) -> str:
    if not lines:
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        return f"[#58a6ff]{frame}[/] [#8b949e]Waiting for output...[/]"
    return "\n".join(lines)


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

    def update_output(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
        lines: list[str],
    ) -> None:
        self._task_id = task_id
        self._title = title
        self._state = state
        self._lines = lines

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
