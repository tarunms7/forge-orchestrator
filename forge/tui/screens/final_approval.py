"""Final approval screen — shows summary before PR creation."""

from __future__ import annotations

import asyncio
import os

from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import Static
from textual.containers import Vertical, VerticalScroll, Center
from textual.message import Message

from forge.tui.widgets.diff_viewer import DiffViewer
from forge.tui.widgets.followup_input import FollowUpInput
from forge.tui.widgets.shortcut_bar import ShortcutBar


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
    """Format task table with status icons based on task state."""
    if not tasks:
        return "[#484f58]No tasks[/]"
    lines: list[str] = []
    for t in tasks:
        title = t.get("title", "?")
        state = t.get("state", t.get("review", "?"))

        if state == "done":
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            files = t.get("files", 0)
            tp = t.get("tests_passed", 0)
            tt = t.get("tests_total", 0)
            stats = f"+{added}/-{removed}"
            if tt > 0:
                stats += f"  tests: {tp}/{tt}"
            if files > 0:
                stats += f"  {files} files"
            lines.append(f"  [#3fb950]✅[/] [bold]{title}[/]  [#8b949e]{stats}[/]")
        elif state == "error":
            error = t.get("error", "failed")
            lines.append(f"  [#f85149]❌[/] [bold]{title}[/]  [#f85149]{error}[/]")
        elif state == "blocked":
            error = t.get("error", "blocked by dependency")
            lines.append(f"  [#d29922]⚠️[/] [bold]{title}[/]  [#d29922]{error}[/]")
        elif state == "cancelled":
            lines.append(f"  [#8b949e]✘[/] [bold]{title}[/]  [#8b949e]cancelled[/]")
        else:
            # Legacy: review-based display
            review = t.get("review", "?")
            icon = "[#3fb950]✓[/]" if review == "passed" else "[#f85149]✗[/]"
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            tp = t.get("tests_passed", 0)
            tt = t.get("tests_total", 0)
            stats = f"+{added}/-{removed}"
            if tt > 0:
                stats += f"  tests: {tp}/{tt}"
            lines.append(f"  {icon} [bold]{title}[/]  [#8b949e]{stats}[/]")
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
        yield ShortcutBar([
            ("j/k", "Scroll"),
            ("g/G", "Top/Bottom"),
            ("Esc", "Back"),
        ])


