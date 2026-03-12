"""Search overlay — vim-style `/` search for agent output and diff views."""

from __future__ import annotations

import logging
import re

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

logger = logging.getLogger("forge.tui.widgets.search_overlay")

_HIGHLIGHT_OPEN = "[on #d29922]"
_HIGHLIGHT_CLOSE = "[/]"


def apply_highlights(text: str, pattern: str, use_regex: bool = False) -> tuple[str, int]:
    """Apply Rich markup highlights to matching text.

    Returns (highlighted_text, match_count). Operates on plain text,
    escaping Rich markup brackets in the pattern match.  When the text
    already contains Rich markup tags we leave them intact and only
    highlight the non-markup portions.

    Performance: uses a single compiled regex pass — O(n) in text length.
    """
    if not pattern:
        return text, 0

    try:
        if use_regex:
            rx = re.compile(pattern, re.IGNORECASE)
        else:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)
    except re.error:
        return text, 0

    # Split text into Rich markup tags and plain segments so we only
    # highlight inside the plain parts.
    tag_re = re.compile(r"(\[(?:[^\]]*)\])")
    segments = tag_re.split(text)

    count = 0
    result_parts: list[str] = []
    for seg in segments:
        if seg.startswith("[") and seg.endswith("]"):
            # Rich markup tag — pass through unchanged
            result_parts.append(seg)
        else:
            # Plain text — apply highlights
            last = 0
            parts: list[str] = []
            for m in rx.finditer(seg):
                parts.append(seg[last:m.start()])
                parts.append(_HIGHLIGHT_OPEN)
                parts.append(seg[m.start():m.end()])
                parts.append(_HIGHLIGHT_CLOSE)
                count += 1
                last = m.end()
            parts.append(seg[last:])
            result_parts.append("".join(parts))

    return "".join(result_parts), count


class SearchOverlay(Widget):
    """Vim-style search bar that docks at the bottom of the screen.

    Behaviour:
      - `/` opens the bar and focuses the input
      - Type to search — results highlight in real-time
      - n/N jump to next/previous match
      - Enter confirms (closes bar, keeps highlights)
      - Esc dismisses the bar; if highlights are active, first Esc keeps
        them, second Esc clears them
    """

    DEFAULT_CSS = """
    SearchOverlay {
        dock: bottom;
        height: auto;
        max-height: 3;
        background: #161b22;
        border-top: tall #30363d;
        padding: 0 1;
        display: none;
    }
    SearchOverlay.visible {
        display: block;
    }
    #search-row {
        height: 1;
        width: 100%;
    }
    #search-prefix {
        width: 2;
        height: 1;
        color: #f0883e;
    }
    #search-input {
        width: 1fr;
        height: 1;
        background: #0d1117;
        color: #c9d1d9;
        border: none;
    }
    #search-status {
        width: auto;
        min-width: 16;
        height: 1;
        color: #8b949e;
        content-align: right middle;
        text-align: right;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Dismiss", show=False, priority=True),
        Binding("enter", "confirm", "Confirm", show=False, priority=True),
    ]

    class SearchChanged(Message):
        """Emitted when the search pattern changes."""
        def __init__(self, pattern: str | None, use_regex: bool = False) -> None:
            self.pattern = pattern
            self.use_regex = use_regex
            super().__init__()

    class SearchNavigate(Message):
        """Emitted when user presses n/N to navigate matches."""
        def __init__(self, direction: int) -> None:
            # direction: +1 = next, -1 = previous
            self.direction = direction
            super().__init__()

    class SearchDismissed(Message):
        """Emitted when the search overlay is dismissed."""
        pass

    def __init__(self) -> None:
        super().__init__()
        self._pattern: str | None = None
        self._use_regex: bool = False
        self._match_count: int = 0
        self._current_match: int = 0  # 1-based index
        self._highlights_active: bool = False

    @property
    def pattern(self) -> str | None:
        return self._pattern

    @property
    def match_count(self) -> int:
        return self._match_count

    @property
    def current_match(self) -> int:
        return self._current_match

    @property
    def is_visible(self) -> bool:
        return self.has_class("visible")

    def compose(self) -> ComposeResult:
        with Horizontal(id="search-row"):
            yield Static("/", id="search-prefix")
            yield Input(placeholder="search...", id="search-input")
            yield Static("", id="search-status")

    def show(self) -> None:
        """Show the search overlay and focus the input."""
        self.add_class("visible")
        try:
            inp = self.query_one("#search-input", Input)
            inp.value = self._pattern or ""
            inp.focus()
        except Exception:
            pass

    def hide(self) -> None:
        """Hide the search overlay."""
        self.remove_class("visible")

    def toggle(self) -> None:
        """Toggle visibility."""
        if self.is_visible:
            self.action_dismiss()
        else:
            self.show()

    def update_match_count(self, count: int) -> None:
        """Called by the parent screen after applying highlights."""
        self._match_count = count
        if count > 0:
            self._current_match = min(self._current_match, count) or 1
        else:
            self._current_match = 0
        self._refresh_status()

    def navigate(self, direction: int) -> None:
        """Move to next (+1) or previous (-1) match."""
        if self._match_count == 0:
            return
        self._current_match += direction
        if self._current_match > self._match_count:
            self._current_match = 1  # wrap
        elif self._current_match < 1:
            self._current_match = self._match_count  # wrap
        self._refresh_status()
        self.post_message(self.SearchNavigate(direction))

    def _refresh_status(self) -> None:
        """Update the match counter display."""
        try:
            status = self.query_one("#search-status", Static)
            if self._match_count > 0:
                status.update(
                    f"[#c9d1d9]{self._current_match}/{self._match_count} matches[/]"
                )
            elif self._pattern:
                status.update("[#f85149]no matches[/]")
            else:
                status.update("")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live search as user types."""
        value = event.value.strip()
        if value:
            self._pattern = value
            self._highlights_active = True
            self._current_match = 1
        else:
            self._pattern = None
            self._highlights_active = False
            self._current_match = 0
            self._match_count = 0
        self._refresh_status()
        self.post_message(self.SearchChanged(self._pattern, self._use_regex))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Only handle dismiss when the overlay is visible or has active highlights."""
        if action == "dismiss" and not self.is_visible and not self._highlights_active:
            return False  # Let escape bubble to parent (e.g. pop_screen)
        return True

    def action_dismiss(self) -> None:
        """Esc — first press hides bar (keeps highlights), second clears highlights."""
        if self.is_visible:
            self.hide()
            if not self._highlights_active:
                self._clear_search()
        else:
            # Already hidden — clear highlights
            self._clear_search()

    def action_confirm(self) -> None:
        """Enter — close the bar, keep highlights active."""
        self.hide()

    def _clear_search(self) -> None:
        """Clear pattern and highlights."""
        self._pattern = None
        self._highlights_active = False
        self._match_count = 0
        self._current_match = 0
        self.post_message(self.SearchChanged(None, self._use_regex))
        self.post_message(self.SearchDismissed())
