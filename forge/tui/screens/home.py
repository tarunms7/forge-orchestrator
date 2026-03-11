"""Home screen — logo, prompt input, recent pipelines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import Input, Static, TextArea
from textual.containers import Vertical, Center
from textual.events import Key
from textual.message import Message

from forge.tui.widgets.logo import ForgeLogo


class PromptTextArea(TextArea):
    """TextArea that emits Submitted on Ctrl+Enter instead of inserting a newline."""

    class Submitted(Message):
        """Fired when user presses Ctrl+Enter."""
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def _on_key(self, event: Key) -> None:
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                self.post_message(self.Submitted(text))
            return
        super()._on_key(event)


_PIPELINE_STATUS_ICONS = {
    "complete": ("\u2714", "#3fb950"),
    "executing": ("\u25cf", "#f0883e"),
    "planned": ("\u25c9", "#a371f7"),
    "planning": ("\u25cc", "#58a6ff"),
    "error": ("\u2716", "#f85149"),
}


def format_recent_pipelines(pipelines: list[dict]) -> str:
    if not pipelines:
        return "[#8b949e]No recent pipelines[/]"
    lines = []
    for p in pipelines:
        status = p.get("status", "unknown")
        icon, color = _PIPELINE_STATUS_ICONS.get(status, ("?", "#8b949e"))
        desc = p.get("description", "Untitled")[:50]
        cost = p.get("cost", 0.0)
        date = p.get("created_at", "")[:10]
        lines.append(f"  [{color}]{icon}[/] {desc}  [#8b949e]{date} \u00b7 ${cost:.2f}[/]")
    return "\n".join(lines)


class HomeScreen(Screen):
    """Landing screen with logo and task input."""

    DEFAULT_CSS = """
    HomeScreen {
        align: center middle;
    }
    #home-container {
        width: 80;
        height: auto;
        max-height: 100%;
    }
    #prompt-input {
        margin: 1 2;
        height: 6;
        border: tall #30363d;
    }
    #prompt-input:focus {
        border: tall #58a6ff;
    }
    #recent-label {
        margin: 1 2 0 2;
        color: #8b949e;
    }
    #recent-list {
        margin: 0 2;
        height: auto;
        max-height: 10;
        color: #8b949e;
    }
    """

    BINDINGS = [
        ("escape", "app.quit", "Quit"),
    ]

    class TaskSubmitted(Message):
        def __init__(self, task: str) -> None:
            self.task = task
            super().__init__()

    def __init__(self, recent_pipelines: list[dict] | None = None) -> None:
        super().__init__()
        self._recent_pipelines = recent_pipelines or []

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="home-container"):
                yield ForgeLogo()
                yield PromptTextArea(id="prompt-input")
                yield Static("[#8b949e]Ctrl+Enter to submit[/]", id="submit-hint")
                yield Static("Recent pipelines", id="recent-label")
                yield Static(
                    format_recent_pipelines(self._recent_pipelines),
                    id="recent-list",
                )

    def on_prompt_text_area_submitted(self, event: PromptTextArea.Submitted) -> None:
        """Ctrl+Enter: submit the task prompt."""
        if event.text:
            self.post_message(self.TaskSubmitted(event.text))
