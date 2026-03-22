"""Pipeline list widget — navigable list of past pipelines for HomeScreen."""

from __future__ import annotations

import logging

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

from forge.tui.widgets.task_list import STATE_COLORS, STATE_ICONS

logger = logging.getLogger("forge.tui.widgets.pipeline_list")

# Map pipeline status → icon/color (reuse task_list constants where possible)
_STATUS_MAP: dict[str, tuple[str, str]] = {
    "complete": (STATE_ICONS.get("done", "✔"), STATE_COLORS.get("done", "#3fb950")),
    "error": (STATE_ICONS.get("error", "✖"), STATE_COLORS.get("error", "#f85149")),
    "in_progress": (STATE_ICONS.get("in_progress", "●"), STATE_COLORS.get("in_progress", "#f0883e")),
    "executing": (STATE_ICONS.get("in_progress", "●"), STATE_COLORS.get("in_progress", "#f0883e")),
    "cancelled": (STATE_ICONS.get("cancelled", "✘"), STATE_COLORS.get("cancelled", "#8b949e")),
    "planning": ("◌", "#58a6ff"),
    "planned": ("◉", "#a371f7"),
}


class PipelineList(Widget, can_focus=True):
    """Navigable list of pipelines with j/k navigation and Enter to select."""

    DEFAULT_CSS = """
    PipelineList {
        width: 1fr;
        height: auto;
        max-height: 8;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "select_pipeline", "Select", show=False),
    ]

    class Selected(Message):
        """Posted when user presses Enter on a pipeline."""
        def __init__(self, pipeline_id: str) -> None:
            self.pipeline_id = pipeline_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._pipelines: list[dict] = []
        self._selected_index: int = 0

    def update_pipelines(self, pipelines: list[dict]) -> None:
        """Update the pipeline list.

        Each dict should have keys: 'id', 'description', 'status',
        'created_at', 'task_count', 'total_cost_usd'.
        """
        self._pipelines = list(pipelines)
        self._selected_index = min(self._selected_index, max(0, len(pipelines) - 1))
        self.refresh()

    @property
    def selected_pipeline(self) -> dict | None:
        if 0 <= self._selected_index < len(self._pipelines):
            return self._pipelines[self._selected_index]
        return None

    def render(self) -> str:
        if not self._pipelines:
            return "[#8b949e]No recent pipelines[/]"

        lines: list[str] = []
        for i, p in enumerate(self._pipelines):
            status = p.get("status", "unknown")
            icon, color = _STATUS_MAP.get(status, ("?", "#8b949e"))
            desc = p.get("description", "Untitled")[:45]
            cost = p.get("total_cost_usd", 0.0) or p.get("cost", 0.0)
            date = str(p.get("created_at", ""))[:10]
            is_selected = i == self._selected_index

            project_dir = p.get("project_dir", "") or ""
            project_tag = ""
            if project_dir:
                import os
                folder = os.path.basename(project_dir.rstrip("/"))[:20]
                if folder:
                    project_tag = f"[#8b949e]{folder}[/]  "

            if is_selected:
                lines.append(
                    f"[bold on #1f2937] [{color}]{icon}[/] {desc}  "
                    f"{project_tag}[#8b949e]{date} · ${cost:.2f}[/] [/]"
                )
            else:
                lines.append(
                    f"  [{color}]{icon}[/] {desc}  {project_tag}[#8b949e]{date} · ${cost:.2f}[/]"
                )

        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        if self._selected_index < len(self._pipelines) - 1:
            self._selected_index += 1
            self.refresh()

    def action_cursor_up(self) -> None:
        if self._selected_index > 0:
            self._selected_index -= 1
            self.refresh()

    def action_select_pipeline(self) -> None:
        p = self.selected_pipeline
        if p and "id" in p:
            self.post_message(self.Selected(p["id"]))
