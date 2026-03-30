"""Home screen — logo, prompt input, recent pipelines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static, TextArea

from forge.tui.theme import PIPELINE_STATUS_ICONS as _PIPELINE_STATUS_ICONS
from forge.tui.widgets.branch_selector import BranchInput, BranchSelector
from forge.tui.widgets.logo import ForgeLogo
from forge.tui.widgets.pipeline_list import PipelineList, is_pipeline_resumable
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
        width: 1fr;
        max-width: 120;
        height: 1fr;
        padding: 0 2;
    }
    ForgeLogo {
        width: 100%;
    }
    #input-row {
        width: 100%;
        height: 9;
        margin: 1 0;
    }
    #prompt-input {
        height: 9;
        border: tall #30363d;
        background: #161b22;
        width: 1fr;
    }
    #prompt-input:focus {
        border: tall #58a6ff;
    }
    #shortcuts-panel {
        width: 32;
        height: 9;
        border: tall #30363d;
        background: #161b22;
        margin-left: 1;
        padding: 0 1;
    }
    #branch-row {
        width: 100%;
        height: auto;
        max-height: 5;
        margin: 0 0 1 0;
    }
    .branch-field {
        width: 1fr;
        height: auto;
        margin-right: 1;
    }
    .workspace-repo-row {
        width: 100%;
        height: auto;
    }
    .repo-id-label {
        width: 15;
        height: 3;
        content-align: left middle;
        color: #58a6ff;
    }
    .branch-label {
        color: #8b949e;
        height: 1;
        margin-bottom: 0;
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
        color: #6e7681;
    }
    PipelineList {
        width: 100%;
        height: 1fr;
        max-height: 12;
    }
    """

    BINDINGS = [
        ("escape", "app.quit", "Quit"),
        Binding("q", "app.quit_app", "Quit", show=False),
        Binding("tab", "cycle_focus", "Switch focus", show=False),
    ]

    class TaskSubmitted(Message):
        def __init__(
            self,
            task: str,
            base_branch: str = "main",
            branch_name: str = "",
            per_repo_base_branches: dict[str, str] | None = None,
        ) -> None:
            self.task = task
            self.base_branch = base_branch
            self.branch_name = branch_name
            self.per_repo_base_branches = per_repo_base_branches
            super().__init__()

    def __init__(
        self,
        recent_pipelines: list[dict] | None = None,
        repos: list | None = None,
        project_dir: str = "",
    ) -> None:
        super().__init__()
        self._recent_pipelines = recent_pipelines or []
        self._repos = repos or []
        self._project_dir = project_dir
        self._is_workspace = len(self._repos) > 1 or (
            len(self._repos) == 1 and self._repos[0].id != "default"
        )

    def compose(self) -> ComposeResult:
        shortcuts_text = (
            "[#e6edf3 bold]Shortcuts[/]\n"
            "\n"
            "[#58a6ff]Ctrl+S[/]  Submit pipeline\n"
            "[#58a6ff]Ctrl+U[/]  Clear input\n"
            "[#58a6ff]Tab[/]     Switch focus\n"
            "[#58a6ff]Ctrl+P[/]  Command palette\n"
            "[#58a6ff]?[/]       Help"
        )
        with Vertical(id="home-container"):
            yield ForgeLogo()
            with Horizontal(id="input-row"):
                yield PromptTextArea(id="prompt-input")
                yield Static(shortcuts_text, id="shortcuts-panel")
            if self._is_workspace:
                yield Static("[#8b949e]Workspace repos[/]", id="workspace-label")
                for repo in self._repos:
                    with Horizontal(classes="workspace-repo-row"):
                        yield Static(f"[#58a6ff]{repo.id}[/]", classes="repo-id-label")
                        yield BranchSelector(
                            default=repo.base_branch,
                            id=f"base-branch-{repo.id}",
                        )
                with Vertical(classes="branch-field"):
                    yield Static("[#8b949e]Branch name (optional)[/]", classes="branch-label")
                    yield BranchInput(id="branch-name-input")
            else:
                with Horizontal(id="branch-row"):
                    with Vertical(classes="branch-field"):
                        yield Static("[#8b949e]Base branch[/]", classes="branch-label")
                        default_base = self._repos[0].base_branch if self._repos else ""
                        yield BranchSelector(default=default_base, id="base-branch-selector")
                    with Vertical(classes="branch-field"):
                        yield Static(
                            "[#8b949e]Branch name (optional)[/]",
                            classes="branch-label",
                        )
                        yield BranchInput(id="branch-name-input")
            yield Static("Recent pipelines", id="recent-label")
            yield PipelineList()
        yield ShortcutBar(
            [
                ("Ctrl+S", "Submit"),
                ("Ctrl+U", "Clear"),
                ("Tab", "Focus"),
                ("j/k", "History"),
                ("Enter", "View"),
                ("Shift+R", "Resume"),
                ("q", "Quit"),
            ]
        )

    def _update_shortcut_label(self, pipeline: dict | None) -> None:
        """Update the ShortcutBar based on selected pipeline."""
        resumable = pipeline and is_pipeline_resumable(pipeline)
        try:
            bar = self.query_one(ShortcutBar)
            shortcuts = [
                ("Ctrl+S", "Submit"),
                ("Ctrl+U", "Clear"),
                ("Tab", "Focus"),
                ("j/k", "History"),
                ("Enter", "View"),
            ]
            if resumable:
                shortcuts.append(("Shift+R", "Resume"))
            shortcuts.append(("q", "Quit"))
            bar.shortcuts = shortcuts
        except Exception:
            pass

    def on_pipeline_list_cursor_moved(self, event: PipelineList.CursorMoved) -> None:
        """Update shortcut bar when pipeline selection changes."""
        self._update_shortcut_label(event.pipeline)

    async def on_mount(self) -> None:
        import asyncio

        pipeline_list = self.query_one(PipelineList)
        pipeline_list.update_pipelines(self._recent_pipelines)

        # Set initial shortcut label based on first pipeline
        self._update_shortcut_label(pipeline_list.selected_pipeline)

        # Load branches into ALL selectors concurrently (not sequentially)
        tasks: list = []
        if self._is_workspace:
            for repo in self._repos:
                try:
                    sel = self.query_one(f"#base-branch-{repo.id}", BranchSelector)
                    tasks.append(sel.load_branches(repo.path))
                except Exception:
                    pass
        else:
            try:
                sel = self.query_one("#base-branch-selector", BranchSelector)
                repo_path = self._repos[0].path if self._repos else self._project_dir
                if repo_path:
                    tasks.append(sel.load_branches(repo_path))
            except Exception:
                pass

        # Also load branches into the pipeline branch input
        try:
            branch_inp = self.query_one("#branch-name-input", BranchInput)
            repo_path = self._repos[0].path if self._repos else self._project_dir
            if repo_path:
                tasks.append(branch_inp.load_branches(repo_path))
        except Exception:
            pass

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def on_prompt_text_area_submitted(self, event: PromptTextArea.Submitted) -> None:
        """Ctrl+S: submit the task prompt."""
        if event.text:
            per_repo: dict[str, str] | None = None
            if self._is_workspace:
                per_repo = {}
                for repo in self._repos:
                    try:
                        sel = self.query_one(f"#base-branch-{repo.id}", BranchSelector)
                        per_repo[repo.id] = sel.selected_value
                    except Exception:
                        per_repo[repo.id] = repo.base_branch
                base_branch = per_repo.get(self._repos[0].id, "main") if self._repos else "main"
            else:
                try:
                    sel = self.query_one("#base-branch-selector", BranchSelector)
                    base_branch = sel.selected_value or "main"
                except Exception:
                    base_branch = "main"
            try:
                branch_inp = self.query_one("#branch-name-input", BranchInput)
                branch_name = branch_inp.value
            except Exception:
                branch_name = ""
            self.post_message(
                self.TaskSubmitted(
                    event.text,
                    base_branch=base_branch,
                    branch_name=branch_name,
                    per_repo_base_branches=per_repo,
                )
            )

    def action_cycle_focus(self) -> None:
        """Tab: cycle focus through prompt → base branch → branch name → history."""
        from textual.widgets import Input as TextualInput
        from textual.widgets import Select

        focusable: list = [self.query_one(PromptTextArea)]

        # Base branch selectors (one per repo in workspace, or single)
        for container in self.query(BranchSelector):
            try:
                focusable.append(container.query_one(Select))
            except Exception:
                pass

        # Pipeline branch: just the Select dropdown
        for container in self.query(BranchInput):
            try:
                focusable.append(container.query_one(Select))
            except Exception:
                pass

        focusable.append(self.query_one(PipelineList))

        current = self.focused
        if current in focusable:
            idx = (focusable.index(current) + 1) % len(focusable)
        else:
            idx = 0
        focusable[idx].focus()
