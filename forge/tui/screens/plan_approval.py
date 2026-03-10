"""Plan approval screen — shows planned tasks for user review before execution."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import VerticalScroll
from textual.binding import Binding
from textual.message import Message


_COMPLEXITY_COLORS = {
    "low": "#3fb950",
    "medium": "#d29922",
    "high": "#f85149",
}


def format_plan_task(task: dict, index: int) -> str:
    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    files = task.get("files", [])
    complexity = task.get("complexity", "medium")
    deps = task.get("depends_on", [])
    color = _COMPLEXITY_COLORS.get(complexity, "#8b949e")

    lines = [f"  [bold #58a6ff]{index}. {title}[/]  [{color}]{complexity}[/]"]
    if desc:
        lines.append(f"     [#8b949e]{desc[:120]}[/]")
    if files:
        file_str = ", ".join(files[:5])
        if len(files) > 5:
            file_str += f" +{len(files) - 5} more"
        lines.append(f"     [#8b949e]Files:[/] {file_str}")
    if deps:
        lines.append(f"     [#8b949e]Depends on:[/] {', '.join(deps)}")
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
            color = _COMPLEXITY_COLORS.get(level, "#8b949e")
            parts.append(f"[{color}]{n} {level}[/]")
    if estimated_cost > 0:
        parts.append(f"[#3fb950]~${estimated_cost:.2f}[/]")
    return " · ".join(parts)


class PlanApprovalScreen(Screen):
    """Shows the planned tasks for user approval before execution."""

    DEFAULT_CSS = """
    PlanApprovalScreen {
        layout: vertical;
    }
    #plan-header {
        height: 3;
        padding: 1 2;
        background: #161b22;
        color: #58a6ff;
    }
    #plan-body {
        padding: 1 2;
    }
    #plan-footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("enter", "approve", "Approve & Execute", show=True, priority=True),
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
    ]

    class PlanApproved(Message):
        """User approved the plan."""

    class PlanCancelled(Message):
        """User cancelled the plan."""

    def __init__(self, tasks: list[dict], estimated_cost: float = 0.0) -> None:
        super().__init__()
        self._tasks = tasks
        self._estimated_cost = estimated_cost

    def compose(self) -> ComposeResult:
        summary = format_plan_summary(self._tasks, self._estimated_cost)
        yield Static(f"[bold #58a6ff]PLAN REVIEW[/]  {summary}", id="plan-header")
        with VerticalScroll(id="plan-body"):
            for i, task in enumerate(self._tasks, 1):
                yield Static(format_plan_task(task, i))
                yield Static("")
        yield Static("[Enter] approve & execute  [Esc] cancel", id="plan-footer")

    def action_approve(self) -> None:
        self.post_message(self.PlanApproved())

    def action_cancel(self) -> None:
        self.post_message(self.PlanCancelled())
