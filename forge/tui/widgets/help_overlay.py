"""Contextual help overlay — per-screen keybinding docs with categories."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

logger = logging.getLogger("forge.tui.widgets.help_overlay")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class HelpEntry:
    """A single help entry describing a keybinding or action."""

    key: str  # Keyboard shortcut string (e.g. 'Ctrl+P', 'j/k', 'Esc')
    action: str  # Short action name (e.g. 'Command Palette', 'Navigate tasks')
    description: str  # Longer description of what the binding does
    category: str  # One of: 'Navigation', 'Actions', 'Views', 'Tools'


# ---------------------------------------------------------------------------
# Per-screen help data
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "Navigation": "#58a6ff",
    "Actions": "#f0883e",
    "Views": "#3fb950",
    "Tools": "#a371f7",
}

GLOBAL_HELP: list[HelpEntry] = [
    HelpEntry("1", "Home", "Go to home screen", "Navigation"),
    HelpEntry("2", "Pipeline", "Go to pipeline screen", "Navigation"),
    HelpEntry("3", "Review", "Go to review screen", "Navigation"),
    HelpEntry("4", "Settings", "Go to settings screen", "Navigation"),
    HelpEntry("q", "Quit", "Quit Forge", "Navigation"),
    HelpEntry("Ctrl+P", "Command Palette", "Fuzzy search all actions", "Tools"),
    HelpEntry("?", "Help", "Toggle this help overlay", "Tools"),
]

HOME_HELP: list[HelpEntry] = [
    HelpEntry("Ctrl+S", "Submit task", "Type a task and press Ctrl+S to start", "Actions"),
    HelpEntry("Cmd+>", "Clear Input", "Clear the text input area", "Actions"),
    HelpEntry("Tab", "Switch focus", "Cycle focus between input and panels", "Navigation"),
    HelpEntry("Esc", "Quit", "Exit Forge", "Navigation"),
]

HOME_TIPS: list[str] = [
    "Type a task and press [bold #58a6ff]Ctrl+S[/] to start a pipeline",
    "Try: [italic #8b949e]Add authentication to my API[/]",
    "Try: [italic #8b949e]Refactor database layer to use async[/]",
]

PIPELINE_HELP: list[HelpEntry] = [
    HelpEntry("Cmd+>", "Clear Input", "Clear the text input area", "Actions"),
    HelpEntry("j/k", "Navigate tasks", "Move cursor up/down in task list", "Navigation"),
    HelpEntry("Tab", "Cycle agent", "Cycle through agent output panels", "Navigation"),
    HelpEntry("1-9", "Jump to task", "Jump directly to task by number", "Navigation"),
    HelpEntry("Esc", "Back", "Return to previous view", "Navigation"),
    HelpEntry("o", "View output", "Show agent output for selected task", "Views"),
    HelpEntry("d", "View diff", "Show diff for selected task", "Views"),
    HelpEntry("t", "Chat thread", "Show chat thread panel", "Views"),
    HelpEntry("r", "Review panel", "Show review output", "Views"),
    HelpEntry("c", "Copy mode", "Enter line-selection copy mode", "Views"),
    HelpEntry("g", "Toggle DAG", "Toggle the dependency graph view", "Views"),
    HelpEntry("w", "Why files?", "Show retrieval evidence for selected task", "Views"),
    HelpEntry("/", "Search", "Toggle search overlay", "Tools"),
    HelpEntry("n/N", "Search nav", "Jump to next/previous search match", "Tools"),
    HelpEntry("R", "Retry task", "Retry a failed task", "Actions"),
    HelpEntry("s", "Skip task", "Skip current task", "Actions"),
    HelpEntry("C", "Copy all", "Copy all agent output to clipboard", "Actions"),
]

REVIEW_HELP: list[HelpEntry] = [
    HelpEntry("a", "Approve", "Approve the current review item", "Actions"),
    HelpEntry("x", "Reject", "Reject the current review item", "Actions"),
    HelpEntry("e", "Edit", "Open in external editor", "Actions"),
    HelpEntry("j/k", "Navigate", "Move through review items", "Navigation"),
    HelpEntry("1-9", "Jump to item", "Jump directly to review item by number", "Navigation"),
    HelpEntry("/", "Search", "Toggle search overlay", "Tools"),
    HelpEntry("n/N", "Search nav", "Jump to next/previous search match", "Tools"),
]

SETTINGS_HELP: list[HelpEntry] = [
    HelpEntry("j/k", "Navigate", "Move through settings", "Navigation"),
    HelpEntry("Enter", "Edit setting", "Modify the selected setting value", "Actions"),
    HelpEntry("r", "Reset", "Reset setting to default value", "Actions"),
    HelpEntry("Esc", "Back", "Return to previous screen", "Navigation"),
]

# Map screen class names to their help data
SCREEN_HELP: dict[str, list[HelpEntry]] = {
    "HomeScreen": HOME_HELP,
    "PipelineScreen": PIPELINE_HELP,
    "ReviewScreen": REVIEW_HELP,
    "SettingsScreen": SETTINGS_HELP,
}

SCREEN_TIPS: dict[str, list[str]] = {
    "HomeScreen": HOME_TIPS,
}


def get_help_for_screen(screen_name: str) -> list[HelpEntry]:
    """Return combined help entries (screen-specific + global) for a screen."""
    screen_entries = SCREEN_HELP.get(screen_name, [])
    return screen_entries + GLOBAL_HELP


def get_tips_for_screen(screen_name: str) -> list[str]:
    """Return quick-start tips for a screen, if any."""
    return SCREEN_TIPS.get(screen_name, [])


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_help_entry(entry: HelpEntry) -> str:
    """Format a single help entry as Rich markup."""
    cat_color = _CATEGORY_COLORS.get(entry.category, "#8b949e")
    return (
        f"  [{cat_color}]{entry.key:<12}[/]"
        f"  [bold #c9d1d9]{entry.action:<20}[/]"
        f"  [#8b949e]{entry.description}[/]"
    )


def format_help_overlay(
    screen_name: str,
    entries: list[HelpEntry],
    tips: list[str],
    scroll_offset: int = 0,
    max_visible: int = 30,
) -> str:
    """Render the full help overlay content."""
    parts: list[str] = []

    # Header
    parts.append("[bold #58a6ff]── HELP ──[/]")
    parts.append("")

    # Tips section (if any)
    if tips:
        parts.append("[bold #f0883e]  Quick Start[/]")
        for tip in tips:
            parts.append(f"    {tip}")
        parts.append("")

    # Group entries by category
    grouped: dict[str, list[HelpEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.category, []).append(entry)

    # Collect all lines for scrolling
    content_lines: list[str] = []
    category_order = ["Navigation", "Actions", "Views", "Tools"]
    for cat in category_order:
        cat_entries = grouped.get(cat, [])
        if not cat_entries:
            continue
        cat_color = _CATEGORY_COLORS.get(cat, "#8b949e")
        content_lines.append(f"  [{cat_color}]{cat}[/]")
        for entry in cat_entries:
            content_lines.append(format_help_entry(entry))
        content_lines.append("")

    # Apply scroll window
    visible = content_lines[scroll_offset : scroll_offset + max_visible]
    parts.extend(visible)

    # Scroll indicator
    total = len(content_lines)
    if total > max_visible:
        remaining = max(0, total - scroll_offset - max_visible)
        if remaining > 0:
            parts.append(f"  [#484f58]↓ {remaining} more lines (j/k to scroll)[/]")
        if scroll_offset > 0:
            parts.append(f"  [#484f58]↑ {scroll_offset} lines above[/]")

    # Footer
    parts.append("")
    parts.append("[#484f58]  Esc: dismiss │ j/k: scroll │ Ctrl+P for command palette[/]")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class HelpOverlay(Widget):
    """Modal overlay showing contextual keybinding help per screen.

    Mount this widget and call .open(screen_name) to show, .close() to hide.
    Bindings: j/k scroll, Esc dismisses.
    """

    DEFAULT_CSS = """
    HelpOverlay {
        width: 100%;
        height: 100%;
        background: rgba(13, 17, 23, 0.95);
        content-align: center top;
        padding: 2 4;
        layer: overlay;
        display: none;
    }
    HelpOverlay.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Dismiss", show=False, priority=True),
        Binding("j", "scroll_down", "Scroll down", show=False, priority=True),
        Binding("k", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
    ]

    class Dismissed(Message):
        """Posted when the help overlay is dismissed."""

        pass

    def __init__(self, screen_name: str = "HomeScreen") -> None:
        super().__init__()
        self._screen_name = screen_name
        self._entries: list[HelpEntry] = get_help_for_screen(screen_name)
        self._tips: list[str] = get_tips_for_screen(screen_name)
        self._scroll_offset: int = 0
        self._max_visible: int = 30

    @property
    def screen_name(self) -> str:
        return self._screen_name

    @property
    def entries(self) -> list[HelpEntry]:
        return list(self._entries)

    @property
    def tips(self) -> list[str]:
        return list(self._tips)

    @property
    def scroll_offset(self) -> int:
        return self._scroll_offset

    @property
    def is_open(self) -> bool:
        return self.has_class("visible")

    def open(self, screen_name: str | None = None) -> None:
        """Show the help overlay, optionally for a specific screen."""
        if screen_name is not None:
            self._screen_name = screen_name
            self._entries = get_help_for_screen(screen_name)
            self._tips = get_tips_for_screen(screen_name)
        self._scroll_offset = 0
        self.add_class("visible")
        try:
            self.focus()
        except Exception:
            pass  # No active app in test context
        self.refresh()

    def close(self) -> None:
        """Hide the help overlay."""
        self.remove_class("visible")
        self._scroll_offset = 0

    def _total_content_lines(self) -> int:
        """Calculate total number of content lines for scroll bounds."""
        grouped: dict[str, list[HelpEntry]] = {}
        for entry in self._entries:
            grouped.setdefault(entry.category, []).append(entry)
        total = 0
        category_order = ["Navigation", "Actions", "Views", "Tools"]
        for cat in category_order:
            cat_entries = grouped.get(cat, [])
            if not cat_entries:
                continue
            total += 1  # category header
            total += len(cat_entries)
            total += 1  # blank line
        return total

    def action_scroll_down(self) -> None:
        max_offset = max(0, self._total_content_lines() - self._max_visible)
        if self._scroll_offset < max_offset:
            self._scroll_offset += 1
            self.refresh()

    def action_scroll_up(self) -> None:
        if self._scroll_offset > 0:
            self._scroll_offset -= 1
            self.refresh()

    def action_dismiss(self) -> None:
        self.close()
        self.post_message(self.Dismissed())

    def render(self) -> str:
        return format_help_overlay(
            self._screen_name,
            self._entries,
            self._tips,
            self._scroll_offset,
            self._max_visible,
        )
