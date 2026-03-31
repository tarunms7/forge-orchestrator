"""Universal shortcut bar — pinned to screen bottom, shows available keys."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


class ShortcutBar(Widget):
    """Persistent bottom bar showing available keyboard shortcuts.

    Usage:
        bar = ShortcutBar([("Enter", "Create PR"), ("r", "Retry")])
        bar.shortcuts = [("d", "View Diff")]  # Update dynamically
    """

    DEFAULT_CSS = """
    ShortcutBar {
        dock: bottom;
        height: 1;
        background: #11161d;
        padding: 0 1;
        border-top: tall #263041;
    }
    """

    shortcuts: reactive[list[tuple[str, str]]] = reactive(list, layout=True)

    def __init__(
        self,
        shortcuts: list[tuple[str, str]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.shortcuts = shortcuts or []

    def watch_shortcuts(self, _old: list, _new: list) -> None:
        """Trigger re-render when shortcuts change."""
        self.refresh()

    def update_shortcuts(self, shortcuts: list[tuple[str, str]]) -> None:
        """Replace internal shortcut list and re-render."""
        self.shortcuts = shortcuts

    def render(self) -> Text:
        if not self.shortcuts:
            return Text("")
        parts = Text()
        for i, (key, label) in enumerate(self.shortcuts):
            if i > 0:
                parts.append("  •  ", style="#30363d")
            parts.append(f" {key} ", style="bold #0d1117 on #79c0ff")
            parts.append(f" {label}", style="#c9d1d9")
        return parts
