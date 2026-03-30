"""Pipeline list widget — navigable list of past pipelines for HomeScreen."""

from __future__ import annotations

import logging
import os

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

from forge.tui.theme import PIPELINE_STATUS_ICONS, STATE_COLORS, STATE_ICONS

logger = logging.getLogger("forge.tui.widgets.pipeline_list")

# Map pipeline status → icon/color
_STATUS_MAP: dict[str, tuple[str, str]] = {
    "complete": (STATE_ICONS.get("done", "✔"), STATE_COLORS.get("done", "#3fb950")),
    "error": (STATE_ICONS.get("error", "✖"), STATE_COLORS.get("error", "#f85149")),
    "in_progress": (
        STATE_ICONS.get("in_progress", "●"),
        STATE_COLORS.get("in_progress", "#f0883e"),
    ),
    "executing": (STATE_ICONS.get("in_progress", "●"), STATE_COLORS.get("in_progress", "#f0883e")),
    "cancelled": (STATE_ICONS.get("cancelled", "✘"), STATE_COLORS.get("cancelled", "#8b949e")),
    **{k: v for k, v in PIPELINE_STATUS_ICONS.items() if k not in ("complete", "error")},
}

# Statuses that are resumable (user can press Enter to continue the pipeline)
RESUMABLE_STATUSES: set[str] = {
    "planning",
    "planned",
    "contracts",
    "countdown",
    "interrupted",
    "executing",
    "partial_success",
    "error",
    "retrying",
}


def is_pipeline_resumable(pipeline: dict) -> bool:
    """Return True if the pipeline can be resumed, False if read-only."""
    status = pipeline.get("status", "unknown")
    if status in RESUMABLE_STATUSES:
        return True
    # complete without PR is resumable (needs PR creation)
    if status == "complete" and not pipeline.get("pr_url"):
        return True
    return False


def _progress_text(pipeline: dict) -> str:
    """Return a short progress string for a pipeline based on its status."""
    status = pipeline.get("status", "unknown")
    total = pipeline.get("total_tasks", 0)
    done = pipeline.get("tasks_done", 0)

    if status in ("executing", "interrupted", "partial_success", "retrying"):
        return f"{done}/{total} tasks done"
    if status == "complete":
        if pipeline.get("pr_url"):
            return "✓ PR created"
        return "✓ Done — no PR yet"
    if status == "planning":
        return "Planning…"
    if status == "planned":
        return "Plan ready"
    if status in ("contracts", "countdown"):
        return "Preparing…"
    if status == "error":
        return "✗ Failed"
    if status == "cancelled":
        return "Cancelled"
    return ""


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
        Binding("R", "request_resume", "Resume", show=False),
    ]

    class Selected(Message):
        """Posted when user presses Enter on a pipeline (always read-only view)."""

        def __init__(self, pipeline_id: str) -> None:
            self.pipeline_id = pipeline_id
            super().__init__()

    class ResumeRequested(Message):
        """Posted when user presses Shift+R to resume a resumable pipeline."""

        def __init__(self, pipeline_id: str) -> None:
            self.pipeline_id = pipeline_id
            super().__init__()

    class CursorMoved(Message):
        """Posted when the cursor moves to a different pipeline."""

        def __init__(self, pipeline: dict | None) -> None:
            self.pipeline = pipeline
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._pipelines: list[dict] = []
        self._selected_index: int = 0

    def update_pipelines(self, pipelines: list[dict]) -> None:
        """Update the pipeline list.

        Each dict should have keys: 'id', 'description', 'status',
        'created_at', 'task_count'/'total_tasks', 'total_cost_usd'.
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
            resumable = is_pipeline_resumable(p)

            # Resume indicator: ▶ green for resumable, ● dim for read-only
            if resumable:
                resume_indicator = "[#3fb950]▶[/]"
            else:
                resume_indicator = "[#484f58]●[/]"

            # Progress text
            progress = _progress_text(p)
            if progress:
                if status == "error":
                    progress_tag = f"  [#f85149]{progress}[/]"
                elif status == "cancelled":
                    progress_tag = f"  [#484f58]{progress}[/]"
                else:
                    progress_tag = f"  [#8b949e]{progress}[/]"
            else:
                progress_tag = ""

            project_dir = p.get("project_dir", "") or ""
            project_tag = ""
            if project_dir:
                folder = os.path.basename(project_dir.rstrip("/"))[:20]
                if folder:
                    project_tag = f"[#8b949e]{folder}[/]  "

            # Dim read-only rows slightly
            dim_open = "[#6e7681]" if not resumable and not is_selected else ""
            dim_close = "[/]" if not resumable and not is_selected else ""

            if is_selected:
                lines.append(
                    f"[bold on #1f2937] {resume_indicator} [{color}]{icon}[/] {desc}"
                    f"{progress_tag}  "
                    f"{project_tag}[#8b949e]{date} · ${cost:.2f}[/] [/]"
                )
            else:
                lines.append(
                    f"  {dim_open}{resume_indicator} [{color}]{icon}[/] {desc}"
                    f"{progress_tag}  "
                    f"{project_tag}[#8b949e]{date} · ${cost:.2f}[/]{dim_close}"
                )

        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        if self._selected_index < len(self._pipelines) - 1:
            self._selected_index += 1
            self.refresh()
            self.post_message(self.CursorMoved(self.selected_pipeline))

    def action_cursor_up(self) -> None:
        if self._selected_index > 0:
            self._selected_index -= 1
            self.refresh()
            self.post_message(self.CursorMoved(self.selected_pipeline))

    def action_select_pipeline(self) -> None:
        p = self.selected_pipeline
        if p and "id" in p:
            self.post_message(self.Selected(p["id"]))

    def action_request_resume(self) -> None:
        """Shift+R: request resume only for resumable pipelines."""
        p = self.selected_pipeline
        if p and "id" in p and is_pipeline_resumable(p):
            self.post_message(self.ResumeRequested(p["id"]))
