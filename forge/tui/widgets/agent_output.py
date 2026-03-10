"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

from textual.widget import Widget

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    if not task_id:
        return "[#8b949e]No task selected[/]"
    state_label = f" [{state}]" if state else ""
    return f"[bold #58a6ff]{task_id}[/]: {title or 'Untitled'} [#8b949e]{state_label}[/]"


def format_output(lines: list[str], spinner_frame: int = 0) -> str:
    if not lines:
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        return f"[#58a6ff]{frame}[/] [#8b949e]Waiting for output...[/]"
    return "\n".join(lines)


class AgentOutput(Widget):
    """Scrollable agent output panel."""

    DEFAULT_CSS = """
    AgentOutput {
        width: 3fr;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._task_id: str | None = None
        self._title: str | None = None
        self._state: str | None = None
        self._lines: list[str] = []

    def on_mount(self) -> None:
        self._spinner_frame = 0
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if not self._lines:
            self._spinner_frame += 1
            self.refresh()

    def update_output(self, task_id: str | None, title: str | None, state: str | None, lines: list[str]) -> None:
        self._task_id = task_id
        self._title = title
        self._state = state
        self._lines = lines
        self.refresh()

    def render(self) -> str:
        header = format_header(self._task_id, self._title, self._state)
        body = format_output(self._lines, getattr(self, '_spinner_frame', 0))
        separator = "[#30363d]" + "─" * 50 + "[/]"
        return f"{header}\n{separator}\n{body}"
