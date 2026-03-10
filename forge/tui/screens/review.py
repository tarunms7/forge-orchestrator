"""Review screen — diff viewer + approve/reject controls."""

from __future__ import annotations

import os
import subprocess
import tempfile

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal
from textual.widgets import Static
from textual.binding import Binding
from textual.message import Message

from forge.tui.state import TuiState
from forge.tui.widgets.task_list import TaskList
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
    #review-pane {
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
    ]

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
        with Horizontal(id="review-pane"):
            yield TaskList()
            yield DiffViewer()
        yield Static("[a] approve  [x] reject  [e] editor  [j/k] navigate", id="review-status")

    def on_mount(self) -> None:
        self._state.on_change(self._on_state_change)
        self._refresh()

    def _on_state_change(self, field: str) -> None:
        if field == "tasks":
            self._refresh()

    def _refresh(self) -> None:
        state = self._state
        reviewable = [
            state.tasks[tid] for tid in state.task_order
            if tid in state.tasks and state.tasks[tid]["state"] in _REVIEWABLE_STATES
        ]
        task_list = self.query_one(TaskList)
        task_list.update_tasks(reviewable, state.selected_task_id)

        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            diff = task.get("merge_result", {}).get("diff", "")
            if not diff:
                diff = task.get("review", {}).get("diff", "")
            self.query_one(DiffViewer).update_diff(tid, task.get("title", ""), diff)

    def on_task_list_selected(self, event: TaskList.Selected) -> None:
        self._state.selected_task_id = event.task_id
        self._refresh()

    def action_cursor_down(self) -> None:
        self.query_one(TaskList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(TaskList).action_cursor_up()

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
        task = self._state.tasks[tid]
        diff = task.get("merge_result", {}).get("diff", "")
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
