"""Plan approval screen — interactive plan editor for reviewing and modifying tasks."""

from __future__ import annotations

import copy
import uuid

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static, TextArea

from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    ACCENT_RED,
    ACCENT_YELLOW,
    TEXT_MUTED,
    TEXT_SECONDARY,
)
from forge.tui.widgets.shortcut_bar import ShortcutBar

_COMPLEXITY_COLORS = {
    "low": ACCENT_GREEN,
    "medium": ACCENT_YELLOW,
    "high": ACCENT_RED,
}

_COMPLEXITY_ORDER = ["low", "medium", "high"]


def format_plan_task(task: dict, index: int) -> str:
    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    files = task.get("files", [])
    complexity = task.get("complexity", "medium")
    deps = task.get("depends_on", [])
    color = _COMPLEXITY_COLORS.get(complexity, TEXT_SECONDARY)

    lines = [f"  [bold {ACCENT_BLUE}]{index}. {title}[/]  [{color}]{complexity}[/]"]
    if desc:
        lines.append(f"     [{TEXT_SECONDARY}]{desc[:120]}[/]")
    if files:
        file_str = ", ".join(files[:5])
        if len(files) > 5:
            file_str += f" +{len(files) - 5} more"
        lines.append(f"     [{TEXT_SECONDARY}]Files:[/] {file_str}")
    if deps:
        lines.append(f"     [{TEXT_SECONDARY}]Depends on:[/] {', '.join(deps)}")
    return "\n".join(lines)


def format_plan_summary(tasks: list[dict], estimated_cost: float = 0.0) -> str:
    count = len(tasks)
    complexities = {"low": 0, "medium": 0, "high": 0}
    for t in tasks:
        c = t.get("complexity", "medium")
        complexities[c] = complexities.get(c, 0) + 1

    task_word = "task" if count == 1 else "tasks"
    parts = [f"[bold]{count} {task_word}[/]"]
    for level, n in complexities.items():
        if n > 0:
            color = _COMPLEXITY_COLORS.get(level, TEXT_SECONDARY)
            parts.append(f"[{color}]{n} {level}[/]")
    if estimated_cost > 0:
        parts.append(f"[{ACCENT_GREEN}]~${estimated_cost:.2f}[/]")
    return " · ".join(parts)


def format_cost_estimate(cost_estimate: dict | None) -> str | None:
    """Format a cost estimate dict into a display string, or None if no estimate."""
    if not cost_estimate:
        return None
    min_usd = cost_estimate.get("min_usd")
    max_usd = cost_estimate.get("max_usd")
    if min_usd is not None and max_usd is not None:
        return f"[{ACCENT_YELLOW}]💰 Estimated cost: ${min_usd:.2f} – ${max_usd:.2f}[/]"
    legacy = cost_estimate.get("estimated_cost")
    if legacy is not None:
        return f"[{ACCENT_YELLOW}]💰 Estimated cost: ~${legacy:.2f}[/]"
    return None


def _format_task_line(task: dict, index: int, selected: bool, modified: bool, removed: bool) -> str:
    """Format a single task line for the interactive list."""
    if removed:
        title = task.get("title", "Untitled")
        return f"  [{TEXT_MUTED} strike]{index}. {title}[/]  [{TEXT_MUTED}][removed — press z to undo][/]"

    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    files = task.get("files", [])
    complexity = task.get("complexity", "medium")
    deps = task.get("depends_on", [])
    notes = task.get("agent_notes", "")
    color = _COMPLEXITY_COLORS.get(complexity, TEXT_SECONDARY)

    marker = "▶ " if selected else "  "
    mod_indicator = f" [{ACCENT_YELLOW}]●[/]" if modified else ""

    lines = [
        f"{marker}[bold {ACCENT_BLUE}]{index}. {title}[/]  [{color}]{complexity}[/]{mod_indicator}"
    ]
    if desc:
        lines.append(f"     [{TEXT_SECONDARY}]{desc[:120]}[/]")
    if files:
        file_str = ", ".join(files[:5])
        if len(files) > 5:
            file_str += f" +{len(files) - 5} more"
        lines.append(f"     [{TEXT_SECONDARY}]Files:[/] {file_str}")
    if deps:
        lines.append(f"     [{TEXT_SECONDARY}]Depends on:[/] {', '.join(deps)}")
    if notes:
        lines.append(f"     [{ACCENT_PURPLE}]📝 Note:[/] {notes[:100]}")
    return "\n".join(lines)


