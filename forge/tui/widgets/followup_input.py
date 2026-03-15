"""Follow-up input widget for iterative refinement after pipeline completion."""

from __future__ import annotations

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Static, TextArea
from textual.containers import Vertical
from textual.message import Message

from forge.tui.widgets.suggestion_chips import SuggestionChips


DEFAULT_SUGGESTIONS = ["Add tests", "Fix linting", "Add docs", "Refactor"]


def format_context_badge(branch: str, files_changed: int) -> str:
    """Format a context badge showing what this follow-up builds on."""
    if not branch:
        return ""
    return (
        f"[#8b949e]Building on: [bold #58a6ff]{branch}[/bold #58a6ff]"
        f" ({files_changed} file{'s' if files_changed != 1 else ''} changed)[/]"
    )


def format_followup_history(history: list[str]) -> str:
    """Format previous follow-up prompts in the session."""
    if not history:
        return ""
    lines = ["[bold #8b949e]Previous follow-ups:[/]"]
    for i, prompt in enumerate(history, 1):
        # Truncate long prompts for display
        display = prompt if len(prompt) <= 80 else prompt[:77] + "..."
        lines.append(f"  [#6e7681]{i}. {display}[/]")
    return "\n".join(lines)


class FollowUpTextArea(TextArea):
    """TextArea subclass with escape-to-unfocus and clear-input bindings."""

    BINDINGS = [
        Binding("ctrl+u", "clear_input", "Clear", show=False, priority=True),
        Binding("escape", "unfocus", "Back", show=False, priority=True),
    ]

    def action_clear_input(self) -> None:
        """Clear the text area content and reset cursor."""
        self.text = ""
        self.move_cursor((0, 0))

    def action_unfocus(self) -> None:
        """Return focus to the parent screen so keybindings work again."""
        self.screen.focus()


class FollowUpInput(Widget):
    """Reusable follow-up prompt widget with multi-line input and suggestion chips."""

    class Submitted(Message):
        """Emitted when the user submits a follow-up prompt."""

        def __init__(self, prompt: str, branch: str, files_changed: int) -> None:
            self.prompt = prompt
            self.branch = branch
            self.files_changed = files_changed
            super().__init__()

    DEFAULT_CSS = """
    FollowUpInput { height: auto; padding: 1 0; }
    FollowUpInput #followup-history { height: auto; margin: 0 1; }
    FollowUpInput #followup-context { height: auto; margin: 0 1; }
    FollowUpInput #followup-label { height: 1; margin: 0 1; }
    FollowUpInput TextArea { height: 4; margin: 0 1; }
    FollowUpInput #followup-hint { height: 1; margin: 0 1; }
    FollowUpInput SuggestionChips { margin: 0 1; }
    """

    def __init__(
        self,
        branch: str = "",
        files_changed: int = 0,
        suggestions: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._branch = branch
        self._files_changed = files_changed
        self._suggestions = suggestions if suggestions is not None else list(DEFAULT_SUGGESTIONS)
        self._history: list[str] = []

    def compose(self):
        with Vertical():
            yield Static("", id="followup-history")
            yield Static(
                format_context_badge(self._branch, self._files_changed),
                id="followup-context",
            )
            yield Static(
                "[bold #58a6ff]Follow up:[/] refine, extend, or fix...",
                id="followup-label",
            )
            yield FollowUpTextArea(id="followup-text")
            yield Static(
                "[#6e7681]Ctrl+S to submit  •  f to focus[/]",
                id="followup-hint",
            )
            yield SuggestionChips(self._suggestions)

    def update_context(self, branch: str, files_changed: int) -> None:
        """Update the context badge with new branch/file info."""
        self._branch = branch
        self._files_changed = files_changed
        try:
            ctx = self.query_one("#followup-context", Static)
            ctx.update(format_context_badge(branch, files_changed))
        except Exception:
            pass

    def add_history(self, prompt: str) -> None:
        """Add a prompt to the follow-up history."""
        self._history.append(prompt)
        try:
            history_widget = self.query_one("#followup-history", Static)
            history_widget.update(format_followup_history(self._history))
        except Exception:
            pass

    def focus_input(self) -> None:
        """Focus the text area for input."""
        try:
            ta = self.query_one("#followup-text", FollowUpTextArea)
            ta.focus()
        except Exception:
            pass

    def submit(self) -> None:
        """Submit the current text area contents as a follow-up prompt."""
        try:
            ta = self.query_one("#followup-text", FollowUpTextArea)
            text = ta.text.strip()
            if text:
                self.add_history(text)
                self.post_message(
                    self.Submitted(text, self._branch, self._files_changed)
                )
                ta.clear()
        except Exception:
            pass

    def on_suggestion_chips_selected(self, event: SuggestionChips.Selected) -> None:
        """When a chip is selected, fill and submit it as the follow-up prompt."""
        self.add_history(event.text)
        self.post_message(
            self.Submitted(event.text, self._branch, self._files_changed)
        )
