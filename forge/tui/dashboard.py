"""TUI dashboard. Minimal status display using Rich."""

from rich.console import Console
from rich.table import Table

from forge.core.models import TaskRecord


def format_status_table(tasks: list[TaskRecord]) -> str:
    """Format tasks as a Rich table string."""
    if not tasks:
        return "No tasks"

    console = Console(record=True, width=120)
    table = Table(title="Forge Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("State", style="bold")
    table.add_column("Agent", style="green")
    table.add_column("Files", style="dim")

    for task in tasks:
        state_style = _state_color(task.state.value)
        table.add_row(
            task.id,
            task.title,
            f"[{state_style}]{task.state.value}[/{state_style}]",
            task.assigned_agent or "-",
            ", ".join(task.files[:2]) + ("..." if len(task.files) > 2 else ""),
        )

    console.print(table)
    return console.export_text()


def _state_color(state: str) -> str:
    colors = {
        "todo": "white",
        "in_progress": "yellow",
        "in_review": "blue",
        "merging": "magenta",
        "done": "green",
        "error": "red",
        "cancelled": "dim",
    }
    return colors.get(state, "white")
