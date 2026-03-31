"""Dry-run screen — DAG visualization with inline editing for task review."""

from __future__ import annotations

import copy

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static, TextArea

from forge.tui.screens.plan_approval import (
    _COMPLEXITY_ORDER,
    format_cost_estimate,
    format_plan_summary,
)
from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    ACCENT_RED,
    ACCENT_YELLOW,
    TEXT_MUTED,
    TEXT_SECONDARY,
)
from forge.tui.widgets.dag import _escape
from forge.tui.widgets.shortcut_bar import ShortcutBar

_COMPLEXITY_COLORS = {
    "low": ACCENT_GREEN,
    "medium": ACCENT_YELLOW,
    "high": ACCENT_RED,
}


def _build_dag_with_models(tasks: list[dict], model_assignments: dict[str, str] | None) -> str:
    """Build DAG text with model assignments shown next to each task."""
    if not tasks:
        return "[#8b949e]No tasks[/]"

    models = model_assignments or {}
    task_map = {t["id"]: t for t in tasks}
    lines = []
    for task in tasks:
        color = _COMPLEXITY_COLORS.get(task.get("complexity", "medium"), TEXT_SECONDARY)
        deps = task.get("depends_on", [])
        title = task.get("title", task["id"])
        short_title = title[:30] + "\u2026" if len(title) > 30 else title
        escaped_title = _escape(short_title)
        escaped_id = _escape(task["id"])

        model = models.get(task["id"])
        model_tag = f" [{ACCENT_PURPLE}]({model})[/]" if model else ""

        if deps:
            dep_str = ", ".join(_escape(d) for d in deps if d in task_map)
            lines.append(
                f"  [{color}]\u25cf[/] {escaped_id}: {escaped_title}{model_tag} [#8b949e]\u2190 {dep_str}[/]"
            )
        else:
            lines.append(f"  [{color}]\u25cf[/] {escaped_id}: {escaped_title}{model_tag}")
    return "\n".join(lines)


def _format_task_detail(
    task: dict,
    tasks: list[dict],
    model_assignments: dict[str, str] | None,
) -> str:
    """Format full detail view for a single task."""
    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    files = task.get("files", [])
    complexity = task.get("complexity", "medium")
    deps = task.get("depends_on", [])
    color = _COMPLEXITY_COLORS.get(complexity, TEXT_SECONDARY)

    task_map = {t["id"]: t for t in tasks}
    models = model_assignments or {}

    lines = [
        f"[bold {ACCENT_BLUE}]{title}[/]",
        "",
    ]
    if desc:
        lines.append(f"[{TEXT_SECONDARY}]{desc}[/]")
        lines.append("")

    lines.append(f"[bold]Complexity:[/] [{color}]{complexity}[/]")

    if files:
        lines.append(f"[bold]Files:[/] [{TEXT_SECONDARY}]{len(files)} file(s)[/]")
        for f in files:
            lines.append(f"  [{TEXT_SECONDARY}]{f}[/]")
    else:
        lines.append(f"[bold]Files:[/] [{TEXT_MUTED}]none[/]")

    if deps:
        lines.append("[bold]Dependencies:[/]")
        for dep_id in deps:
            dep_task = task_map.get(dep_id)
            dep_title = dep_task.get("title", dep_id) if dep_task else dep_id
            lines.append(f"  [{TEXT_SECONDARY}]{dep_id}: {dep_title}[/]")
    else:
        lines.append(f"[bold]Dependencies:[/] [{TEXT_MUTED}]none[/]")

    model = models.get(task.get("id", ""))
    if model:
        lines.append(f"[bold]Agent model:[/] [{ACCENT_PURPLE}]{model}[/]")

    return "\n".join(lines)


