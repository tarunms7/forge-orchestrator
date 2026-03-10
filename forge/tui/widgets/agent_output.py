"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

from textual.widget import Widget


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    if not task_id:
        return "[#8b949e]No task selected[/]"
    state_label = f" [{state}]" if state else ""
    return f"[bold #58a6ff]{task_id}[/]: {title or 'Untitled'} [#8b949e]{state_label}[/]"


def format_output(lines: list[str]) -> str:
    if not lines:
        return "[#8b949e]Waiting for output...[/]"
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

    def update_output(self, task_id: str | None, title: str | None, state: str | None, lines: list[str]) -> None:
        self._task_id = task_id
        self._title = title
        self._state = state
        self._lines = lines
        self.refresh()

    def render(self) -> str:
        header = format_header(self._task_id, self._title, self._state)
        body = format_output(self._lines)
        separator = "[#30363d]" + "─" * 50 + "[/]"
        return f"{header}\n{separator}\n{body}"
