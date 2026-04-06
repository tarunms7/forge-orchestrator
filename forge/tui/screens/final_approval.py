"""Final approval screen — shows summary before PR creation."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict

from textual import events
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static

from forge.core.async_utils import safe_create_task
from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_CYAN,
    ACCENT_GOLD,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    TEXT_MUTED,
    TEXT_SECONDARY,
)
from forge.tui.widgets.diff_viewer import DiffViewer
from forge.tui.widgets.followup_input import FollowUpInput, FollowUpTextArea
from forge.tui.widgets.shortcut_bar import ShortcutBar

logger = logging.getLogger("forge.tui.screens.final_approval")


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in dynamic screen content."""
    if text is None:
        return ""
    return str(text).replace("[", "\\[").replace("]", "\\]")


def _summarize_task_states(tasks: list[dict]) -> dict[str, int]:
    summary = {
        "done": 0,
        "error": 0,
        "blocked": 0,
        "active": 0,
        "cancelled": 0,
    }
    for task in tasks:
        state = task.get("state", task.get("review", "todo"))
        if state == "done":
            summary["done"] += 1
        elif state == "error":
            summary["error"] += 1
        elif state == "blocked":
            summary["blocked"] += 1
        elif state == "cancelled":
            summary["cancelled"] += 1
        else:
            summary["active"] += 1
    return summary


def _format_launch_banner(
    tasks: list[dict],
    pipeline_branch: str,
    base_branch: str,
    *,
    partial: bool = False,
    multi_repo: bool = False,
    pr_created: bool = False,
    pr_count: int = 0,
    stats: dict | None = None,
) -> str:
    stats = stats or {}
    counts = _summarize_task_states(tasks)
    title = "RECOVERY BAY" if partial else "LAUNCH BAY"
    if pr_created:
        subtitle = (
            f"[bold {ACCENT_GREEN}]{pr_count} pull request{'s' if pr_count != 1 else ''} live[/]"
            if pr_count
            else f"[bold {ACCENT_GREEN}]Pull request live[/]"
        )
    else:
        subtitle = (
            f"[bold {ACCENT_YELLOW}]Completed work can ship now[/]"
            if partial
            else f"[bold {ACCENT_GREEN}]Ready to open the pull request[/]"
        )
    progress_parts = [f"{counts['done']} shipped"]
    if counts["active"]:
        progress_parts.append(f"{counts['active']} active")
    if counts["error"]:
        progress_parts.append(f"{counts['error']} failed")
    if counts["blocked"]:
        progress_parts.append(f"{counts['blocked']} blocked")
    if counts["cancelled"]:
        progress_parts.append(f"{counts['cancelled']} cancelled")
    if multi_repo and stats.get("repo_count"):
        progress_parts.append(f"{stats['repo_count']} repos coordinated")
    if multi_repo:
        target = (
            f"[{TEXT_SECONDARY}]Launch opens one pull request per repo into its configured "
            "base branch.[/]"
        )
    elif pipeline_branch:
        target = (
            f"[{TEXT_SECONDARY}]PR target:[/] [bold {ACCENT_BLUE}]{_escape(pipeline_branch)}[/] "
            f"[{TEXT_SECONDARY}]→[/] [bold {ACCENT_BLUE}]{_escape(base_branch)}[/]"
        )
    else:
        target = f"[{TEXT_MUTED}]Branch target loading…[/]"
    return "\n".join(
        [
            f"[bold {ACCENT_GOLD}]{title}[/]",
            subtitle,
            f"[{TEXT_SECONDARY}]{'  •  '.join(progress_parts)}[/]",
            target,
        ]
    )