class DryRunScreen(Screen):
    """Dry-run plan viewer — DAG visualization with inline task editing."""

    DEFAULT_CSS = f"""
    DryRunScreen {{
        layout: vertical;
    }}
    #dry-run-header {{
        height: 3;
        padding: 1 2;
        background: #11161d;
        color: {ACCENT_BLUE};
        border-bottom: tall #263041;
    }}
    #dry-run-cost {{
        padding: 0 2;
        background: #11161d;
        height: 1;
    }}
    #dry-run-panels {{
        height: 1fr;
        background: #0d1117;
    }}
    #dag-panel {{
        width: 1fr;
        padding: 1 2;
        border-right: tall #263041;
        background: #11161d;
    }}
    #detail-panel {{
        width: 1fr;
        padding: 1 2;
        background: #0d1117;
    }}
    #dry-run-footer {{
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #11161d;
        color: {TEXT_SECONDARY};
        border-top: tall #263041;
    }}
    #edit-area {{
        height: 6;
        margin: 0 2;
        border: tall {ACCENT_YELLOW};
        background: #0d1117;
        display: none;
    }}
    #edit-area.visible {{
        display: block;
    }}
    #edit-label {{
        height: 1;
        margin: 0 2;
        color: {ACCENT_YELLOW};
        display: none;
    }}
    #edit-label.visible {{
        display: block;
    }}
    """

    BINDINGS = [
        Binding("enter", "approve", "Approve & Execute", show=True, priority=True),
        Binding("escape", "cancel_or_close", "Cancel", show=True, priority=True),
        Binding("j", "cursor_down", "Next task", show=False),
        Binding("k", "cursor_up", "Prev task", show=False),
        Binding("down", "cursor_down", "Next task", show=False),
        Binding("up", "cursor_up", "Prev task", show=False),
        Binding("e", "edit_task", "Edit description", show=False),
        Binding("f", "edit_files", "Edit files", show=False),
        Binding("c", "cycle_complexity", "Cycle complexity", show=False),
    ]

    class PlanApproved(Message):
        """User approved the dry-run plan with (possibly edited) tasks."""

        def __init__(self, tasks: list[dict] | None = None) -> None:
            self.tasks = tasks
            super().__init__()

    class PlanCancelled(Message):
        """User cancelled the dry-run plan."""

    def __init__(
        self,
        tasks: list[dict],
        cost_estimate: dict | None = None,
        model_assignments: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._tasks = [copy.deepcopy(t) for t in tasks]
        self._cost_estimate = cost_estimate
        self._model_assignments = model_assignments or {}
        self._cursor = 0
        self._modified: set[int] = set()
        self._editing: str | None = None  # "description", "files", or None

    @property
    def _active_tasks(self) -> list[dict]:
        """Return all tasks (dry-run doesn't support removal)."""
        return list(self._tasks)

    def compose(self) -> ComposeResult:
        summary = format_plan_summary(self._active_tasks)
        cost_line = format_cost_estimate(self._cost_estimate)

        yield Static(f"[bold {ACCENT_BLUE}]DRY RUN[/]  {summary}", id="dry-run-header")
        if cost_line is not None:
            yield Static(cost_line, id="dry-run-cost")
        yield Static("", id="edit-label")
        yield TextArea(id="edit-area")
        with Horizontal(id="dry-run-panels"):
            with VerticalScroll(id="dag-panel"):
                yield Static(
                    _build_dag_with_models(self._tasks, self._model_assignments),
                    id="dag-content",
                )
            with VerticalScroll(id="detail-panel"):
                detail = ""
                if self._tasks:
                    detail = _format_task_detail(
                        self._tasks[0], self._tasks, self._model_assignments
                    )
                yield Static(detail, id="detail-content")
        yield Static(
            "[Enter] approve  [e] edit  [f] files  [c] complexity  [j/k] navigate  [Esc] cancel",
            id="dry-run-footer",
        )
        yield ShortcutBar(
            [
                ("Enter", "Approve Plan"),
                ("\u2191\u2193", "Navigate"),
                ("e", "Edit"),
                ("Esc", "Cancel"),
            ]
        )

    def _refresh_dag(self) -> None:
        """Re-render the DAG panel."""
        self.query_one("#dag-content", Static).update(
            _build_dag_with_models(self._tasks, self._model_assignments)
        )

    def _refresh_detail(self) -> None:
        """Re-render the detail panel for the current task."""
        if not self._tasks:
            self.query_one("#detail-content", Static).update("")
            return
        task = self._tasks[self._cursor]
        self.query_one("#detail-content", Static).update(
            _format_task_detail(task, self._tasks, self._model_assignments)
        )

    def _refresh_header(self) -> None:
        """Re-render the header summary."""
        summary = format_plan_summary(self._active_tasks)
        self.query_one("#dry-run-header", Static).update(
            f"[bold {ACCENT_BLUE}]DRY RUN[/]  {summary}"
        )

    def _refresh_all(self) -> None:
        """Refresh all panels."""
        self._refresh_dag()
        self._refresh_detail()
        self._refresh_header()

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
        return self._editing is not None

    def action_cursor_down(self) -> None:
        if self._is_editing():
            return
        if self._cursor < len(self._tasks) - 1:
            self._cursor += 1
            self._refresh_detail()

    def action_cursor_up(self) -> None:
        if self._is_editing():
            return
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh_detail()

    def action_edit_task(self) -> None:
        """Open inline editor for task title + description."""
        if self._is_editing():
            return
        if not self._tasks:
            return
        task = self._tasks[self._cursor]
        self._editing = "description"
        label = self.query_one("#edit-label", Static)
        label.update(
            f"[{ACCENT_YELLOW}]Editing task {self._cursor + 1} \u2014 title | description (Ctrl+S to save, Esc to cancel)[/]"
        )
        label.add_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.text = f"{task.get('title', '')}\n{task.get('description', '')}"
        area.add_class("visible")
        area.focus()

    def action_edit_files(self) -> None:
        """Open inline editor for comma-separated file list."""
        if self._is_editing():
            return
        if not self._tasks:
            return
        task = self._tasks[self._cursor]
        self._editing = "files"
        label = self.query_one("#edit-label", Static)
        label.update(
            f"[{ACCENT_YELLOW}]Editing files for task {self._cursor + 1} \u2014 comma-separated (Ctrl+S to save, Esc to cancel)[/]"
        )
        label.add_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.text = ", ".join(task.get("files", []))
        area.add_class("visible")
        area.focus()

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

        self._close_editor()

    def _close_editor(self) -> None:
        """Close the edit area and return to navigation."""
        self._editing = None
        label = self.query_one("#edit-label", Static)
        label.remove_class("visible")
        area = self.query_one("#edit-area", TextArea)
        area.remove_class("visible")
        self._refresh_all()

    def action_cycle_complexity(self) -> None:
        """Cycle complexity: low -> medium -> high -> low."""
        if self._is_editing():
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
        self._refresh_all()

    def action_approve(self) -> None:
        if self._is_editing():
            self._save_edit()
            return
        self.post_message(self.PlanApproved(tasks=self._active_tasks))

    def action_cancel_or_close(self) -> None:
        if self._editing is not None:
            self._close_editor()
            return
        self.post_message(self.PlanCancelled())

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Handle text area changes — prevent bubbling."""
        pass

    def key_ctrl_s(self) -> None:
        """Save current edit via Ctrl+S."""
        if self._editing is not None:
            self._save_edit()
