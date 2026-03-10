"""Home screen — logo, prompt input, recent pipelines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Static
from textual.containers import Vertical, Center
from textual.message import Message

from forge.tui.widgets.logo import ForgeLogo


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

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="home-container"):
                yield ForgeLogo()
                yield Input(placeholder="What should I build?", id="prompt-input")
                yield Static("Recent pipelines", id="recent-label")
                yield Static("[#8b949e]No recent pipelines[/]", id="recent-list")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if task:
            self.post_message(self.TaskSubmitted(task))
