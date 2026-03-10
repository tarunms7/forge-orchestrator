"""ASCII DAG overlay showing task dependency graph."""

from __future__ import annotations

from textual.widget import Widget


def build_dag_text(tasks: list[dict]) -> str:
    if not tasks:
        return "[#8b949e]No tasks[/]"

    state_colors = {
        "todo": "#8b949e", "in_progress": "#f0883e", "in_review": "#a371f7",
        "awaiting_approval": "#d29922", "merging": "#79c0ff", "done": "#3fb950",
        "cancelled": "#8b949e", "error": "#f85149",
    }

    task_map = {t["id"]: t for t in tasks}
    lines = []
    for task in tasks:
        color = state_colors.get(task.get("state", "todo"), "#8b949e")
        deps = task.get("depends_on", [])
        title = task.get("title", task["id"])
        short_title = title[:30] + "…" if len(title) > 30 else title
        if deps:
            dep_str = ", ".join(d for d in deps if d in task_map)
            lines.append(f"  [{color}]●[/] {task['id']}: {short_title} [#8b949e]← {dep_str}[/]")
        else:
            lines.append(f"  [{color}]●[/] {task['id']}: {short_title}")
    return "\n".join(lines)


class DagOverlay(Widget):
    """Toggleable DAG overlay."""

    DEFAULT_CSS = """
    DagOverlay {
        width: 100%;
        height: auto;
        max-height: 15;
        padding: 1;
        background: #0d1117;
        border: solid #30363d;
        display: none;
    }
    DagOverlay.visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[dict] = []

    def update_tasks(self, tasks: list[dict]) -> None:
        self._tasks = tasks
        self.refresh()

    def toggle(self) -> None:
        self.toggle_class("visible")

    def render(self) -> str:
        header = "[bold #58a6ff]Task Dependencies[/] [#8b949e](g to close)[/]\n"
        return header + build_dag_text(self._tasks)
