"""Review screen — diff viewer + approve/reject controls."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static

from forge.core.async_utils import safe_create_task
from forge.tui.state import TuiState
from forge.tui.widgets.diff_viewer import DiffViewer
from forge.tui.widgets.search_overlay import SearchOverlay
from forge.tui.widgets.shortcut_bar import ShortcutBar

logger = logging.getLogger("forge.tui.screens.review")

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
        Binding("slash", "toggle_search", "Search", show=False),
        Binding("n", "search_next", "Next match", show=False),
        Binding("N", "search_prev", "Prev match", show=False),
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
        self._diff_loaded = False

    def compose(self) -> ComposeResult:
        yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
        yield DiffViewer()
        yield SearchOverlay()
        yield Static(
            "[a] approve  [x] reject  [e] editor  [j/k] scroll  [/] search  [1-9] jump task",
            id="review-status",
        )
        yield ShortcutBar([("Esc", "Back")])

    def _update_shortcut_bar(self) -> None:
        """Update shortcut bar based on diff load state."""
        if self._diff_loaded:
            shortcuts: list[tuple[str, str]] = [
                ("a", "Approve"),
                ("x", "Reject"),
                ("e", "Editor"),
                ("/", "Search"),
                ("Esc", "Back"),
            ]
        else:
            shortcuts = [("Esc", "Back")]
        try:
            bar = self.query_one(ShortcutBar)
            bar.update_shortcuts(shortcuts)
        except Exception:
            pass

    def on_mount(self) -> None:
        self._state.on_change(self._on_state_change)
        self._refresh()

    def on_unmount(self) -> None:
        self._state.remove_change_callback(self._on_state_change)

    def _on_state_change(self, field: str) -> None:
        if field in ("tasks", "task_diffs"):
            self._refresh()

    def _refresh(self) -> None:
        state = self._state
        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            # Always prefer daemon-computed diff (from worktree, always accurate)
            diff = state.task_diffs.get(tid, "")
            if diff:
                # Update cache with fresh daemon diff (replaces any stale error)
                self._diff_cache[tid] = diff
            elif tid in self._diff_cache:
                cached = self._diff_cache[tid]
                # Don't use cached error messages — they may be stale
                if cached.startswith(("No pipeline", "git diff failed", "Error")):
                    diff = ""  # Force reload below
                else:
                    diff = cached
            if not diff and tid not in self._diff_loading:
                self._diff_loading.add(tid)
                safe_create_task(self._load_diff(tid), logger=logger, name="load-diff")
                diff = "\u23f3 Loading diff..."
            was_loaded = self._diff_loaded
            self._diff_loaded = bool(diff and not diff.startswith("\u23f3"))
            if self._diff_loaded != was_loaded:
                self._update_shortcut_bar()
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
            tid
            for tid in self._state.task_order
            if tid in self._state.tasks and self._state.tasks[tid]["state"] in _REVIEWABLE_STATES
        ]
        if 0 < index <= len(reviewable):
            self._state.selected_task_id = reviewable[index - 1]
            self._refresh()

    def _get_project_dir(self) -> str | None:
        """Get the project directory from the app if available."""
        try:
            return getattr(self.app, "_project_dir", None) or os.getcwd()
        except Exception:
            return None

    async def _resolve_branch(self) -> str:
        """Resolve the pipeline branch — from state or git."""
        branch = self._state.pipeline_branch or ""
        if branch:
            return branch
        # Fallback: detect current branch from git
        cwd = self._get_project_dir()
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
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
        """Load diff on-demand from git, scoped to task files when available."""
        # Retry branch resolution — daemon may still be computing it
        branch = ""
        for _attempt in range(3):
            branch = await self._resolve_branch()
            if branch:
                break
            await asyncio.sleep(1.0)
        if not branch:
            diff = "No pipeline branch available yet — waiting for execution to start.\nPress Esc to go back, then try again with [bold]r[/bold]."
        else:
            cwd = self._get_project_dir()
            try:
                # Scope diff to task's changed files when available
                base = getattr(self._state, "base_branch", "main") or "main"
                cmd: list[str] = ["git", "diff", f"{base}...{branch}"]
                task = self._state.tasks.get(tid, {})
                files = task.get("files_changed", [])
                if files:
                    cmd.append("--")
                    cmd.extend(files)
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                stdout, stderr = await proc.communicate()
                diff = (
                    stdout.decode(errors="replace")
                    if proc.returncode == 0
                    else f"git diff failed: {stderr.decode(errors='replace')}"
                )
            except Exception as e:
                diff = f"Error: {e}"
        self._diff_cache[tid] = diff
        self._diff_loading.discard(tid)
        # Guard: only update if user is still on this task
        if self._state.selected_task_id != tid:
            return
        self._diff_loaded = True
        self._update_shortcut_bar()
        try:
            self.query_one(DiffViewer).update_diff(
                tid,
                self._state.tasks.get(tid, {}).get("title", ""),
                diff,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def action_toggle_search(self) -> None:
        """Show the search overlay."""
        try:
            self.query_one(SearchOverlay).show()
        except Exception:
            pass

    def action_search_next(self) -> None:
        """Navigate to next search match."""
        try:
            self.query_one(SearchOverlay).navigate(+1)
        except Exception:
            pass

    def action_search_prev(self) -> None:
        """Navigate to previous search match."""
        try:
            self.query_one(SearchOverlay).navigate(-1)
        except Exception:
            pass

    def on_search_overlay_search_changed(self, event: SearchOverlay.SearchChanged) -> None:
        """Apply search highlights to diff viewer."""
        count = 0
        try:
            count = self.query_one(DiffViewer).set_search_highlights(event.pattern)
        except Exception:
            pass
        try:
            self.query_one(SearchOverlay).update_match_count(count)
        except Exception:
            pass

    def on_search_overlay_search_dismissed(self, event: SearchOverlay.SearchDismissed) -> None:
        """Clear highlights when search is fully dismissed."""
        try:
            self.query_one(DiffViewer).set_search_highlights(None)
        except Exception:
            pass
