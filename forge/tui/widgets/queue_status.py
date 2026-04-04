"""Compact queue telemetry for the pipeline screen."""

from __future__ import annotations

from textual.widget import Widget

from forge.tui.theme import ACCENT_BLUE, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED, TEXT_MUTED


def _truncate(text: str, width: int = 36) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    return text[: width - 1] + "..."


def _summarize_reason(reason: str) -> str:
    if not reason:
        return "scheduler is recalculating"
    if reason.startswith("Waiting on "):
        deps = [
            part.strip() for part in reason.removeprefix("Waiting on ").split(",") if part.strip()
        ]
        if not deps:
            return "waiting on dependencies"
        if len(deps) == 1:
            return f"waiting on {deps[0]}"
        return f"waiting on {deps[0]} +{len(deps) - 1}"
    if reason.startswith("Blocked by failed dependency: "):
        dep = reason.removeprefix("Blocked by failed dependency: ").strip()
        return f"blocked by {dep}"
    if reason.startswith("Blocked by failed dependencies: "):
        deps = [
            part.strip()
            for part in reason.removeprefix("Blocked by failed dependencies: ").split(",")
            if part.strip()
        ]
        if not deps:
            return "blocked by failed dependencies"
        return f"blocked by {deps[0]} +{len(deps) - 1}"
    return reason.lower()


def format_queue_status(scheduling: dict | None) -> str:
    """Render a two-line queue summary from scheduling insight."""
    if not scheduling:
        return f"[{TEXT_MUTED}]Dispatch map warming up[/]"

    ready = scheduling.get("ready_count", 0)
    active = scheduling.get("active_count", 0)
    blocked = scheduling.get("blocked_count", 0)
    human = scheduling.get("human_wait_count", 0)
    critical = scheduling.get("critical_path_length", 0)

    header = (
        f"[bold {ACCENT_BLUE}]QUEUE[/] "
        f"[{ACCENT_GREEN}]ready {ready}[/]  "
        f"[{ACCENT_ORANGE}]live {active}[/]  "
        f"[{ACCENT_RED}]blocked {blocked}[/]  "
        f"[#d6a85f]human {human}[/]  "
        f"[#79c0ff]cp {critical}[/]"
    )

    dispatching_now = scheduling.get("dispatching_now", []) or []
    next_up = scheduling.get("next_up", []) or []
    task_map = scheduling.get("tasks", {}) or {}

    if dispatching_now:
        detail = "launching " + ", ".join(dispatching_now[:2])
        if len(dispatching_now) > 2:
            detail += f" +{len(dispatching_now) - 2}"
    elif next_up:
        task_ids = [entry.get("task_id", "") for entry in next_up if entry.get("task_id")]
        detail = "next up " + ", ".join(task_ids[:2])
        if len(task_ids) > 2:
            detail += f" +{len(task_ids) - 2}"
    elif human:
        detail = "waiting on human input or approval"
    elif blocked:
        blocked_ids = scheduling.get("blocked_task_ids", []) or []
        first_id = blocked_ids[0] if blocked_ids else ""
        reason = _summarize_reason(task_map.get(first_id, {}).get("reason", ""))
        detail = reason
    else:
        detail = "queue clear - all runnable work is flowing"

    return f"{header}\n[{TEXT_MUTED}]{_truncate(detail)}[/]"


class QueueStatus(Widget):
    """Small telemetry rail for scheduler insight."""

    DEFAULT_CSS = """
    QueueStatus {
        height: 2;
        padding: 0 1;
        background: #11161d;
        border-top: tall #263041;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._scheduling: dict | None = None

    def update(self, scheduling: dict | None) -> None:
        self._scheduling = scheduling
        self.refresh()

    def render(self) -> str:
        return format_queue_status(self._scheduling)
