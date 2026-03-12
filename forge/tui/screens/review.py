"""Review screen — diff viewer + approve/reject controls."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.binding import Binding
from textual.message import Message

from forge.tui.state import TuiState
from forge.tui.widgets.diff_viewer import DiffViewer

_REVIEWABLE_STATES = {"in_review", "awaiting_approval"}


class ReviewAction(Message):
    """Message for approve/reject actions."""
    def __init__(self, task_id: str, action: str) -> None:
        self.task_id = task_id
        self.action = action
        super().__init__()


class ReviewScreen(Screen):
    """Review screen with inline diff viewer."""

    DEFAULT_CSS = """
    ReviewScreen {
        layout: vertical;
    }
    #review-header {
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #a371f7;
    }
    DiffViewer {
        height: 1fr;
    }
    #review-status {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("x", "reject", "Reject"),
        Binding("e", "edit", "Open in $EDITOR"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("escape", "app.pop_screen", "Back", show=True),
        # Task jump — priority=True prevents bubble to app-level screen switch
        Binding("1", "jump_task(1)", show=False, priority=True),
        Binding("2", "jump_task(2)", show=False, priority=True),
        Binding("3", "jump_task(3)", show=False, priority=True),
        Binding("4", "jump_task(4)", show=False, priority=True),
        Binding("5", "jump_task(5)", show=False, priority=True),
        Binding("6", "jump_task(6)", show=False, priority=True),
        Binding("7", "jump_task(7)", show=False, priority=True),
        Binding("8", "jump_task(8)", show=False, priority=True),
        Binding("9", "jump_task(9)", show=False, priority=True),
    ]

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state
        self._diff_cache: dict[str, str] = {}
        self._diff_loading: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
        yield DiffViewer()
        yield Static("[a] approve  [x] reject  [e] editor  [j/k] scroll  [1-9] jump task", id="review-status")

    def on_mount(self) -> None:
        self._state.on_change(self._on_state_change)
        self._refresh()

    def _on_state_change(self, field: str) -> None:
        if field == "tasks":
            self._refresh()

    def _refresh(self) -> None:
        state = self._state
        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            diff = self._diff_cache.get(tid, "")
            if not diff and tid not in self._diff_loading:
                self._diff_loading.add(tid)
                asyncio.create_task(self._load_diff(tid))
                diff = "Loading diff..."
            self.query_one(DiffViewer).update_diff(tid, task.get("title", ""), diff)

    def action_cursor_down(self) -> None:
        self.query_one(DiffViewer).scroll_relative(y=3)

    def action_cursor_up(self) -> None:
        self.query_one(DiffViewer).scroll_relative(y=-3)

    def action_approve(self) -> None:
        tid = self._state.selected_task_id
        if tid:
            self.post_message(ReviewAction(tid, "approve"))

    def action_reject(self) -> None:
        tid = self._state.selected_task_id
        if tid:
            self.post_message(ReviewAction(tid, "reject"))

    def action_edit(self) -> None:
        tid = self._state.selected_task_id
        if not tid or tid not in self._state.tasks:
            return
        diff = self._diff_cache.get(tid, "")
        if not diff:
            return
        editor = os.environ.get("EDITOR", "vim")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
            f.write(diff)
            f.flush()
            tmp_path = f.name
        try:
            self.app.suspend()
            subprocess.run([editor, tmp_path])
            self.app.resume()
        finally:
            os.unlink(tmp_path)

    def action_jump_task(self, index: int) -> None:
        """Jump to the Nth reviewable task (1-based)."""
        reviewable = [
            tid for tid in self._state.task_order
            if tid in self._state.tasks
            and self._state.tasks[tid]["state"] in _REVIEWABLE_STATES
        ]
        if 0 < index <= len(reviewable):
            self._state.selected_task_id = reviewable[index - 1]
            self._refresh()

    async def _resolve_branch(self) -> str:
        """Resolve the pipeline branch — from state or git."""
        branch = self._state.pipeline_branch or ""
        if branch:
            return branch
        # Fallback: detect current branch from git
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                name = stdout.decode().strip()
                if name and name not in ("main", "master", "HEAD"):
                    self._state.pipeline_branch = name
                    return name
        except Exception:
            pass
        return ""

    async def _load_diff(self, tid: str) -> None:
        """Load diff on-demand from git."""
        branch = await self._resolve_branch()
        if not branch:
            diff = "No pipeline branch available — run 'forge doctor' to check git setup."
        else:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "diff", f"main...{branch}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                diff = stdout.decode(errors="replace") if proc.returncode == 0 else f"git diff failed: {stderr.decode(errors='replace')}"
            except Exception as e:
                diff = f"Error: {e}"
        self._diff_cache[tid] = diff
        self._diff_loading.discard(tid)
        # Guard: only update if user is still on this task
        if self._state.selected_task_id != tid:
            return
        try:
            self.query_one(DiffViewer).update_diff(
                tid, self._state.tasks.get(tid, {}).get("title", ""), diff,
            )
        except Exception:
            pass
