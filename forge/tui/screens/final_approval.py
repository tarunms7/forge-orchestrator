"""Final approval screen — shows summary before PR creation."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict

from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static

from forge.core.async_utils import safe_create_task
from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    TEXT_MUTED,
    TEXT_SECONDARY,
)
from forge.tui.widgets.diff_viewer import DiffViewer
from forge.tui.widgets.followup_input import FollowUpInput
from forge.tui.widgets.shortcut_bar import ShortcutBar

logger = logging.getLogger("forge.tui.screens.final_approval")


def format_summary_stats(stats: dict, multi_repo: bool = False) -> str:
    added = stats.get("added", 0)
    removed = stats.get("removed", 0)
    files = stats.get("files", 0)
    elapsed = stats.get("elapsed", "?")
    cost = stats.get("cost", 0)
    questions = stats.get("questions", 0)
    lines: list[str] = []
    if multi_repo:
        repo_count = stats.get("repo_count", 0)
        task_count = stats.get("task_count", 0)
        lines.append(f"{repo_count} repos, {task_count} tasks")
    lines.extend(
        [
            f"[bold {ACCENT_GREEN}]+{added}[/] / [bold {ACCENT_RED}]-{removed}[/]  •  {files} files  •  {elapsed}",
            f"[{TEXT_SECONDARY}]${cost:.2f} cost  •  {questions} questions answered[/]",
        ]
    )
    return "\n".join(lines)


def _format_task_list(tasks: list[dict], indent: str = "  ") -> list[str]:
    """Format a list of tasks as lines with status icons."""
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
            lines.append(
                f"{indent}[{ACCENT_GREEN}]✅[/] [bold]{title}[/]  [{TEXT_SECONDARY}]{stats}[/]"
            )
        elif state == "error":
            error = t.get("error", "failed")
            lines.append(f"{indent}[{ACCENT_RED}]❌[/] [bold]{title}[/]  [{ACCENT_RED}]{error}[/]")
        elif state == "blocked":
            error = t.get("error", "blocked by dependency")
            lines.append(
                f"{indent}[{ACCENT_YELLOW}]⚠️[/] [bold]{title}[/]  [{ACCENT_YELLOW}]{error}[/]"
            )
        elif state == "cancelled":
            lines.append(
                f"{indent}[{TEXT_SECONDARY}]✘[/] [bold]{title}[/]  [{TEXT_SECONDARY}]cancelled[/]"
            )
        else:
            # Active states: merging, in_review, awaiting_approval, in_progress, todo
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            tp = t.get("tests_passed", 0)
            tt = t.get("tests_total", 0)
            stats = f"+{added}/-{removed}"
            if tt > 0:
                stats += f"  tests: {tp}/{tt}"
            files = t.get("files", 0)
            if files > 0:
                stats += f"  {files} files"

            if state in ("merging", "awaiting_approval"):
                # Check review gates — if all passed, show green
                gates = t.get("review_gates", {})
                all_passed = gates and all(g.get("status") == "passed" for g in gates.values())
                if all_passed:
                    lines.append(
                        f"{indent}[{ACCENT_GREEN}]✅[/] [bold]{title}[/]  [{TEXT_SECONDARY}]{stats}[/]"
                    )
                else:
                    lines.append(
                        f"{indent}[{ACCENT_YELLOW}]⏳[/] [bold]{title}[/]  [{TEXT_SECONDARY}]{state}  {stats}[/]"
                    )
            elif state == "in_review":
                lines.append(
                    f"{indent}[{ACCENT_YELLOW}]⏳[/] [bold]{title}[/]  [{TEXT_SECONDARY}]reviewing  {stats}[/]"
                )
            elif state == "in_progress":
                lines.append(
                    f"{indent}[{ACCENT_BLUE}]⚙[/] [bold]{title}[/]  [{TEXT_SECONDARY}]running  {stats}[/]"
                )
            else:
                # Fallback for unknown states (todo, etc.)
                lines.append(
                    f"{indent}[{TEXT_SECONDARY}]○[/] [bold]{title}[/]  [{TEXT_SECONDARY}]{state}  {stats}[/]"
                )
    return lines


def format_task_table(tasks: list[dict], multi_repo: bool = False) -> str:
    """Format task table with status icons based on task state."""
    if not tasks:
        return f"[{TEXT_MUTED}]No tasks[/]"

    if not multi_repo:
        lines = _format_task_list(tasks, indent="  ")
        return "\n".join(lines)

    # Group tasks by repo
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for t in tasks:
        repo_id = t.get("repo", "default")
        if repo_id not in groups:
            groups[repo_id] = []
        groups[repo_id].append(t)

    lines: list[str] = []
    for repo_id, repo_tasks in groups.items():
        # Aggregate stats for repo header
        total_added = sum(t.get("added", 0) for t in repo_tasks)
        total_removed = sum(t.get("removed", 0) for t in repo_tasks)
        lines.append(f"[bold {ACCENT_BLUE}]{repo_id}[/]  +{total_added}/-{total_removed}")
        lines.extend(_format_task_list(repo_tasks, indent="    "))
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
        yield ShortcutBar(
            [
                ("j/k", "Scroll"),
                ("g/G", "Top/Bottom"),
                ("Esc", "Back"),
            ]
        )


class RepoSelectorScreen(Screen):
    """Repo selector screen for multi-repo diff viewing."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "select", "Select", show=True),
        Binding("escape", "app.pop_screen", "Back", show=True),
    ]

    DEFAULT_CSS = """
    RepoSelectorScreen { align: center middle; }
    #repo-selector-container { width: 60; padding: 2; }
    .repo-item { padding: 0 1; }
    .repo-item--selected { background: #1f6feb; }
    """

    def __init__(
        self,
        repos: list[dict],
        tasks: list[dict],
        on_select: callable,
    ) -> None:
        super().__init__()
        self._repos = repos
        self._tasks = tasks
        self._on_select = on_select
        self._cursor = 0

    def compose(self):
        with Center(), Vertical(id="repo-selector-container"):
            yield Static(f"[bold {ACCENT_BLUE}]Select Repository[/]\n")
            for i, repo in enumerate(self._repos):
                repo_id = repo.get("repo_id", repo.get("id", "unknown"))
                # Calculate aggregate stats for this repo
                repo_tasks = [t for t in self._tasks if t.get("repo") == repo_id]
                total_added = sum(t.get("added", 0) for t in repo_tasks)
                total_removed = sum(t.get("removed", 0) for t in repo_tasks)
                marker = "▸ " if i == 0 else "  "
                yield Static(
                    f"{marker}[bold]{repo_id}[/]  [{TEXT_SECONDARY}]+{total_added}/-{total_removed}[/]",
                    id=f"repo-item-{i}",
                    classes="repo-item repo-item--selected" if i == 0 else "repo-item",
                )
            yield Static(f"\n[{TEXT_SECONDARY}]j/k: navigate  Enter: select  Esc: back[/]")

    def _update_cursor(self) -> None:
        """Update visual cursor state."""
        for i in range(len(self._repos)):
            try:
                widget = self.query_one(f"#repo-item-{i}", Static)
                repo_id = self._repos[i].get("repo_id", self._repos[i].get("id", "unknown"))
                repo_tasks = [t for t in self._tasks if t.get("repo") == repo_id]
                total_added = sum(t.get("added", 0) for t in repo_tasks)
                total_removed = sum(t.get("removed", 0) for t in repo_tasks)
                marker = "▸ " if i == self._cursor else "  "
                widget.update(
                    f"{marker}[bold]{repo_id}[/]  [{TEXT_SECONDARY}]+{total_added}/-{total_removed}[/]"
                )
                if i == self._cursor:
                    widget.add_class("repo-item--selected")
                else:
                    widget.remove_class("repo-item--selected")
            except Exception:
                pass

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._repos) - 1:
            self._cursor += 1
            self._update_cursor()

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._update_cursor()

    def action_select(self) -> None:
        if self._repos:
            repo = self._repos[self._cursor]
            repo_id = repo.get("repo_id", repo.get("id", "unknown"))
            self.app.pop_screen()
            self._on_select(repo_id)


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
        multi_repo: bool = False,
        per_repo_pr_urls: dict[str, str] | None = None,
        repos: list[dict] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._stats = stats or {}
        self._tasks = tasks or []
        self._pipeline_branch = pipeline_branch
        self._base_branch = base_branch
        self._partial = partial
        self._multi_repo = multi_repo
        self._per_repo_pr_urls = per_repo_pr_urls or {}
        self._repos = repos or []

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Dynamically enable/disable actions based on partial mode."""
        if action in ("rerun", "skip_failed"):
            return self._partial  # only available in partial mode
        if action == "new_task":
            return not self._partial  # only available in full mode
        return True

    def on_mount(self) -> None:
        safe_create_task(self._check_behind_main(), logger=logger, name="check-behind-main")

    async def _check_behind_main(self) -> None:
        """Check if pipeline branch is behind the base branch and show warning."""
        project_dir = self._get_project_dir()
        if not project_dir or not self._pipeline_branch:
            return
        base = self._base_branch
        try:
            fetch = await asyncio.create_subprocess_exec(
                "git",
                "fetch",
                "origin",
                base,
                "--quiet",
                cwd=project_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(fetch.wait(), timeout=15)

            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-list",
                "--count",
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
                    f"[bold {ACCENT_YELLOW}]⚠ Branch is {count} commit{'s' if count != 1 else ''} "
                    f"behind {base}. PR may have merge conflicts.[/]"
                )
        except Exception:
            pass  # Non-critical — silently skip if git fails

    def compose(self):
        files_count = self._stats.get("files", 0)
        pr_label = "Create PRs" if self._multi_repo else "Create PR"
        if self._partial:
            done = sum(1 for t in self._tasks if t.get("state") == "done")
            total = len(self._tasks)
            header = f"Pipeline Partial — {done}/{total} Tasks Completed"
        else:
            header = "Pipeline Complete — Final Approval"
        with VerticalScroll():
            with Center():
                with Vertical(id="approval-container"):
                    yield Static(f"[bold {ACCENT_BLUE}]{header}[/]\n", id="header")
                    yield Static("", id="behind-main-warning")  # populated by _check_behind_main
                    yield Static(
                        format_summary_stats(self._stats, multi_repo=self._multi_repo), id="stats"
                    )
                    yield Static("", id="pr-url")
                    yield Static("\n[bold]Tasks:[/]", id="tasks-header")
                    yield Static(
                        format_task_table(self._tasks, multi_repo=self._multi_repo), id="task-table"
                    )
                    yield Static(
                        f"\n[{TEXT_SECONDARY}]Enter: create PR  d: diff  r: re-run  "
                        f"f: follow up  n: new task  Esc: cancel[/]"
                    )
                    yield FollowUpInput(
                        branch=self._pipeline_branch,
                        files_changed=files_count,
                    )
        if self._partial:
            yield ShortcutBar(
                [
                    ("Enter", f"{pr_label} (completed only)"),
                    ("r", "Retry Failed"),
                    ("s", "Skip & Finish"),
                    ("d", "View Diff"),
                    ("f", "Follow Up"),
                    ("Esc", "Back"),
                ]
            )
        else:
            yield ShortcutBar(
                [
                    ("Enter", pr_label),
                    ("d", "View Diff"),
                    ("f", "Follow Up"),
                    ("n", "New Task"),
                    ("Esc", "Back"),
                ]
            )

    def show_pr_url(self, url: str, repo_id: str | None = None) -> None:
        """Display PR URL(s) inline, with optional per-repo labeling."""
        try:
            pr_widget = self.query_one("#pr-url", Static)
            if repo_id is not None:
                self._per_repo_pr_urls[repo_id] = url
                # Render all accumulated repo PR URLs
                pr_lines = []
                for rid, rurl in self._per_repo_pr_urls.items():
                    pr_lines.append(
                        f"[bold {ACCENT_GREEN}]{rid}:[/] [underline {ACCENT_BLUE}]{rurl}[/]"
                    )
                pr_widget.update("\n".join(pr_lines))
            else:
                pr_widget.update(
                    f"[bold {ACCENT_GREEN}]PR created:[/] [underline {ACCENT_BLUE}]{url}[/]"
                )
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
        self.post_message(self.FollowUp(event.prompt, event.branch, event.files_changed))

    def action_view_diff(self) -> None:
        if not self._pipeline_branch:
            self.notify("No pipeline branch available.", severity="warning")
            return
        if self._multi_repo and self._repos:
            self.app.push_screen(
                RepoSelectorScreen(
                    repos=self._repos,
                    tasks=self._tasks,
                    on_select=lambda repo_id: safe_create_task(
                        self._load_and_show_diff(repo_id=repo_id),
                        logger=logger,
                        name=f"load-diff-{repo_id}",
                    ),
                )
            )
        else:
            safe_create_task(self._load_and_show_diff(), logger=logger, name="load-diff")

    def _get_project_dir(self) -> str | None:
        """Get the project directory from the app if available."""
        try:
            return getattr(self.app, "_project_dir", None) or os.getcwd()
        except Exception:
            return None

    def _get_repo_config(self, repo_id: str) -> dict | None:
        """Find repo config by repo_id."""
        for repo in self._repos:
            if repo.get("repo_id", repo.get("id")) == repo_id:
                return repo
        return None

    async def _load_and_show_diff(self, repo_id: str | None = None) -> None:
        """Run git diff and push a DiffScreen with the result."""
        project_dir = self._get_project_dir()
        base_branch = self._base_branch
        branch = self._pipeline_branch

        if repo_id is not None:
            repo_config = self._get_repo_config(repo_id)
            if repo_config:
                project_dir = repo_config.get("project_dir", project_dir)
                base_branch = repo_config.get("base_branch", base_branch)
                branch = repo_config.get("branch", branch)

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                f"{base_branch}...{branch}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_dir,
            )
            stdout, stderr = await proc.communicate()
            diff_text = (
                stdout.decode(errors="replace")
                if proc.returncode == 0
                else f"git diff failed: {stderr.decode(errors='replace')}"
            )
        except Exception as e:
            diff_text = f"Error running git diff: {e}"
        if not self.is_running:
            return
        self.app.push_screen(DiffScreen(diff_text, branch=branch))
