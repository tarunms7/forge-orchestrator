"""Final approval screen — shows summary before PR creation."""

from __future__ import annotations
from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import Static, Footer
from textual.containers import Vertical, Center
from textual.message import Message


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


class FinalApprovalScreen(Screen):
    class CreatePR(Message):
        pass

    class ReRun(Message):
        pass

    BINDINGS = [
        Binding("enter", "create_pr", "Create PR", show=True, priority=True),
        Binding("d", "view_diff", "View Diff", show=True),
        Binding("r", "rerun", "Re-run Failed", show=True),
        Binding("escape", "app.pop_screen", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    FinalApprovalScreen { align: center middle; }
    #approval-container { width: 80; padding: 2; }
    """

    def __init__(self, stats: dict | None = None, tasks: list[dict] | None = None) -> None:
        super().__init__()
        self._stats = stats or {}
        self._tasks = tasks or []

    def compose(self):
        with Center():
            with Vertical(id="approval-container"):
                yield Static("[bold #58a6ff]Pipeline Complete — Final Approval[/]\n", id="header")
                yield Static(format_summary_stats(self._stats), id="stats")
                yield Static("\n[bold]Tasks:[/]", id="tasks-header")
                yield Static(format_task_table(self._tasks), id="task-table")
                yield Static("\n[#8b949e]Press Enter to create PR, d for diff, r to re-run, Esc to cancel[/]")
        yield Footer()

    def action_create_pr(self) -> None:
        self.post_message(self.CreatePR())

    def action_rerun(self) -> None:
        self.post_message(self.ReRun())

    def action_view_diff(self) -> None:
        pass  # Will be wired in Task 18
