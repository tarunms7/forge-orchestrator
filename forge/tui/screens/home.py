"""Home screen — logo, prompt input, recent pipelines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Input, Static, TextArea

from forge.tui.widgets.logo import ForgeLogo
from forge.tui.widgets.pipeline_list import PipelineList
from forge.tui.widgets.shortcut_bar import ShortcutBar


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
    import os

    if not pipelines:
        return "[#8b949e]No recent pipelines[/]"
    lines = []
    for p in pipelines:
        status = p.get("status", "unknown")
        icon, color = _PIPELINE_STATUS_ICONS.get(status, ("?", "#8b949e"))
        desc = p.get("description", "Untitled")[:50]
        cost = p.get("cost", 0.0)
        date = p.get("created_at", "")[:10]
        project_dir = p.get("project_dir", "") or ""
        project_tag = ""
        if project_dir:
            folder = os.path.basename(project_dir.rstrip("/"))[:20]
            if folder:
                project_tag = f"[#8b949e]{folder}[/]  "
        lines.append(
            f"  [{color}]{icon}[/] {desc}  {project_tag}[#8b949e]{date} \u00b7 ${cost:.2f}[/]"
        )
    return "\n".join(lines)


class HomeScreen(Screen):
    """Landing screen with logo and task input."""

    DEFAULT_CSS = """
    HomeScreen {
        layout: vertical;
        align: center top;
    }
    #home-container {
        width: 110;
        height: 1fr;
    }
    ForgeLogo {
        width: 100%;
    }
    #input-row {
        width: 100%;
        height: auto;
        margin: 1 0;
    }
    #prompt-input {
        height: 7;
        border: tall #30363d;
        width: 1fr;
    }
    #prompt-input:focus {
        border: tall #58a6ff;
    }
    #shortcuts-panel {
        width: 30;
        height: 7;
        border: tall #30363d;
        margin-left: 1;
        padding: 0 1;
    }
    #branch-row {
        width: 100%;
        height: auto;
    }
    .branch-field {
        width: 1fr;
        height: auto;
    }
    .branch-field Input {
        height: 3;
        border: tall #30363d;
        background: #161b22;
        color: #e6edf3;
        padding: 0 1;
    }
    .branch-field Input:focus {
        border: tall #58a6ff;
    }
    .branch-label {
        color: #8b949e;
        height: 1;
    }
    #workspace-info {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 0 0;
    }
    #recent-label {
        width: 100%;
        margin: 1 0 0 0;
        color: #8b949e;
    }
    PipelineList {
        width: 100%;
        height: auto;
        max-height: 8;
    }
    """

    BINDINGS = [
        ("escape", "app.quit", "Quit"),
        Binding("tab", "cycle_focus", "Switch focus", show=False),
    ]

    class TaskSubmitted(Message):
        def __init__(self, task: str, base_branch: str = "main", branch_name: str = "") -> None:
            self.task = task
            self.base_branch = base_branch
            self.branch_name = branch_name
            super().__init__()

    def __init__(
        self, recent_pipelines: list[dict] | None = None, repos: list | None = None
    ) -> None:
        super().__init__()
        self._recent_pipelines = recent_pipelines or []
        self._repos = repos or []
        self._is_workspace = len(self._repos) > 1 or (
            len(self._repos) == 1 and self._repos[0].id != "default"
        )

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
        with Vertical(id="home-container"):
            yield ForgeLogo()
            with Horizontal(id="input-row"):
                yield PromptTextArea(id="prompt-input")
                yield Static(shortcuts_text, id="shortcuts-panel")
            if self._is_workspace:
                # Workspace mode: show repos as read-only info
                repo_lines = "  ".join(
                    f"[#58a6ff]{r.id}[/] [#8b949e]({r.base_branch})[/]" for r in self._repos
                )
                yield Static(
                    f"[#8b949e]Workspace repos:[/]  {repo_lines}\n"
                    f"[#6e7681 italic]Edit .forge/workspace.toml to change base branches[/]",
                    id="workspace-info",
                )
            else:
                with Horizontal(id="branch-row"):
                    with Vertical(classes="branch-field"):
                        yield Static("[#8b949e]Base branch[/]", classes="branch-label")
                        yield Input(value="main", id="base-branch-input")
                    with Vertical(classes="branch-field"):
                        yield Static("[#8b949e]Branch name (optional)[/]", classes="branch-label")
                        yield Input(placeholder="Auto-generated if empty", id="branch-name-input")
            yield Static("Recent pipelines", id="recent-label")
            yield PipelineList()
        yield ShortcutBar(
            [
                ("Ctrl+S", "Submit Task"),
                ("j/k", "History"),
                ("Enter", "Resume Selected"),
                ("q", "Quit"),
            ]
        )

    def on_mount(self) -> None:
        pipeline_list = self.query_one(PipelineList)
        pipeline_list.update_pipelines(self._recent_pipelines)

    def on_prompt_text_area_submitted(self, event: PromptTextArea.Submitted) -> None:
        """Ctrl+Enter: submit the task prompt."""
        if event.text:
            if self._is_workspace:
                # Workspace mode: base branch comes from workspace.toml per-repo,
                # use the first repo's base branch as the pipeline-level default
                base_branch = self._repos[0].base_branch if self._repos else "main"
                branch_name = ""
            else:
                base_branch = self.query_one("#base-branch-input", Input).value.strip() or "main"
                branch_name = self.query_one("#branch-name-input", Input).value.strip()
            self.post_message(
                self.TaskSubmitted(event.text, base_branch=base_branch, branch_name=branch_name)
            )

    def on_click(self, event) -> None:
        """Click outside focused widget to unfocus it."""
        # Let Textual handle focus naturally — clicking a focusable widget
        # focuses it. If user clicks a non-focusable area, blur everything.
        from textual.widgets import Input

        target = getattr(event, "widget", None) if hasattr(event, "widget") else None
        if target is not None and not isinstance(target, (PromptTextArea, Input, PipelineList)):
            self.set_focus(None)

    def action_cycle_focus(self) -> None:
        """Tab: switch focus between PromptTextArea and PipelineList."""
        prompt = self.query_one(PromptTextArea)
        pipeline_list = self.query_one(PipelineList)
        if prompt.has_focus:
            pipeline_list.focus()
        else:
            prompt.focus()
