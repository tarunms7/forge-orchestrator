"""Inline diff viewer with syntax highlighting."""

from __future__ import annotations

from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Static

from forge.tui.widgets.search_overlay import apply_highlights


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


def _escape(text: str | None) -> str:
    if text is None:
        return ""
    return text.replace("[", "\\[")


class DiffViewer(ScrollableContainer):
    """Scrollable diff viewer with vim-style navigation."""

    BINDINGS = [
        Binding("j", "scroll_down", "Scroll Down", show=False),
        Binding("k", "scroll_up", "Scroll Up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("shift+g", "scroll_end", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    DiffViewer {
        width: 100%;
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._diff_text: str = ""
        self._task_id: str | None = None
        self._task_title: str | None = None
        self._search_pattern: str | None = None
        self._content = Static("")

    def compose(self):
        yield self._content

    def update_diff(self, task_id: str, title: str, diff_text: str) -> None:
        self._task_id = task_id
        self._task_title = title
        self._diff_text = diff_text
        self._refresh_content()

    def set_search_highlights(self, pattern: str | None) -> int:
        """Apply or clear search highlights on diff content."""
        self._search_pattern = pattern
        self._refresh_content()
        if pattern:
            base = format_diff(self._diff_text)
            _, count = apply_highlights(base, pattern)
            return count
        return 0

    def _refresh_content(self) -> None:
        """Update the child Static with rendered diff content."""
        if not self._task_id:
            self._content.update("[#8b949e]Select a task to view its diff[/]")
            return
        header = f"[bold #58a6ff]{_escape(self._task_id)}[/]: {_escape(self._task_title or '')}\n"
        separator = "[#30363d]" + "─" * 60 + "[/]\n"
        diff_content = format_diff(self._diff_text)
        if self._search_pattern:
            diff_content, _ = apply_highlights(diff_content, self._search_pattern)
        self._content.update(header + separator + diff_content)
