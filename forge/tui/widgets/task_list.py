"""Task list widget — left pane of the split-pane layout."""

from __future__ import annotations

from textual.message import Message
from textual.widget import Widget

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


MAX_WIDTH = 40


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def format_task_line(task: dict, *, selected: bool, multi_repo: bool = False) -> str:
    state = task.get("state", "todo")
    icon = STATE_ICONS.get(state, "?")
    color = STATE_COLORS.get(state, "#8b949e")
    title = task.get("title", "Untitled")

    # Build repo prefix for multi-repo pipelines
    repo_prefix = ""
    repo_width = 0
    repo_id = task.get("repo")
    if multi_repo and repo_id:
        repo_prefix = f"[#79c0ff]\\[{repo_id}][/] "
        repo_width = len(repo_id) + 3  # brackets + space

    # Build suffix parts
    suffix_parts: list[str] = []
    files_changed = task.get("files_changed", [])
    file_count = len(files_changed) if files_changed else 0

    if state == "error":
        suffix_parts.append("⚠")
    if file_count > 0:
        suffix_parts.append(f"[#8b949e]{file_count} files[/]")

    suffix = " ".join(suffix_parts)

    # Calculate available width for title: max_width - icon prefix (3 chars) - repo prefix - suffix
    # Rough visible length of suffix (strip markup for length calc)
    suffix_visible_len = 0
    if suffix:
        import re
        suffix_visible_len = len(re.sub(r"\[.*?\]", "", suffix)) + 1  # +1 for space before suffix

    available = MAX_WIDTH - 3 - repo_width - suffix_visible_len  # 3 = " X " icon prefix
    if available < 4:
        available = 4

    if len(title) > available:
        title = title[: available - 1] + "…"

    # Build the final line
    suffix_str = f" {suffix}" if suffix else ""
    escaped_title = _escape(title)
    if selected:
        return f"[bold on #1f2937] [{color}]{icon} {repo_prefix}[#c9d1d9]{escaped_title}{suffix_str} [/]"
    else:
        return f" [{color}]{icon}[/] {repo_prefix}[#c9d1d9]{escaped_title}{suffix_str}[/]"


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
        self._multi_repo: bool = False

    def update_tasks(self, tasks: list[dict], selected_id: str | None = None, *, phase: str = "", multi_repo: bool = False) -> None:
        self._multi_repo = multi_repo
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
            lines.append(format_task_line(task, selected=(i == self._selected_index), multi_repo=self._multi_repo))
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
