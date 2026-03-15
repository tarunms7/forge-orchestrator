"""Universal shortcut bar — pinned to screen bottom, shows available keys."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


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
        background: $surface;
        padding: 0 1;
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

    def render(self) -> Text:
        if not self.shortcuts:
            return Text("")
        parts = Text()
        for i, (key, label) in enumerate(self.shortcuts):
            if i > 0:
                parts.append("  ")
            parts.append(f"[{key}]", style="bold bright_cyan")
            parts.append(f" {label}")
        return parts
