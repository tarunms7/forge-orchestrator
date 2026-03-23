"""Suggestion chip selector for agent question suggestions.

Each suggestion is rendered on its own line for readability.
User can press 1-9 to select, or Tab to cycle and Enter to confirm.
"""

from __future__ import annotations

from textual.message import Message
from textual.widget import Widget


def format_chips(suggestions: list[str], selected: int = -1) -> str:
    """Format suggestions as numbered lines, one per row."""
    if not suggestions:
        return ""
    lines = []
    for i, s in enumerate(suggestions):
        num = f"{i + 1}"
        if i == selected:
            lines.append(f"  [bold #58a6ff on #1c3a5f] {num}. {s} [/]")
        else:
            lines.append(f"  [#58a6ff]{num}.[/] [#c9d1d9]{s}[/]")
    return "\n".join(lines)


class SuggestionChips(Widget):
    class Selected(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    DEFAULT_CSS = "SuggestionChips { height: auto; max-height: 8; margin: 0 1; }"

    def __init__(self, suggestions: list[str] | None = None) -> None:
        super().__init__()
        self._suggestions = suggestions or []
        self._selected = -1

    def update_suggestions(self, suggestions: list[str]) -> None:
        self._suggestions = suggestions
        self._selected = -1
        self.refresh()

    def select_next(self) -> None:
        if self._suggestions:
            self._selected = (self._selected + 1) % len(self._suggestions)
            self.refresh()

    def select_prev(self) -> None:
        if self._suggestions:
            self._selected = (self._selected - 1) % len(self._suggestions)
            self.refresh()

    def confirm(self) -> None:
        if 0 <= self._selected < len(self._suggestions):
            self.post_message(self.Selected(self._suggestions[self._selected]))

    def select_by_number(self, n: int) -> None:
        idx = n - 1
        if 0 <= idx < len(self._suggestions):
            self._selected = idx
            self.post_message(self.Selected(self._suggestions[idx]))

    def render(self) -> str:
        return format_chips(self._suggestions, self._selected)
