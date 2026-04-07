"""Task list widget — left pane of the split-pane layout."""

from __future__ import annotations

import re

from textual.message import Message
from textual.widget import Widget

from forge.core.retry_summary import retry_summary_from_task
from forge.tui.theme import STATE_COLORS, STATE_ICONS

MAX_WIDTH = 40
MIN_WIDTH = 24
_FOLLOWUP_TASK_RE = re.compile(r"-followup-(\d+)$")

_ANIMATED_ICONS: dict[str, list[str]] = {
    "in_progress": ["●", "◉", "○", "◉"],
    "in_review": ["◉", "○", "◉", "○"],
    "merging": ["◈", "◇", "◈", "◇"],
}


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def _queue_hint(task: dict) -> str:
    """Return a compact queue hint derived from scheduler insight."""
    from forge.core.blocked_reason import format_blocked_reason

    status = task.get("_queue_status", "")
    priority_rank = task.get("_priority_rank")
    reason = str(task.get("_blocked_reason", "") or "").strip()

    if status == "ready" and priority_rank:
        if priority_rank == 1:
            return "[#3fb950]NEXT[/#3fb950]"
        return f"[#58a6ff]P{priority_rank}[/#58a6ff]"

    if status == "human_wait":
        if "approval" in reason.lower():
            return "[#d29922]needs approval[/#d29922]"
        return "[#d29922]needs input[/#d29922]"

    if reason.startswith("Waiting on "):
        formatted = format_blocked_reason(reason)
        if formatted:
            # Convert "Waiting on task-2 + 1 other" -> "wait task-2 +1"
            text = formatted.lower()
            if text.startswith("waiting on "):
                text = text[11:]  # Remove "waiting on " prefix
                text = text.replace(" + ", " +").replace(" others", "").replace(" other", "")
                return f"[#8b949e]wait {text}[/#8b949e]"

    if reason.startswith("Blocked by failed dependenc"):
        formatted = format_blocked_reason(reason)
        if formatted:
            # Convert "Blocked: dep + 1 other failed" -> "blocked dep +1"
            text = formatted.lower()
            if text.startswith("blocked: "):
                text = text[9:]  # Remove "blocked: " prefix
            if text.endswith(" failed"):
                text = text[:-7]  # Remove " failed" suffix
            text = text.replace(" + ", " +").replace(" others", "").replace(" other", "")
            return f"[#f0883e]blocked {text}[/#f0883e]"

    return ""


def _followup_wave(task_id: str | None) -> int | None:
    """Return the follow-up wave number encoded in synthetic follow-up task ids."""
    if not task_id:
        return None
    match = _FOLLOWUP_TASK_RE.search(task_id)
    if not match:
        return None
    return int(match.group(1))


def _format_followup_separator(wave: int) -> str:
    """Render a compact divider before each follow-up wave."""
    return f"[#6e7681]──[/] [bold #d6a85f]Follow-up {wave}[/] [#6e7681]────────────────[/]"


def format_task_line(
    task: dict,
    *,
    selected: bool,
    multi_repo: bool = False,
    icon_frame: int = 0,
    max_width: int = MAX_WIDTH,
) -> str:
    state = task.get("state", "todo")
    # Use animated icon for selected active tasks
    if selected and state in _ANIMATED_ICONS:
        frames = _ANIMATED_ICONS[state]
        icon = frames[icon_frame % len(frames)]
    else:
        icon = STATE_ICONS.get(state, "?")
    color = STATE_COLORS.get(state, "#8b949e")
    title = task.get("title", "Untitled")

    # Build repo prefix for multi-repo pipelines
    repo_prefix = ""
    repo_width = 0
    repo_id = task.get("repo")
    if multi_repo and repo_id:
        repo_prefix = f"[#79c0ff]\\[{repo_id}][/#79c0ff] "
        repo_width = len(repo_id) + 3  # brackets + space

    # Build suffix parts
    suffix_parts: list[str] = []
    files_changed = task.get("files_changed", [])
    file_count = len(files_changed) if files_changed else 0

    if task.get("_preparing") and state == "todo":
        suffix_parts.append("[#a371f7]⚙ PREP[/#a371f7]")
    summary = retry_summary_from_task(task)
    if state == "error":
        if summary.retry_count > 0:
            suffix_parts.append(
                f"⚠ [#d29922]↻ {summary.retry_count}/{summary.max_retries}[/#d29922]"
            )
        else:
            suffix_parts.append("⚠")
    elif summary.retry_count > 0:
        suffix_parts.append(f"[#d29922]↻ {summary.retry_count}/{summary.max_retries}[/#d29922]")
    elif state == "merging":
        _MERGE_STEP_LABELS = {
            "rebasing": "Rebasing",
            "integration_check": "Checks",
            "finalizing": "Finalizing",
        }
        merge_sub = task.get("merge_substatus", "")
        label = _MERGE_STEP_LABELS.get(merge_sub, "Merging")
        suffix_parts.append(f"[#79c0ff]{label}…[/#79c0ff]")
    queue_hint = _queue_hint(task)
    if queue_hint:
        suffix_parts.append(queue_hint)
    if file_count > 0:
        suffix_parts.append(f"[#8b949e]{file_count} files[/#8b949e]")

    suffix = " ".join(suffix_parts)

    # Calculate available width for title: max_width - icon prefix (3 chars) - repo prefix - suffix
    # Rough visible length of suffix (strip markup for length calc)
    suffix_visible_len = 0
    if suffix:
        import re

        suffix_visible_len = len(re.sub(r"\[.*?\]", "", suffix)) + 1  # +1 for space before suffix

    available = max_width - 3 - repo_width - suffix_visible_len  # 3 = " X " icon prefix
    if available < 4:
        available = 4

    if len(title) > available:
        title = title[: available - 1] + "…"

    # Build the final line
    suffix_str = f" [#30363d]·[/#30363d] {suffix}" if suffix else ""
    escaped_title = _escape(title)
    if selected:
        return (
            f"[bold on #1f2937] [#d6a85f]▎[/#d6a85f] "
            f"[{color}]{icon} {repo_prefix}[#e6edf3]{escaped_title}[/#e6edf3]{suffix_str} [/]"
        )
    else:
        return f" [{color}]{icon}[/] {repo_prefix}[#c9d1d9]{escaped_title}[/#c9d1d9]{suffix_str}"