class PlanApprovalScreen(Screen):
    """Interactive plan editor — review, edit, reorder, and approve tasks."""

    DEFAULT_CSS = """
    PlanApprovalScreen {
        layout: vertical;
    }
    #plan-header {
        height: 3;
        padding: 1 2;
        background: #11161d;
        color: #58a6ff;
        border-bottom: tall #263041;
    }
    #plan-cost {
        padding: 0 2;
        background: #11161d;
        height: 1;
    }
    #plan-body {
        padding: 1 2;
        background: #0d1117;
    }
    #plan-footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #11161d;
        color: #8b949e;
        border-top: tall #263041;
    }
    #edit-area {
        height: 6;
        margin: 0 2;
        border: tall #d29922;
        background: #0d1117;
        display: none;
    }
    #edit-area.visible {
        display: block;
    }
    #edit-label {
        height: 1;
        margin: 0 2;
        color: #d29922;
        display: none;
    }
    #edit-label.visible {
        display: block;
    }
    #add-form {
        height: auto;
        margin: 1 2;
        border: tall #3fb950;
        background: #11161d;
        padding: 1;
        display: none;
    }
    #add-form.visible {
        display: block;
    }
    .add-field {
        height: 1;
        margin: 0 0;
    }
    .add-input {
        height: 3;
        margin: 0 0;
    }
    """

    BINDINGS = [
        Binding("enter", "approve", "Approve & Execute", show=True, priority=True),
        Binding("escape", "cancel_or_close", "Save & Exit", show=True, priority=True),
        Binding("j", "cursor_down", "Next task", show=False),
        Binding("k", "cursor_up", "Prev task", show=False),
        Binding("e", "edit_task", "Edit description", show=False),
        Binding("f", "edit_files", "Edit files", show=False),
        Binding("x", "remove_task", "Remove task", show=False),
        Binding("z", "undo_remove", "Undo remove", show=False),
        Binding("a", "add_task", "Add task", show=False),
        Binding("J", "move_down", "Move down", show=False),
        Binding("K", "move_up", "Move up", show=False),
        Binding("c", "cycle_complexity", "Cycle complexity", show=False),
        Binding("n", "add_note", "Add note", show=False),
    ]

    class PlanApproved(Message):
        """User approved the plan with (possibly edited) tasks."""

        def __init__(self, tasks: list[dict] | None = None) -> None:
            self.tasks = tasks
            super().__init__()

    class PlanCancelled(Message):
        """User exited plan review and wants to continue later."""

        def __init__(self, tasks: list[dict] | None = None) -> None:
            self.tasks = tasks
            super().__init__()

    def __init__(
        self,
        tasks: list[dict],
        estimated_cost: float = 0.0,
        cost_estimate: dict | None = None,
    ) -> None:
        super().__init__()
        self._tasks = [copy.deepcopy(t) for t in tasks]
        self._original_tasks = [copy.deepcopy(t) for t in tasks]
        self._estimated_cost = estimated_cost
        self._cost_estimate = cost_estimate
        self._cursor = 0
        self._removed: set[int] = set()
        self._modified: set[int] = set()
        self._editing: str | None = None  # "description", "files", "note", or None
        self._adding: bool = False

    @property
    def _active_tasks(self) -> list[dict]:
        """Return non-removed tasks."""
        return [t for i, t in enumerate(self._tasks) if i not in self._removed]

    def compose(self) -> ComposeResult:
        summary = format_plan_summary(self._active_tasks, self._estimated_cost)
        yield Static(f"[bold {ACCENT_BLUE}]PLAN REVIEW[/]  {summary}", id="plan-header")
        cost_line = format_cost_estimate(self._cost_estimate)
        if cost_line is not None:
            yield Static(cost_line, id="plan-cost")
        yield Static("", id="edit-label")
        yield TextArea(id="edit-area")
        with VerticalScroll(id="plan-body"):
            for i, task in enumerate(self._tasks):
                selected = i == self._cursor
                modified = i in self._modified
                removed = i in self._removed
                yield Static(
                    _format_task_line(task, i + 1, selected, modified, removed),
                    id=f"task-{i}",
                )
                yield Static("")
        yield Static(
            "[Enter] approve  [e] edit  [f] files  [x] remove  [a] add  [J/K] reorder  [c] complexity  [n] note  [Esc] save & exit",
            id="plan-footer",
        )
        yield ShortcutBar(
            [
                ("Enter", "Approve"),
                ("e", "Edit"),
                ("f", "Files"),
                ("x", "Remove"),
                ("a", "Add"),
                ("J/K", "Reorder"),
                ("Esc", "Save & Exit"),
            ]
        )

    def _refresh_task_list(self) -> None:
        """Re-render all task widgets and the header summary."""
        for i, task in enumerate(self._tasks):
            widget = self.query_one(f"#task-{i}", Static)
            selected = i == self._cursor
            modified = i in self._modified
            removed = i in self._removed
            widget.update(_format_task_line(task, i + 1, selected, modified, removed))

        summary = format_plan_summary(self._active_tasks, self._estimated_cost)
        self.query_one("#plan-header", Static).update(
            f"[bold {ACCENT_BLUE}]PLAN REVIEW[/]  {summary}"
        )

    def _clamp_cursor(self) -> None:
        """Ensure cursor is within bounds."""
        if not self._tasks:
            self._cursor = 0
            return
        if self._cursor < 0:
            self._cursor = 0
        if self._cursor >= len(self._tasks):
            self._cursor = len(self._tasks) - 1

    def _is_editing(self) -> bool:
        """Check if currently in an editing mode."""
        return self._editing is not None or self._adding

    def action_cursor_down(self) -> None:
        if self._is_editing():
            return
        if self._cursor < len(self._tasks) - 1:
            self._cursor += 1
            self._refresh_task_list()

    def action_cursor_up(self) -> None:
        if self._is_editing():
            return
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh_task_list()

    def action_edit_task(self) -> None:
        """Open inline editor for task title + description."""
        if self._is_editing() or self._cursor in self._removed:
            return
        task = self._tasks[self._cursor]
        self._editing = "description"
        label = self.query_one("#edit-label", Static)
        label.update(
            f"[{ACCENT_YELLOW}]Editing task {self._cursor + 1} — title | description (Ctrl+S to save, Esc to cancel)[/]"
        )
        label.add_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.text = f"{task.get('title', '')}\n{task.get('description', '')}"
        area.add_class("visible")
        area.focus()
        self._update_shortcut_bar()

    def action_edit_files(self) -> None:
        """Open inline editor for comma-separated file list."""
        if self._is_editing() or self._cursor in self._removed:
            return
        task = self._tasks[self._cursor]
        self._editing = "files"
        label = self.query_one("#edit-label", Static)
        label.update(
            f"[{ACCENT_YELLOW}]Editing files for task {self._cursor + 1} — comma-separated (Ctrl+S to save, Esc to cancel)[/]"
        )
        label.add_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.text = ", ".join(task.get("files", []))
        area.add_class("visible")
        area.focus()
        self._update_shortcut_bar()

    def action_add_note(self) -> None:
        """Add a context note for the agent."""
        if self._is_editing() or self._cursor in self._removed:
            return
        task = self._tasks[self._cursor]
        self._editing = "note"
        label = self.query_one("#edit-label", Static)
        label.update(
            f"[{ACCENT_YELLOW}]Agent note for task {self._cursor + 1} (Ctrl+S to save, Esc to cancel)[/]"
        )
        label.add_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.text = task.get("agent_notes", "")
        area.add_class("visible")
        area.focus()
        self._update_shortcut_bar()

    def _save_edit(self) -> None:
        """Save the current edit from the TextArea."""
        area = self.query_one("#edit-area", TextArea)
        text = area.text

        if self._editing == "description":
            lines = text.split("\n", 1)
            self._tasks[self._cursor]["title"] = lines[0].strip()
            self._tasks[self._cursor]["description"] = lines[1].strip() if len(lines) > 1 else ""
            self._modified.add(self._cursor)
        elif self._editing == "files":
            files = [f.strip() for f in text.split(",") if f.strip()]
            self._tasks[self._cursor]["files"] = files
            self._modified.add(self._cursor)
        elif self._editing == "note":
            self._tasks[self._cursor]["agent_notes"] = text.strip()
            self._modified.add(self._cursor)

        self._close_editor()

    def _update_shortcut_bar(self) -> None:
        """Update shortcut bar based on whether we're in edit mode."""
        if self._is_editing():
            shortcuts: list[tuple[str, str]] = [
                ("Ctrl+S", "Save"),
                ("Esc", "Cancel Edit"),
            ]
        else:
            shortcuts = [
                ("Enter", "Approve"),
                ("e", "Edit"),
                ("f", "Files"),
                ("x", "Remove"),
                ("a", "Add"),
                ("J/K", "Reorder"),
                ("Esc", "Save & Exit"),
            ]
        try:
            bar = self.query_one(ShortcutBar)
            bar.update_shortcuts(shortcuts)
        except Exception:
            pass

    def _close_editor(self) -> None:
        """Close the edit area and return to task list navigation."""
        self._editing = None
        label = self.query_one("#edit-label", Static)
        label.remove_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.remove_class("visible")
        self._refresh_task_list()
        self._update_shortcut_bar()

    def action_remove_task(self) -> None:
        """Mark the current task as removed."""
        if self._is_editing():
            return
        if not self._tasks:
            return
        if self._cursor in self._removed:
            return
        # Don't allow removing all tasks
        active_count = len(self._tasks) - len(self._removed)
        if active_count <= 1:
            self.app.notify("Cannot remove the last task", severity="warning")
            return
        self._removed.add(self._cursor)
        self._modified.add(self._cursor)
        self._refresh_task_list()

    def action_undo_remove(self) -> None:
        """Undo removal of the current task."""
        if self._is_editing():
            return
        if self._cursor in self._removed:
            self._removed.discard(self._cursor)
            self._refresh_task_list()

    def action_add_task(self) -> None:
        """Add a new task at the end of the list."""
        if self._is_editing():
            return
        new_task = {
            "id": f"task-{uuid.uuid4().hex[:8]}",
            "title": "New task",
            "description": "",
            "files": [],
            "complexity": "medium",
            "depends_on": [],
        }
        new_index = len(self._tasks)
        self._tasks.append(new_task)
        self._modified.add(new_index)

        # Add new Static widgets in the plan-body
        body = self.query_one("#plan-body", VerticalScroll)
        task_widget = Static(
            _format_task_line(new_task, new_index + 1, False, True, False),
            id=f"task-{new_index}",
        )
        body.mount(task_widget)
        body.mount(Static(""))

        self._cursor = new_index
        self._refresh_task_list()

        # Immediately open editor for the new task
        self.action_edit_task()

    def action_move_up(self) -> None:
        """Move the current task up (swap with previous)."""
        if self._is_editing():
            return
        if self._cursor <= 0:
            return
        prev = self._cursor - 1
        self._swap_tasks(prev, self._cursor)
        self._cursor = prev
        self._refresh_task_list()

    def action_move_down(self) -> None:
        """Move the current task down (swap with next)."""
        if self._is_editing():
            return
        if self._cursor >= len(self._tasks) - 1:
            return
        nxt = self._cursor + 1
        self._swap_tasks(self._cursor, nxt)
        self._cursor = nxt
        self._refresh_task_list()

    def _swap_tasks(self, i: int, j: int) -> None:
        """Swap two tasks and update their modification/removal tracking."""
        self._tasks[i], self._tasks[j] = self._tasks[j], self._tasks[i]

        # Update tracking sets
        new_removed: set[int] = set()
        for idx in self._removed:
            if idx == i:
                new_removed.add(j)
            elif idx == j:
                new_removed.add(i)
            else:
                new_removed.add(idx)
        self._removed = new_removed

        new_modified: set[int] = set()
        for idx in self._modified:
            if idx == i:
                new_modified.add(j)
            elif idx == j:
                new_modified.add(i)
            else:
                new_modified.add(idx)
        # Both swapped positions are modified
        new_modified.add(i)
        new_modified.add(j)
        self._modified = new_modified

    def action_cycle_complexity(self) -> None:
        """Cycle complexity: low → medium → high → low."""
        if self._is_editing() or self._cursor in self._removed:
            return
        if not self._tasks:
            return
        task = self._tasks[self._cursor]
        current = task.get("complexity", "medium")
        try:
            idx = _COMPLEXITY_ORDER.index(current)
        except ValueError:
            idx = 1
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        self._modified.add(self._cursor)
        self._refresh_task_list()

    def action_approve(self) -> None:
        if self._is_editing():
            self._save_edit()
            return
        edited_tasks = self._active_tasks
        self.post_message(self.PlanApproved(tasks=edited_tasks))

    def action_cancel_or_close(self) -> None:
        if self._editing is not None:
            self._close_editor()
            return
        self.post_message(self.PlanCancelled(tasks=self._active_tasks))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Handle text area changes — no-op, just prevent bubbling."""
        pass

    def key_ctrl_s(self) -> None:
        """Save current edit via Ctrl+S."""
        if self._editing is not None:
            self._save_edit()