class FinalApprovalScreen(Screen):
    class CreatePR(Message):
        pass

    class ReRun(Message):
        pass

    class SkipFailed(Message):
        pass

    class FollowUp(Message):
        """Emitted when user submits a follow-up prompt."""

        def __init__(self, prompt: str, branch: str, files_changed: int) -> None:
            self.prompt = prompt
            self.branch = branch
            self.files_changed = files_changed
            super().__init__()

    BINDINGS = [
        Binding("enter", "create_pr", "Create PR", show=True, priority=True),
        Binding("d", "view_diff", "View Diff", show=True, priority=True),
        Binding("r", "rerun", "Re-run Failed", show=True, priority=True),
        Binding("s", "skip_failed", "Skip & Finish", show=True, priority=True),
        Binding("f", "focus_followup", "Follow Up", show=True),
        Binding("n", "new_task", "New Task", show=True, priority=True),
        Binding("ctrl+s", "submit_followup", "Submit Follow-up", show=False),
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
        base_branch: str = "main",
        partial: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._stats = stats or {}
        self._tasks = tasks or []
        self._pipeline_branch = pipeline_branch
        self._base_branch = base_branch
        self._partial = partial

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Dynamically enable/disable actions based on partial mode."""
        if action in ("rerun", "skip_failed"):
            return self._partial  # only available in partial mode
        if action == "new_task":
            return not self._partial  # only available in full mode
        return True

    def on_mount(self) -> None:
        asyncio.create_task(self._check_behind_main())

    async def _check_behind_main(self) -> None:
        """Check if pipeline branch is behind the base branch and show warning."""
        project_dir = self._get_project_dir()
        if not project_dir or not self._pipeline_branch:
            return
        base = self._base_branch
        try:
            fetch = await asyncio.create_subprocess_exec(
                "git", "fetch", "origin", base, "--quiet",
                cwd=project_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(fetch.wait(), timeout=15)

            proc = await asyncio.create_subprocess_exec(
                "git", "rev-list", "--count",
                f"{self._pipeline_branch}..origin/{base}",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            count = int(stdout.decode().strip()) if stdout else 0

            if count > 0 and self.is_running:
                warning = self.query_one("#behind-main-warning", Static)
                warning.update(
                    f"[bold #d29922]⚠ Branch is {count} commit{'s' if count != 1 else ''} "
                    f"behind {base}. PR may have merge conflicts.[/]"
                )
        except Exception:
            pass  # Non-critical — silently skip if git fails

    def compose(self):
        files_count = self._stats.get("files", 0)
        if self._partial:
            done = sum(1 for t in self._tasks if t.get("state") == "done")
            total = len(self._tasks)
            header = f"Pipeline Partial — {done}/{total} Tasks Completed"
        else:
            header = "Pipeline Complete — Final Approval"
        with VerticalScroll():
            with Center():
                with Vertical(id="approval-container"):
                    yield Static(f"[bold #58a6ff]{header}[/]\n", id="header")
                    yield Static("", id="behind-main-warning")  # populated by _check_behind_main
                    yield Static(format_summary_stats(self._stats), id="stats")
                    yield Static("", id="pr-url")
                    yield Static("\n[bold]Tasks:[/]", id="tasks-header")
                    yield Static(format_task_table(self._tasks), id="task-table")
                    yield Static(
                        "\n[#8b949e]Enter: create PR  d: diff  r: re-run  "
                        "f: follow up  n: new task  Esc: cancel[/]"
                    )
                    yield FollowUpInput(
                        branch=self._pipeline_branch,
                        files_changed=files_count,
                    )
        if self._partial:
            yield ShortcutBar([
                ("Enter", "Create PR (completed only)"),
                ("r", "Retry Failed"),
                ("s", "Skip & Finish"),
                ("d", "View Diff"),
                ("f", "Follow Up"),
                ("Esc", "Back"),
            ])
        else:
            yield ShortcutBar([
                ("Enter", "Create PR"),
                ("d", "View Diff"),
                ("f", "Follow Up"),
                ("n", "New Task"),
                ("Esc", "Back"),
            ])

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

    def action_skip_failed(self) -> None:
        """Skip all failed tasks and finish the pipeline."""
        self.post_message(self.SkipFailed())

    def action_focus_followup(self) -> None:
        """Focus the follow-up input area."""
        try:
            followup = self.query_one(FollowUpInput)
            followup.focus_input()
        except Exception:
            pass

    def action_submit_followup(self) -> None:
        """Submit the follow-up input via Ctrl+S."""
        try:
            followup = self.query_one(FollowUpInput)
            followup.submit()
        except Exception:
            pass

    def on_follow_up_input_submitted(self, event: FollowUpInput.Submitted) -> None:
        """Relay the follow-up submission as a screen-level message."""
        self.post_message(
            self.FollowUp(event.prompt, event.branch, event.files_changed)
        )

    def action_view_diff(self) -> None:
        if not self._pipeline_branch:
            self.notify("No pipeline branch available.", severity="warning")
            return
        asyncio.create_task(self._load_and_show_diff())

    def _get_project_dir(self) -> str | None:
        """Get the project directory from the app if available."""
        try:
            return getattr(self.app, "_project_dir", None) or os.getcwd()
        except Exception:
            return None

    async def _load_and_show_diff(self) -> None:
        """Run git diff and push a DiffScreen with the result."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", f"{self._base_branch}...{self._pipeline_branch}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._get_project_dir(),
            )
            stdout, stderr = await proc.communicate()
            diff_text = stdout.decode(errors="replace") if proc.returncode == 0 else f"git diff failed: {stderr.decode(errors='replace')}"
        except Exception as e:
            diff_text = f"Error running git diff: {e}"
        if not self.is_running:
            return
        self.app.push_screen(DiffScreen(diff_text, branch=self._pipeline_branch))
