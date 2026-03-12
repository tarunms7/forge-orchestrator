"""Command palette widget — Ctrl+P fuzzy search for all TUI actions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

logger = logging.getLogger("forge.tui.widgets.command_palette")


@dataclass
class CommandPaletteAction:
    """A single action in the command palette registry."""

    name: str  # Display name (e.g. 'Toggle DAG', 'New Task')
    description: str  # Short description
    shortcut: str = ""  # Keyboard shortcut hint (e.g. 'Ctrl+P')
    category: str = "Tools"  # Navigation, Pipeline, Tools, View
    callback_name: str = ""  # Textual action method name (e.g. 'switch_home')


def get_all_actions() -> list[CommandPaletteAction]:
    """Return all registered actions for the command palette.

    Task-5 can call this to list available actions in help overlay.
    """
    return [
        # Navigation
        CommandPaletteAction(
            name="Home",
            description="Go to home screen",
            shortcut="1",
            category="Navigation",
            callback_name="switch_home",
        ),
        CommandPaletteAction(
            name="Pipeline",
            description="Go to pipeline screen",
            shortcut="2",
            category="Navigation",
            callback_name="switch_pipeline",
        ),
        CommandPaletteAction(
            name="Review",
            description="Go to review screen",
            shortcut="3",
            category="Navigation",
            callback_name="switch_review",
        ),
        CommandPaletteAction(
            name="Settings",
            description="Open settings screen",
            shortcut="4",
            category="Navigation",
            callback_name="switch_settings",
        ),
        # Pipeline
        CommandPaletteAction(
            name="New Task",
            description="Start a new pipeline task",
            shortcut="",
            category="Pipeline",
            callback_name="reset_for_new_task",
        ),
        CommandPaletteAction(
            name="Retry Task",
            description="Retry failed task",
            shortcut="",
            category="Pipeline",
            callback_name="retry_task",
        ),
        CommandPaletteAction(
            name="Skip Task",
            description="Skip current task",
            shortcut="",
            category="Pipeline",
            callback_name="skip_task",
        ),
        CommandPaletteAction(
            name="Next Question",
            description="Cycle to next pending question",
            shortcut="Tab",
            category="Pipeline",
            callback_name="cycle_questions",
        ),
        # View
        CommandPaletteAction(
            name="Toggle DAG",
            description="Toggle the dependency graph view",
            shortcut="g",
            category="View",
            callback_name="toggle_dag",
        ),
        CommandPaletteAction(
            name="View Diff",
            description="Show diff for selected task",
            shortcut="d",
            category="View",
            callback_name="view_diff",
        ),
        CommandPaletteAction(
            name="View Output",
            description="Show agent output for selected task",
            shortcut="o",
            category="View",
            callback_name="view_output",
        ),
        CommandPaletteAction(
            name="View Contracts",
            description="Show contracts panel",
            shortcut="c",
            category="View",
            callback_name="view_contracts",
        ),
        # Tools
        CommandPaletteAction(
            name="Command Palette",
            description="Open command palette",
            shortcut="Ctrl+P",
            category="Tools",
            callback_name="show_command_palette",
        ),
        CommandPaletteAction(
            name="Help",
            description="Show keybinding help",
            shortcut="?",
            category="Tools",
            callback_name="show_help",
        ),
        CommandPaletteAction(
            name="Copy Output",
            description="Copy agent output to clipboard",
            shortcut="y",
            category="Tools",
            callback_name="copy_output",
        ),
        CommandPaletteAction(
            name="Screenshot",
            description="Export screenshot to file",
            shortcut="s",
            category="Tools",
            callback_name="screenshot_export",
        ),
        CommandPaletteAction(
            name="Export Logs",
            description="Export pipeline logs",
            shortcut="",
            category="Tools",
            callback_name="export_logs",
        ),
        CommandPaletteAction(
            name="Quit",
            description="Quit Forge",
            shortcut="q",
            category="Tools",
            callback_name="quit_app",
        ),
    ]


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def fuzzy_score(query: str, text: str) -> int:
    """Score how well query matches text. Higher = better. 0 = no match.

    Scoring:
    - Exact substring match: 100 + length bonus
    - Prefix match: 80 + length bonus
    - All chars present in order (fuzzy): 50 + consecutive bonus
    - No match: 0
    """
    if not query:
        return 1  # Everything matches empty query

    q = query.lower()
    t = text.lower()

    # Exact substring
    if q in t:
        bonus = len(q) * 10
        # Extra bonus for prefix match
        if t.startswith(q):
            return 180 + bonus
        return 100 + bonus

    # Fuzzy: all chars in order
    qi = 0
    consecutive = 0
    max_consecutive = 0
    prev_idx = -2

    for ti, ch in enumerate(t):
        if qi < len(q) and ch == q[qi]:
            if ti == prev_idx + 1:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 1
            prev_idx = ti
            qi += 1

    if qi == len(q):
        return 50 + max_consecutive * 10 + len(q) * 2
    return 0


def fuzzy_match(query: str, actions: list[CommandPaletteAction]) -> list[CommandPaletteAction]:
    """Filter and rank actions by fuzzy match against name, description, and category."""
    if not query:
        return list(actions)

    scored: list[tuple[int, CommandPaletteAction]] = []
    for action in actions:
        # Score against name (primary), description, and category
        name_score = fuzzy_score(query, action.name)
        desc_score = fuzzy_score(query, action.description)
        cat_score = fuzzy_score(query, action.category)
        best = max(name_score, desc_score // 2, cat_score // 2)
        if best > 0:
            scored.append((best, action))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [action for _, action in scored]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "Navigation": "#58a6ff",
    "Pipeline": "#f0883e",
    "Tools": "#a371f7",
    "View": "#3fb950",
}


def format_result_line(action: CommandPaletteAction, selected: bool = False) -> str:
    """Format a single result line with name, description, shortcut, and category."""
    cat_color = _CATEGORY_COLORS.get(action.category, "#8b949e")
    shortcut_part = f"  [#484f58]{action.shortcut}[/]" if action.shortcut else ""

    if selected:
        return (
            f"[bold on #1f2937] [{cat_color}]●[/] "
            f"[bold #c9d1d9]{action.name}[/]"
            f"  [#8b949e]{action.description}[/]"
            f"{shortcut_part} [/]"
        )
    return (
        f"  [{cat_color}]○[/] "
        f"[#c9d1d9]{action.name}[/]"
        f"  [#8b949e]{action.description}[/]"
        f"{shortcut_part}"
    )


def format_palette(query: str, results: list[CommandPaletteAction], selected_index: int) -> str:
    """Render the full command palette content."""
    parts: list[str] = []

    # Header
    parts.append("[bold #58a6ff]── COMMAND PALETTE ──[/]")
    parts.append("")

    # Search input display
    if query:
        parts.append(f"  [bold #c9d1d9]❯[/] [#c9d1d9]{query}[/][blink #58a6ff]│[/]")
    else:
        parts.append("  [bold #c9d1d9]❯[/] [#484f58]Type to search...[/][blink #58a6ff]│[/]")
    parts.append("")

    if not results:
        parts.append("  [#8b949e]No matching actions[/]")
    else:
        # Group by category
        current_category = ""
        for i, action in enumerate(results):
            if action.category != current_category:
                current_category = action.category
                cat_color = _CATEGORY_COLORS.get(current_category, "#8b949e")
                if i > 0:
                    parts.append("")
                parts.append(f"  [{cat_color}]{current_category}[/]")
            parts.append(format_result_line(action, selected=(i == selected_index)))

    # Footer
    parts.append("")
    parts.append("[#484f58]  j/k: navigate │ Enter: execute │ Esc: dismiss[/]")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

COMMAND_PALETTE_BINDING_KEY = "ctrl+p"
COMMAND_PALETTE_BINDING_DESCRIPTION = "Command Palette"


class CommandPalette(Widget):
    """Modal overlay for fuzzy-searching and executing TUI actions.

    Mount this widget and call .open() to show, .close() to hide.
    Bindings: j/k navigate, Enter executes, Esc dismisses, any other
    key appends to the search query.
    """

    DEFAULT_CSS = """
    CommandPalette {
        width: 100%;
        height: 100%;
        background: rgba(13, 17, 23, 0.92);
        content-align: center top;
        padding: 3 0 0 0;
        layer: overlay;
        display: none;
    }
    CommandPalette.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Dismiss", show=False, priority=True),
        Binding("enter", "execute", "Execute", show=False, priority=True),
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("backspace", "delete_char", "Delete", show=False, priority=True),
    ]

    class ActionSelected(Message):
        """Posted when user selects an action to execute."""

        def __init__(self, action: CommandPaletteAction) -> None:
            self.action = action
            super().__init__()

    class Dismissed(Message):
        """Posted when the palette is dismissed."""
        pass

    def __init__(
        self,
        actions: list[CommandPaletteAction] | None = None,
    ) -> None:
        super().__init__()
        self._all_actions = actions or get_all_actions()
        self._query = ""
        self._results: list[CommandPaletteAction] = list(self._all_actions)
        self._selected_index = 0

    @property
    def query(self) -> str:
        return self._query

    @property
    def results(self) -> list[CommandPaletteAction]:
        return list(self._results)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def selected_action(self) -> CommandPaletteAction | None:
        if 0 <= self._selected_index < len(self._results):
            return self._results[self._selected_index]
        return None

    @property
    def is_open(self) -> bool:
        return self.has_class("visible")

    def open(self) -> None:
        """Show the palette and reset state."""
        self._query = ""
        self._results = list(self._all_actions)
        self._selected_index = 0
        self.add_class("visible")
        try:
            self.focus()
        except Exception:
            pass  # No active app in test context
        self.refresh()

    def close(self) -> None:
        """Hide the palette."""
        self.remove_class("visible")
        self._query = ""

    def _update_results(self) -> None:
        """Re-filter results based on current query."""
        self._results = fuzzy_match(self._query, self._all_actions)
        # Clamp selected index
        if self._results:
            self._selected_index = min(self._selected_index, len(self._results) - 1)
        else:
            self._selected_index = 0
        self.refresh()

    def set_query(self, query: str) -> None:
        """Set query and update results — useful for programmatic input."""
        self._query = query
        self._selected_index = 0
        self._update_results()

    def on_key(self, event) -> None:
        """Handle character input for the search query."""
        if not self.is_open:
            return

        # Let bindings handle special keys
        if event.key in (
            "escape", "enter", "up", "down", "j", "k", "backspace",
            "ctrl+p", "tab", "shift+tab",
        ):
            return

        # Printable character — append to query
        if event.character and event.is_printable:
            event.stop()
            event.prevent_default()
            self._query += event.character
            self._selected_index = 0
            self._update_results()

    def action_cursor_down(self) -> None:
        if self._results and self._selected_index < len(self._results) - 1:
            self._selected_index += 1
            self.refresh()

    def action_cursor_up(self) -> None:
        if self._selected_index > 0:
            self._selected_index -= 1
            self.refresh()

    def action_delete_char(self) -> None:
        if self._query:
            self._query = self._query[:-1]
            self._selected_index = 0
            self._update_results()

    def action_execute(self) -> None:
        action = self.selected_action
        if action:
            self.close()
            self.post_message(self.ActionSelected(action))

    def action_dismiss(self) -> None:
        self.close()
        self.post_message(self.Dismissed())

    def render(self) -> str:
        return format_palette(self._query, self._results, self._selected_index)
