"""Branch selector dropdown widgets for the TUI HomeScreen.

BranchSelector — dropdown for picking a git branch (base branch).
BranchInput    — hybrid: free-text input + branch dropdown (pipeline branch).
"""

from __future__ import annotations

import logging

from rich.text import Text
from textual.binding import Binding
from textual.events import Key
from textual.message import Message
from textual.widget import Widget

logger = logging.getLogger("forge.tui.widgets.branch_selector")

# ── Constants ────────────────────────────────────────────────────────

_MAX_VISIBLE = 8
_MAX_BRANCH_DISPLAY = 50
_MARKER_CURRENT = "●"
_MARKER_CURSOR = "▸"
_MARKER_EMPTY = " "
_CURSOR_IN_TEXT = -1  # Sentinel for "cursor is in the text input" in BranchInput


def _truncate(name: str, max_len: int = _MAX_BRANCH_DISPLAY) -> str:
    """Truncate a branch name with ellipsis if too long."""
    return name[: max_len - 1] + "…" if len(name) > max_len else name


# ── BranchSelector ──────────────────────────────────────────────────


class BranchSelector(Widget, can_focus=True):
    """Dropdown selector for git branches.

    Expands on focus showing a filterable list of branches. Supports
    keyboard navigation (arrows/j/k when not filtering), type-to-filter,
    and ``r`` to fetch remote branches (when filter is empty).

    Usage::

        selector = BranchSelector(id="base-branch")
        await selector.load_branches("/path/to/repo")
        # Later: selector.selected_value → "main"
    """

    DEFAULT_CSS = """
    BranchSelector {
        width: 1fr;
        height: auto;
        min-height: 3;
        border: tall #30363d;
        background: #161b22;
        color: #e6edf3;
        padding: 0 1;
    }
    BranchSelector:focus {
        border: tall #58a6ff;
    }
    """

    BINDINGS = [
        Binding("enter", "select_branch", "Select", show=False, priority=True),
        Binding("escape", "collapse", "Close", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("backspace", "delete_filter_char", "Backspace", show=False, priority=True),
    ]

    class BranchSelected(Message):
        """Posted when a branch is selected."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        default: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._branches: list[str] = []
        self._current_branch: str = ""
        self._default: str = default
        self._selected: str = default or "main"
        self._cursor: int = 0
        self._filter: str = ""
        self._expanded: bool = False
        self._loading: bool = False
        self._repo_path: str = ""

    # ── Public API ───────────────────────────────────────────────────

    @property
    def selected_value(self) -> str:
        """The currently selected branch name."""
        return self._selected

    async def load_branches(self, repo_path: str) -> None:
        """Load branches from a git repo. Call after mounting."""
        from forge.core.daemon_helpers import list_local_branches

        self._repo_path = repo_path
        self._loading = True
        self.refresh()

        try:
            branches, current = await list_local_branches(
                repo_path, include_remote=True, return_current=True
            )
        except Exception:
            branches = ["main"]
            current = "main"

        self._branches = branches
        self._current_branch = current
        self._loading = False

        # Default selection: config default → current branch → first branch
        if self._default and self._default in branches:
            self._selected = self._default
        elif current in branches:
            self._selected = current
        elif branches:
            self._selected = branches[0]

        self._cursor = 0
        self.refresh()

    # ── Filtering ────────────────────────────────────────────────────

    def _filtered_branches(self) -> list[str]:
        """Return branches matching the current filter."""
        if not self._filter:
            return self._branches
        query = self._filter.lower()
        return [b for b in self._branches if query in b.lower()]

    # ── Rendering ────────────────────────────────────────────────────

    def render(self) -> Text:
        """Render the selector — collapsed or expanded."""
        text = Text()

        if self._loading:
            text.append("  Loading branches...", style="#8b949e italic")
            return text

        if not self._expanded:
            # Collapsed: show selected value with dropdown indicator
            display = _truncate(self._selected or "main")
            if self._selected == self._current_branch:
                text.append(f"  {_MARKER_CURRENT} ", style="#3fb950")
            else:
                text.append("    ")
            text.append(display, style="#e6edf3 bold")
            text.append("  ▾", style="#8b949e")
            return text

        # Expanded: filter line + branch list
        if self._filter:
            text.append(f"  ❯ {self._filter}", style="#58a6ff")
            text.append("│\n", style="#30363d")
        else:
            text.append("  ❯ ", style="#58a6ff")
            text.append("type to filter...\n", style="#8b949e italic")

        filtered = self._filtered_branches()
        if not filtered:
            text.append("  [no matching branches]", style="#8b949e italic")
            return text

        # Clamp cursor to valid range (can go stale after filter change or branch reload)
        if self._cursor >= len(filtered):
            self._cursor = max(0, len(filtered) - 1)

        # Determine visible window (scroll around cursor)
        start = max(0, self._cursor - _MAX_VISIBLE + 1)
        end = min(len(filtered), start + _MAX_VISIBLE)
        if end - start < _MAX_VISIBLE and start > 0:
            start = max(0, end - _MAX_VISIBLE)

        for i in range(start, end):
            branch = filtered[i]
            is_cursor = i == self._cursor
            is_current = branch == self._current_branch
            is_remote = branch.startswith("origin/")

            # Cursor marker
            if is_cursor:
                text.append(f"  {_MARKER_CURSOR} ", style="#58a6ff bold")
            elif is_current:
                text.append(f"  {_MARKER_CURRENT} ", style="#3fb950")
            else:
                text.append(f"  {_MARKER_EMPTY} ")

            # Branch name (truncated)
            display = _truncate(branch)
            if is_cursor:
                text.append(display, style="#e6edf3 bold")
            elif is_remote:
                text.append(display, style="#8b949e")
            else:
                text.append(display, style="#e6edf3")

            # Tags
            if is_current and not is_cursor:
                text.append(" (current)", style="#3fb950 dim")
            if branch == self._default and branch != self._current_branch:
                text.append(" (default)", style="#8b949e dim")
            if is_remote:
                text.append(" (remote)", style="#6e7681 dim")

            if i < end - 1:
                text.append("\n")

        # Scroll indicators
        if start > 0:
            text = Text("  ↑ more\n") + text
        if end < len(filtered):
            text.append("\n  ↓ more", style="#8b949e")

        # Hint at bottom
        text.append("\n  ", style="")
        text.append("r", style="#58a6ff")
        text.append(": fetch remotes  ", style="#6e7681")
        text.append("↑↓", style="#58a6ff")
        text.append(": navigate", style="#6e7681")

        return text

    # ── Focus handlers ───────────────────────────────────────────────

    def on_focus(self) -> None:
        self._expanded = True
        self._filter = ""
        # Position cursor on the selected branch
        filtered = self._filtered_branches()
        if self._selected in filtered:
            self._cursor = filtered.index(self._selected)
        else:
            self._cursor = 0
        self.refresh()

    def on_blur(self) -> None:
        self._expanded = False
        self._filter = ""
        self.refresh()

    # ── Key handlers ─────────────────────────────────────────────────

    def on_key(self, event: Key) -> None:
        """Handle character input for filtering.

        When filter is empty, j/k/r act as shortcuts (nav/refresh).
        When filter has content, ALL printable chars (including j/k/r)
        are appended to the filter — so you can search for "jira" or "release".
        """
        if not self._expanded:
            return

        # Always let these pass to bindings
        if event.key in ("up", "down", "enter", "escape", "backspace", "tab", "shift+tab"):
            return

        char = event.character
        if not char or not char.isprintable() or len(char) != 1:
            return

        # j/k/r are shortcuts ONLY when filter is empty
        if not self._filter and char in ("j", "k", "r"):
            if char == "j":
                self.action_cursor_down()
            elif char == "k":
                self.action_cursor_up()
            elif char == "r":
                self._start_refresh()
            event.prevent_default()
            event.stop()
            return

        # All other chars (and j/k/r when filter is active) → append to filter
        self._filter += char
        self._cursor = 0
        event.prevent_default()
        event.stop()
        self.refresh()

    def action_cursor_down(self) -> None:
        if not self._expanded:
            return
        filtered = self._filtered_branches()
        if self._cursor < len(filtered) - 1:
            self._cursor += 1
            self.refresh()

    def action_cursor_up(self) -> None:
        if not self._expanded:
            return
        if self._cursor > 0:
            self._cursor -= 1
            self.refresh()

    def action_select_branch(self) -> None:
        if not self._expanded:
            self._expanded = True
            self.refresh()
            return
        filtered = self._filtered_branches()
        if 0 <= self._cursor < len(filtered):
            branch = filtered[self._cursor]
            # Strip origin/ prefix for remote branches
            if branch.startswith("origin/"):
                branch = branch[len("origin/") :]
            self._selected = branch
            self._expanded = False
            self._filter = ""
            self.refresh()
            self.post_message(self.BranchSelected(branch))

    def action_collapse(self) -> None:
        self._expanded = False
        self._filter = ""
        self.refresh()

    def action_delete_filter_char(self) -> None:
        if self._filter:
            self._filter = self._filter[:-1]
            self._cursor = 0
            self.refresh()

    # ── Remote fetch ─────────────────────────────────────────────────

    def _start_refresh(self) -> None:
        """Start fetching remote branches in the background."""
        if not self._repo_path:
            return
        self.run_worker(self._do_refresh, exclusive=True)

    async def _do_refresh(self) -> None:
        from forge.core.daemon_helpers import fetch_remote_branches

        self._loading = True
        self.refresh()
        try:
            await fetch_remote_branches(self._repo_path)
            await self.load_branches(self._repo_path)
        except Exception:
            logger.debug("Failed to fetch remote branches", exc_info=True)
            self._loading = False
        # Only re-expand if we still have focus (user didn't leave)
        self._expanded = self.has_focus
        self.refresh()


# ── BranchInput ─────────────────────────────────────────────────────


class BranchInput(Widget, can_focus=True):
    """Hybrid input: free-text entry + branch dropdown.

    First item in the list is always "Auto-generate from task" (value = "").
    User can type a custom branch name OR pick from existing branches.

    Usage::

        inp = BranchInput(id="branch-name")
        await inp.load_branches("/path/to/repo")
        # Later: inp.value → "" (auto) or "feat/my-branch"
    """

    DEFAULT_CSS = """
    BranchInput {
        width: 1fr;
        height: auto;
        min-height: 3;
        border: tall #30363d;
        background: #161b22;
        color: #e6edf3;
        padding: 0 1;
    }
    BranchInput:focus {
        border: tall #58a6ff;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Confirm", show=False, priority=True),
        Binding("escape", "collapse", "Close", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("backspace", "delete_char", "Backspace", show=False, priority=True),
    ]

    class BranchChosen(Message):
        """Posted when user confirms a branch (typed or selected)."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._branches: list[str] = []
        self._text: str = ""
        self._cursor: int = _CURSOR_IN_TEXT
        self._expanded: bool = False

    # ── Public API ───────────────────────────────────────────────────

    @property
    def value(self) -> str:
        """The current value: typed text, selected branch, or empty for auto."""
        if self._cursor >= 0:
            filtered = self._filtered_with_auto()
            if 0 <= self._cursor < len(filtered):
                return filtered[self._cursor][1]  # (display, value)
        return self._text.strip()

    async def load_branches(self, repo_path: str) -> None:
        """Load branches for the dropdown portion."""
        from forge.core.daemon_helpers import list_local_branches

        try:
            branches, _ = await list_local_branches(repo_path, return_current=True)
            self._branches = branches
        except Exception:
            self._branches = []
        self.refresh()

    # ── Filtering ────────────────────────────────────────────────────

    def _filtered_with_auto(self) -> list[tuple[str, str]]:
        """Return (display_text, value) pairs. First is always auto-generate."""
        items: list[tuple[str, str]] = [("✦ Auto-generate from task", "")]
        query = self._text.lower().strip()
        for b in self._branches:
            if not query or query in b.lower():
                items.append((_truncate(b), b))
        return items

    # ── Rendering ────────────────────────────────────────────────────

    def render(self) -> Text:
        text = Text()

        if not self._expanded:
            # Collapsed: show current value or placeholder
            if self._text:
                text.append(f"  {_truncate(self._text)}", style="#e6edf3")
            else:
                text.append("  Auto-generated if empty", style="#8b949e italic")
            text.append("  ▾", style="#8b949e")
            return text

        # Expanded: input line + list
        text.append("  ❯ ", style="#58a6ff")
        if self._text:
            text.append(self._text, style="#e6edf3")
        else:
            text.append("type or pick below...", style="#8b949e italic")
        text.append("│\n", style="#30363d")

        items = self._filtered_with_auto()
        # Clamp cursor to valid range
        if self._cursor >= len(items):
            self._cursor = max(_CURSOR_IN_TEXT, len(items) - 1)
        visible = items[: _MAX_VISIBLE + 1]  # +1 for auto-generate
        for i, (display, _val) in enumerate(visible):
            is_cursor = i == self._cursor
            is_auto = i == 0

            if is_cursor:
                text.append(f"  {_MARKER_CURSOR} ", style="#58a6ff bold")
            else:
                text.append(f"  {_MARKER_EMPTY} ")

            if is_auto:
                style = "#a371f7 bold" if is_cursor else "#a371f7"
            elif is_cursor:
                style = "#e6edf3 bold"
            else:
                style = "#e6edf3"
            text.append(display, style=style)

            if i < len(visible) - 1:
                text.append("\n")

        if len(items) > len(visible):
            text.append("\n  ↓ more", style="#8b949e")

        return text

    # ── Focus handlers ───────────────────────────────────────────────

    def on_focus(self) -> None:
        self._expanded = True
        self._cursor = _CURSOR_IN_TEXT
        self.refresh()

    def on_blur(self) -> None:
        self._expanded = False
        self.refresh()

    # ── Key handlers ─────────────────────────────────────────────────

    def on_key(self, event: Key) -> None:
        if not self._expanded:
            return
        if event.key in ("up", "down", "enter", "escape", "backspace", "tab", "shift+tab"):
            return
        if event.character and event.character.isprintable() and len(event.character) == 1:
            self._text += event.character
            self._cursor = _CURSOR_IN_TEXT
            event.prevent_default()
            event.stop()
            self.refresh()

    def action_cursor_down(self) -> None:
        if not self._expanded:
            return
        items = self._filtered_with_auto()
        if self._cursor < len(items) - 1:
            self._cursor += 1
            self.refresh()

    def action_cursor_up(self) -> None:
        if not self._expanded:
            return
        if self._cursor > _CURSOR_IN_TEXT:
            self._cursor -= 1
            self.refresh()

    def action_confirm(self) -> None:
        if not self._expanded:
            self._expanded = True
            self.refresh()
            return
        val = self.value
        self._expanded = False
        self.refresh()
        self.post_message(self.BranchChosen(val))

    def action_collapse(self) -> None:
        self._expanded = False
        self.refresh()

    def action_delete_char(self) -> None:
        if self._text:
            self._text = self._text[:-1]
            self._cursor = _CURSOR_IN_TEXT
            self.refresh()