class TaskList(Widget):
    """Scrollable task list with keyboard navigation."""

    can_focus = True

    DEFAULT_CSS = """
    TaskList {
        width: 1fr;
        min-width: 32;
        max-width: 52;
        padding: 0 0 0 1;
    }
    """

    class Selected(Message):
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[dict] = []
        self._selected_index: int = 0
        self._phase: str = ""
        self._multi_repo: bool = False
        self._icon_frame: int = 0
        self._icon_timer = None
        self._last_icon_frame: int = -1

    def update_tasks(
        self,
        tasks: list[dict],
        selected_id: str | None = None,
        *,
        phase: str = "",
        multi_repo: bool = False,
    ) -> None:
        self._multi_repo = multi_repo
        self._tasks = tasks
        self._phase = phase
        if selected_id:
            for i, t in enumerate(tasks):
                if t["id"] == selected_id:
                    self._selected_index = i
                    break
        self._selected_index = min(self._selected_index, max(0, len(tasks) - 1))
        self.refresh()

    def on_mount(self) -> None:
        self._icon_timer = self.set_interval(0.5, self._tick_icon)

    def on_unmount(self) -> None:
        if self._icon_timer is not None:
            self._icon_timer.stop()

    def _tick_icon(self) -> None:
        """Animate the selected task's icon if it's in an active state."""
        self._icon_frame += 1
        # Only refresh if selected task has an animated state and frame actually changed
        if self.selected_task:
            state = self.selected_task.get("state", "")
            if state in _ANIMATED_ICONS:
                frames = _ANIMATED_ICONS[state]
                effective_frame = self._icon_frame % len(frames)
                last_effective = (
                    self._last_icon_frame % len(frames) if self._last_icon_frame >= 0 else -1
                )
                if effective_frame != last_effective:
                    self._last_icon_frame = self._icon_frame
                    self.refresh()

    @property
    def selected_task(self) -> dict | None:
        if 0 <= self._selected_index < len(self._tasks):
            return self._tasks[self._selected_index]
        return None

    def render(self) -> str:
        if not self._tasks:
            if self._phase == "planning":
                return (
                    "[bold #d6a85f]MISSION QUEUE[/]\n"
                    "[#8b949e]Forge is mapping the repo,\nscoring work, and preparing agents.[/]"
                )
            return "[#8b949e]No tasks yet[/]"
        lines = []
        last_followup_wave: int | None = None
        max_width = max(MIN_WIDTH, (self.size.width or MAX_WIDTH) - 4)
        for i, task in enumerate(self._tasks):
            wave = _followup_wave(task.get("id"))
            if wave is not None and wave != last_followup_wave:
                lines.append(_format_followup_separator(wave))
                last_followup_wave = wave
            lines.append(
                format_task_line(
                    task,
                    selected=(i == self._selected_index),
                    multi_repo=self._multi_repo,
                    icon_frame=self._icon_frame,
                    max_width=max_width,
                )
            )
        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        if self._selected_index < len(self._tasks) - 1:
            self._selected_index += 1
            self.refresh()
            if task := self.selected_task:
                self.post_message(self.Selected(task["id"]))

    def action_cursor_up(self) -> None:
        if self._selected_index > 0:
            self._selected_index -= 1
            self.refresh()
            if task := self.selected_task:
                self.post_message(self.Selected(task["id"]))