def _format_launch_status(
    pipeline_branch: str,
    base_branch: str,
    *,
    multi_repo: bool = False,
    per_repo_pr_urls: dict[str, str] | None = None,
) -> str:
    per_repo_pr_urls = per_repo_pr_urls or {}
    if per_repo_pr_urls:
        lines = [f"[bold {ACCENT_GREEN}]PR live[/]"]
        for rid, url in sorted(per_repo_pr_urls.items()):
            lines.append(
                f"[bold {ACCENT_GREEN}]{_escape(rid)}:[/] "
                f"[underline {ACCENT_BLUE}]{_escape(url)}[/]"
            )
        return "\n".join(lines)
    if multi_repo:
        return (
            f"[bold {ACCENT_CYAN}]Launch plan[/]\n"
            f"[{TEXT_SECONDARY}]Press Enter to push the prepared repo branches and open the PRs.[/]"
        )
    if not pipeline_branch:
        return f"[{TEXT_MUTED}]Branch target loading…[/]"
    return (
        f"[bold {ACCENT_CYAN}]Launch plan[/]\n"
        f"[{TEXT_SECONDARY}]Forge will open a pull request from "
        f"[bold {ACCENT_BLUE}]{_escape(pipeline_branch)}[/] into "
        f"[bold {ACCENT_BLUE}]{_escape(base_branch)}[/].[/]"
    )


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
        title = _escape(t.get("title", "?"))
        state = str(t.get("state", t.get("review", "?")))
        escaped_state = _escape(state)

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
            error = _escape(t.get("error", "failed"))
            lines.append(f"{indent}[{ACCENT_RED}]❌[/] [bold]{title}[/]  [{ACCENT_RED}]{error}[/]")
        elif state == "blocked":
            error = _escape(t.get("error", "blocked by dependency"))
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
                        f"{indent}[{ACCENT_YELLOW}]⏳[/] [bold]{title}[/]  "
                        f"[{TEXT_SECONDARY}]{escaped_state}  {stats}[/]"
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
                    f"{indent}[{TEXT_SECONDARY}]○[/] [bold]{title}[/]  "
                    f"[{TEXT_SECONDARY}]{escaped_state}  {stats}[/]"
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
        lines.append(
            f"[bold {ACCENT_BLUE}]{_escape(repo_id)}[/]  +{total_added}/-{total_removed}"
        )
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
                    f"{marker}[bold]{_escape(repo_id)}[/] "
                    f"[{TEXT_SECONDARY}]+{total_added}/-{total_removed}[/]",
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
                    f"{marker}[bold]{_escape(repo_id)}[/] "
                    f"[{TEXT_SECONDARY}]+{total_added}/-{total_removed}[/]"
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

    class Rerun(Message):
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
    FinalApprovalScreen {
        align: center top;
    }
    #approval-scroll {
        width: 100%;
        height: 1fr;
    }
    #approval-container {
        width: 1fr;
        max-width: 116;
        padding: 1 2 2 2;
    }
    #launch-banner,
    #stats,
    #launch-status,
    #task-table {
        background: #11161d;
        border: tall #263041;
        padding: 1 2;
    }
    #launch-banner {
        margin-bottom: 1;
    }
    #behind-main-warning {
        margin: 0 0 1 0;
    }
    #stats {
        margin-top: 1;
    }
    #launch-status {
        margin-top: 1;
    }
    #tasks-header {
        margin: 1 0 0 0;
        color: #d6a85f;
    }
    #task-table {
        margin-top: 1;
    }
    #approval-container FollowUpInput {
        margin-top: 1;
    }
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
        self._pr_created = False
        self._single_pr_url = ""

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Dynamically enable/disable actions based on partial mode."""
        if action == "create_pr":
            return not self._pr_created
        if action in ("rerun", "skip_failed"):
            return self._partial  # only available in partial mode
        if action == "new_task":
            return not self._partial  # only available in full mode
        return True

    def on_mount(self) -> None:
        safe_create_task(self._check_behind_main(), logger=logger, name="check-behind-main")
        # Keep screen-level shortcuts active by default; follow-up input is opt-in via "f".
        self.focus()
        self._update_shortcut_bar()

    async def _check_behind_main(self) -> None:
        """Check if pipeline branch is behind the base branch and show warning."""
        project_dir = self._get_project_dir()
        if self._multi_repo or not project_dir or not self._pipeline_branch:
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
                    f"behind {_escape(base)}. Sync before launch to avoid merge conflicts.[/]"
                )
        except Exception:
            pass  # Non-critical — silently skip if git fails

    def compose(self):
        files_count = self._stats.get("files", 0)
        pr_label = "Create PRs" if self._multi_repo else "Create PR"
        with VerticalScroll(id="approval-scroll"):
            with Center():
                with Vertical(id="approval-container"):
                    yield Static(
                        self._render_launch_banner(),
                        id="launch-banner",
                    )
                    yield Static("", id="behind-main-warning")  # populated by _check_behind_main
                    yield Static(
                        self._render_launch_status(),
                        id="launch-status",
                    )
                    yield Static(
                        f"[bold {ACCENT_GOLD}]OUTCOME SUMMARY[/]\n"
                        f"{format_summary_stats(self._stats, multi_repo=self._multi_repo)}",
                        id="stats",
                    )
                    yield Static("[bold]Task outcomes[/]", id="tasks-header")
                    yield Static(
                        format_task_table(self._tasks, multi_repo=self._multi_repo), id="task-table"
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

    def _pr_count(self) -> int:
        if self._per_repo_pr_urls:
            return len(self._per_repo_pr_urls)
        return 1 if self._single_pr_url else 0

    def _render_launch_banner(self) -> str:
        return _format_launch_banner(
            self._tasks,
            self._pipeline_branch,
            self._base_branch,
            partial=self._partial,
            multi_repo=self._multi_repo,
            pr_created=self._pr_created,
            pr_count=self._pr_count(),
            stats=self._stats,
        )

    def _render_launch_status(self) -> str:
        if self._single_pr_url:
            return (
                f"[bold {ACCENT_GREEN}]PR live[/]\n"
                f"[{TEXT_SECONDARY}]Review, merge, or keep iterating on the branch.[/]\n"
                f"[underline {ACCENT_BLUE}]{_escape(self._single_pr_url)}[/]"
            )
        return _format_launch_status(
            self._pipeline_branch,
            self._base_branch,
            multi_repo=self._multi_repo,
            per_repo_pr_urls=self._per_repo_pr_urls,
        )

    def _refresh_launch_widgets(self) -> None:
        try:
            self.query_one("#launch-banner", Static).update(self._render_launch_banner())
        except Exception:
            pass
        try:
            self.query_one("#launch-status", Static).update(self._render_launch_status())
        except Exception:
            pass

    def _update_shortcut_bar(self) -> None:
        """Update shortcut bar based on current mode and PR state."""
        if isinstance(self.focused, FollowUpTextArea):
            shortcuts = [
                ("Enter", "Submit follow-up"),
                ("Ctrl+S", "Submit follow-up"),
                ("Ctrl+U", "Clear"),
                ("Esc", "Back to actions"),
            ]
        elif self._pr_created:
            shortcuts: list[tuple[str, str]] = [
                ("d", "Diff"),
                ("f", "Follow-up"),
                ("n", "New Task"),
                ("Esc", "Done"),
            ]
        elif self._partial:
            shortcuts = [
                ("Enter", "Create PR"),
                ("d", "Diff"),
                ("r", "Retry Failed"),
                ("s", "Skip & Finish"),
                ("f", "Follow-up"),
                ("Esc", "Back"),
            ]
        else:
            shortcuts = [
                ("Enter", "Create PR"),
                ("d", "Diff"),
                ("f", "Follow-up"),
                ("n", "New Task"),
                ("Esc", "Back"),
            ]
        try:
            bar = self.query_one(ShortcutBar)
            bar.update_shortcuts(shortcuts)
        except Exception:
            pass

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._update_shortcut_bar()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self.call_after_refresh(self._update_shortcut_bar)

    def on_focus(self, event: events.Focus) -> None:
        self.call_after_refresh(self._update_shortcut_bar)

    def show_pr_url(self, url: str, repo_id: str | None = None) -> None:
        """Display PR URL(s) inline, with optional per-repo labeling."""
        self._pr_created = True
        if repo_id is not None:
            self._per_repo_pr_urls[repo_id] = url
        else:
            self._single_pr_url = url
        self._refresh_launch_widgets()
        self._update_shortcut_bar()

    def show_pipeline_target(self, branch: str, base_branch: str) -> None:
        """Update branch target info after the screen is already mounted."""
        self._pipeline_branch = branch
        self._base_branch = base_branch
        self._refresh_launch_widgets()
        if not self._multi_repo and self.is_running:
            safe_create_task(self._check_behind_main(), logger=logger, name="refresh-behind-main")

    def action_new_task(self) -> None:
        """Return to HomeScreen for a new task, cleaning up pipeline state."""
        self.app.action_reset_for_new_task()

    def action_create_pr(self) -> None:
        if isinstance(self.focused, FollowUpTextArea):
            self.action_submit_followup()
            return
        self.post_message(self.CreatePR())

    def action_rerun(self) -> None:
        self.post_message(self.Rerun())

    def action_skip_failed(self) -> None:
        """Skip all failed tasks and finish the pipeline."""
        self.post_message(self.SkipFailed())

    def action_focus_followup(self) -> None:
        """Focus the follow-up input area."""
        try:
            followup = self.query_one(FollowUpInput)
            followup.focus_input()
            self._update_shortcut_bar()
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
