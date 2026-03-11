"""Task list widget — left pane of the split-pane layout."""

from __future__ import annotations

from textual.widget import Widget
from textual.message import Message

STATE_ICONS: dict[str, str] = {
    "todo": "○",
    "in_progress": "●",
    "in_review": "◉",
    "awaiting_approval": "⊙",
    "awaiting_input": "◆",
    "merging": "◈",
    "done": "✔",
    "cancelled": "✘",
    "error": "✖",
}

STATE_COLORS: dict[str, str] = {
    "todo": "#8b949e",
    "in_progress": "#f0883e",
    "in_review": "#a371f7",
    "awaiting_approval": "#d29922",
    "awaiting_input": "#f0883e",
    "merging": "#79c0ff",
    "done": "#3fb950",
    "cancelled": "#8b949e",
    "error": "#f85149",
}


def format_task_line(task: dict, *, selected: bool) -> str:
    state = task.get("state", "todo")
    icon = STATE_ICONS.get(state, "?")
    color = STATE_COLORS.get(state, "#8b949e")
    title = task.get("title", "Untitled")
    if selected:
        return f"[bold on #1f2937] [{color}]{icon} [#c9d1d9]{title} [/]"
    else:
        return f" [{color}]{icon}[/] [#c9d1d9]{title}[/]"


class TaskList(Widget):
    """Scrollable task list with keyboard navigation."""

    DEFAULT_CSS = """
    TaskList {
        width: 1fr;
        min-width: 25;
        max-width: 40;
        padding: 0 1;
    }
    """

    class Selected(Message):
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[dict] = []
        self._selected_index: int = 0
        self._phase: str = ""

    def update_tasks(self, tasks: list[dict], selected_id: str | None = None, *, phase: str = "") -> None:
        self._tasks = tasks
        self._phase = phase
        if selected_id:
            for i, t in enumerate(tasks):
                if t["id"] == selected_id:
                    self._selected_index = i
                    break
        self._selected_index = min(self._selected_index, max(0, len(tasks) - 1))
        self.refresh()

    @property
    def selected_task(self) -> dict | None:
        if 0 <= self._selected_index < len(self._tasks):
            return self._tasks[self._selected_index]
        return None

    def render(self) -> str:
        if not self._tasks:
            if self._phase == "planning":
                return "[#a371f7]⚙ Planning...[/]\n\n[#8b949e]Analyzing codebase and\ndecomposing into tasks[/]"
            return "[#8b949e]No tasks yet[/]"
        lines = []
        for i, task in enumerate(self._tasks):
            lines.append(format_task_line(task, selected=(i == self._selected_index)))
        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        if self._selected_index < len(self._tasks) - 1:
            self._selected_index += 1
            self.refresh()
            if task := self.selected_task:
                self.post_message(self.Selected(task["id"]))

    def action_cursor_up(self) -> None:
        if self._selected_index > 0:
            self._selected_index -= 1
            self.refresh()
            if task := self.selected_task:
                self.post_message(self.Selected(task["id"]))
