"""Inline diff viewer with syntax highlighting."""

from __future__ import annotations

from textual.widget import Widget


def format_diff(diff_text: str) -> str:
    if not diff_text:
        return "[#8b949e]No diff available[/]"
    lines = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            lines.append(f"[bold #8b949e]{_escape(line)}[/]")
        elif line.startswith("@@"):
            lines.append(f"[#79c0ff]{_escape(line)}[/]")
        elif line.startswith("+"):
            lines.append(f"[#3fb950]{_escape(line)}[/]")
        elif line.startswith("-"):
            lines.append(f"[#f85149]{_escape(line)}[/]")
        else:
            lines.append(_escape(line))
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("[", "\\[")


class DiffViewer(Widget):
    """Scrollable diff viewer."""

    DEFAULT_CSS = """
    DiffViewer {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._diff_text: str = ""
        self._task_id: str | None = None
        self._task_title: str | None = None

    def update_diff(self, task_id: str, title: str, diff_text: str) -> None:
        self._task_id = task_id
        self._task_title = title
        self._diff_text = diff_text
        self.refresh()

    def render(self) -> str:
        if not self._task_id:
            return "[#8b949e]Select a task to view its diff[/]"
        header = f"[bold #58a6ff]{self._task_id}[/]: {self._task_title or ''}\n"
        separator = "[#30363d]" + "─" * 60 + "[/]\n"
        return header + separator + format_diff(self._diff_text)
