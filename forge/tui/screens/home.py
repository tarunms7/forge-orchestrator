"""Home screen — logo, prompt input, recent pipelines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import Static, TextArea
from textual.containers import Vertical, Horizontal, Center
from textual.message import Message

from forge.tui.widgets.logo import ForgeLogo
from forge.tui.widgets.pipeline_list import PipelineList


class PromptTextArea(TextArea):
    """TextArea that emits Submitted on Ctrl+S instead of inserting a newline.

    Note: Terminals cannot distinguish Ctrl+Enter from Enter, so we use
    Ctrl+S as the submit shortcut (common "send" shortcut in chat apps).
    """

    BINDINGS = [
        Binding("ctrl+s", "submit_prompt", "Submit", show=False, priority=True),
        Binding("ctrl+u", "clear_input", "Clear", show=False, priority=True),
    ]

    class Submitted(Message):
        """Fired when user presses Ctrl+S."""
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def action_submit_prompt(self) -> None:
        text = self.text.strip()
        if text:
            self.post_message(self.Submitted(text))

    def action_clear_input(self) -> None:
        """Clear the text area content and reset cursor."""
        self.text = ""
        self.move_cursor((0, 0))


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
        width: 110;
        height: auto;
        max-height: 100%;
    }
    #input-row {
        height: auto;
        margin: 1 2;
    }
    #prompt-input {
        height: 10;
        border: tall #30363d;
        width: 1fr;
    }
    #prompt-input:focus {
        border: tall #58a6ff;
    }
    #shortcuts-panel {
        width: 30;
        height: 10;
        border: tall #30363d;
        margin-left: 1;
        padding: 0 1;
    }
    #recent-label {
        margin: 1 2 0 2;
        color: #8b949e;
    }
    PipelineList {
        margin: 0 2;
        height: auto;
        max-height: 10;
    }
    """

    BINDINGS = [
        ("escape", "app.quit", "Quit"),
        Binding("tab", "cycle_focus", "Switch focus", show=False),
    ]

    class TaskSubmitted(Message):
        def __init__(self, task: str) -> None:
            self.task = task
            super().__init__()

    def __init__(self, recent_pipelines: list[dict] | None = None) -> None:
        super().__init__()
        self._recent_pipelines = recent_pipelines or []

    def compose(self) -> ComposeResult:
        shortcuts_text = (
            "[#D8DEE9 bold]Shortcuts[/]\n"
            "\n"
            "[#5FA8FF]Ctrl+S[/]  [#A9C7E8]Submit pipeline[/]\n"
            "[#5FA8FF]Ctrl+U[/]  [#A9C7E8]Clear input[/]\n"
            "[#5FA8FF]Tab[/]     [#A9C7E8]Switch focus[/]\n"
            "[#5FA8FF]Ctrl+P[/]  [#A9C7E8]Command palette[/]\n"
            "[#5FA8FF]Esc[/]     [#A9C7E8]Quit[/]\n"
            "[#5FA8FF]?[/]       [#A9C7E8]Help[/]"
        )
        with Center():
            with Vertical(id="home-container"):
                yield ForgeLogo()
                with Horizontal(id="input-row"):
                    yield PromptTextArea(id="prompt-input")
                    yield Static(shortcuts_text, id="shortcuts-panel")
                yield Static("Recent pipelines", id="recent-label")
                yield PipelineList()

    def on_mount(self) -> None:
        pipeline_list = self.query_one(PipelineList)
        pipeline_list.update_pipelines(self._recent_pipelines)

    def on_prompt_text_area_submitted(self, event: PromptTextArea.Submitted) -> None:
        """Ctrl+Enter: submit the task prompt."""
        if event.text:
            self.post_message(self.TaskSubmitted(event.text))

    def action_cycle_focus(self) -> None:
        """Tab: switch focus between PromptTextArea and PipelineList."""
        prompt = self.query_one(PromptTextArea)
        pipeline_list = self.query_one(PipelineList)
        if prompt.has_focus:
            pipeline_list.focus()
        else:
            prompt.focus()
