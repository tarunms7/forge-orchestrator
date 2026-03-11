"""Final approval screen — shows summary before PR creation."""

from __future__ import annotations

import asyncio

from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import Static, Footer
from textual.containers import Vertical, Center
from textual.message import Message

from forge.tui.widgets.diff_viewer import DiffViewer


def format_summary_stats(stats: dict) -> str:
    added = stats.get("added", 0)
    removed = stats.get("removed", 0)
    files = stats.get("files", 0)
    elapsed = stats.get("elapsed", "?")
    cost = stats.get("cost", 0)
    questions = stats.get("questions", 0)
    lines = [
        f"[bold #3fb950]+{added}[/] / [bold #f85149]-{removed}[/]  •  {files} files  •  {elapsed}",
        f"[#8b949e]${cost:.2f} cost  •  {questions} questions answered[/]",
    ]
    return "\n".join(lines)


def format_task_table(tasks: list[dict]) -> str:
    if not tasks:
        return "[#484f58]No tasks[/]"
    lines = []
    for t in tasks:
        title = t.get("title", "?")
        added = t.get("added", 0)
        removed = t.get("removed", 0)
        tp = t.get("tests_passed", 0)
        tt = t.get("tests_total", 0)
        review = t.get("review", "?")
        review_icon = "[#3fb950]✓[/]" if review == "passed" else "[#f85149]✗[/]"
        lines.append(f"  {review_icon} [bold]{title}[/]  [#8b949e]+{added}/-{removed}  tests: {tp}/{tt}[/]")
    return "\n".join(lines)


class DiffScreen(Screen):
    """Full-screen diff viewer pushed from FinalApprovalScreen."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back", show=True),
        Binding("q", "app.pop_screen", "Back", show=False),
    ]

    DEFAULT_CSS = """
    DiffScreen { layout: vertical; }
    """

    def __init__(self, diff_text: str, branch: str = "") -> None:
        super().__init__()
        self._diff_text = diff_text
        self._branch = branch

    def compose(self):
        viewer = DiffViewer()
        viewer.update_diff("pipeline", f"diff main...{self._branch}", self._diff_text)
        yield viewer
        yield Footer()


class FinalApprovalScreen(Screen):
    class CreatePR(Message):
        pass

    class ReRun(Message):
        pass

    BINDINGS = [
        Binding("enter", "create_pr", "Create PR", show=True, priority=True),
        Binding("d", "view_diff", "View Diff", show=True),
        Binding("r", "rerun", "Re-run Failed", show=True),
        Binding("n", "new_task", "New Task", show=True),
        Binding("escape", "app.pop_screen", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    FinalApprovalScreen { align: center middle; }
    #approval-container { width: 80; padding: 2; }
    #pr-url { margin-top: 1; }
    """

    def __init__(
        self,
        stats: dict | None = None,
        tasks: list[dict] | None = None,
        pipeline_branch: str = "",
    ) -> None:
        super().__init__()
        self._stats = stats or {}
        self._tasks = tasks or []
        self._pipeline_branch = pipeline_branch

    def compose(self):
        with Center():
            with Vertical(id="approval-container"):
                yield Static("[bold #58a6ff]Pipeline Complete — Final Approval[/]\n", id="header")
                yield Static(format_summary_stats(self._stats), id="stats")
                yield Static("", id="pr-url")
                yield Static("\n[bold]Tasks:[/]", id="tasks-header")
                yield Static(format_task_table(self._tasks), id="task-table")
                yield Static("\n[#8b949e]Enter: create PR  d: diff  r: re-run  n: new task  Esc: cancel[/]")
        yield Footer()

    def show_pr_url(self, url: str) -> None:
        """Display the PR URL inline in the stats area."""
        try:
            pr_widget = self.query_one("#pr-url", Static)
            pr_widget.update(f"[bold #3fb950]PR created:[/] [underline #58a6ff]{url}[/]")
        except Exception:
            pass

    def action_new_task(self) -> None:
        """Return to HomeScreen for a new task, cleaning up pipeline state."""
        self.app.action_reset_for_new_task()

    def action_create_pr(self) -> None:
        self.post_message(self.CreatePR())

    def action_rerun(self) -> None:
        self.post_message(self.ReRun())

    def action_view_diff(self) -> None:
        if not self._pipeline_branch:
            self.notify("No pipeline branch available.", severity="warning")
            return
        asyncio.create_task(self._load_and_show_diff())

    async def _load_and_show_diff(self) -> None:
        """Run git diff and push a DiffScreen with the result."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", f"main...{self._pipeline_branch}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            diff_text = stdout.decode(errors="replace") if proc.returncode == 0 else f"git diff failed: {stderr.decode(errors='replace')}"
        except Exception as e:
            diff_text = f"Error running git diff: {e}"
        self.app.push_screen(DiffScreen(diff_text, branch=self._pipeline_branch))
