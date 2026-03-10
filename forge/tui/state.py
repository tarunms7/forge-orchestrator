"""Reactive state container for the TUI.

Holds all data the UI needs: pipeline phase, tasks, agent output, costs.
Widgets read from this; EventBus writes to this via apply_event().
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger("forge.tui.state")


class TuiState:
    """Single source of truth for TUI data."""

    def __init__(self, max_output_lines: int = 1000) -> None:
        self._max_output_lines = max_output_lines
        self._change_callbacks: list[Callable[[str], None]] = []

        self.pipeline_id: str | None = None
        self.phase: str = "idle"
        self.total_cost_usd: float = 0.0
        self.elapsed_seconds: float = 0.0

        self.tasks: dict[str, dict] = {}
        self.task_order: list[str] = []
        self.selected_task_id: str | None = None

        self.agent_output: dict[str, list[str]] = defaultdict(list)
        self.planner_output: list[str] = []
        self.error: str | None = None

    def on_change(self, callback: Callable[[str], None]) -> None:
        self._change_callbacks.append(callback)

    def _notify(self, field: str) -> None:
        for cb in self._change_callbacks:
            try:
                cb(field)
            except Exception:
                logger.exception("Change callback error for field %r", field)

    def apply_event(self, event_type: str, data: dict) -> None:
        handler = self._EVENT_MAP.get(event_type)
        if handler:
            handler(self, data)

    def _on_phase_changed(self, data: dict) -> None:
        self.phase = data.get("phase", self.phase)
        self._notify("phase")

    def _on_plan_ready(self, data: dict) -> None:
        self.tasks.clear()
        self.task_order.clear()
        for t in data.get("tasks", []):
            tid = t["id"]
            self.tasks[tid] = {
                "id": tid,
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "files": t.get("files", []),
                "depends_on": t.get("depends_on", []),
                "complexity": t.get("complexity", "medium"),
                "state": "todo",
                "agent_cost": 0.0,
                "error": None,
            }
            self.task_order.append(tid)
        if self.task_order and not self.selected_task_id:
            self.selected_task_id = self.task_order[0]
        self._notify("tasks")

    def _on_task_state_changed(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["state"] = data.get("state", self.tasks[tid]["state"])
            if "error" in data:
                self.tasks[tid]["error"] = data["error"]
            self._notify("tasks")

    def _on_agent_output(self, data: dict) -> None:
        tid = data.get("task_id", "")
        line = data.get("line", "")
        lines = self.agent_output[tid]
        lines.append(line)
        if len(lines) > self._max_output_lines:
            del lines[: len(lines) - self._max_output_lines]
        self._notify("agent_output")

    def _on_planner_output(self, data: dict) -> None:
        self.planner_output.append(data.get("line", ""))
        self._notify("planner_output")

    def _on_cost_update(self, data: dict) -> None:
        if "total_cost_usd" in data:
            self.total_cost_usd = data["total_cost_usd"]
        self._notify("cost")

    def _on_task_cost_update(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            if "agent_cost" in data:
                self.tasks[tid]["agent_cost"] = data["agent_cost"]
            self._notify("tasks")

    def _on_review_update(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["review"] = data
            self._notify("tasks")

    def _on_merge_result(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["merge_result"] = data
            self._notify("tasks")

    def _on_awaiting_approval(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["state"] = "awaiting_approval"
            self._notify("tasks")

    def _on_pipeline_error(self, data: dict) -> None:
        self.error = data.get("error", "Unknown error")
        self._notify("error")

    @property
    def done_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t["state"] == "done")

    @property
    def error_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t["state"] == "error")

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def progress_pct(self) -> float:
        if not self.tasks:
            return 0.0
        return (self.done_count / self.total_count) * 100

    @property
    def active_task_ids(self) -> list[str]:
        return [tid for tid, t in self.tasks.items() if t["state"] in ("in_progress", "in_review", "merging")]

    _EVENT_MAP: dict[str, Callable[["TuiState", dict], None]] = {
        "pipeline:phase_changed": _on_phase_changed,
        "pipeline:plan_ready": _on_plan_ready,
        "pipeline:cost_update": _on_cost_update,
        "pipeline:error": _on_pipeline_error,
        "task:state_changed": _on_task_state_changed,
        "task:agent_output": _on_agent_output,
        "task:cost_update": _on_task_cost_update,
        "task:review_update": _on_review_update,
        "task:merge_result": _on_merge_result,
        "task:awaiting_approval": _on_awaiting_approval,
        "planner:output": _on_planner_output,
    }
