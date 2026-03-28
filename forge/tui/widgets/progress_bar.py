"""Pipeline progress bar with cost and timing."""

from __future__ import annotations

from textual.widget import Widget

# Breathing pulse colors for active segment (orange spectrum)
_PULSE_FRAMES = [
    "#f0883e",  # Bright orange
    "#d4782f",  # Medium
    "#b06828",  # Dim
    "#d4782f",  # Medium
    "#f0883e",  # Bright orange
]
_PULSE_INTERVAL = 0.2  # 200ms per frame

# 7 stages in the pipeline
_STAGES = [
    ("planning", "Planning"),
    ("planned", "Plan Approval"),
    ("contracts", "Contracts"),
    ("executing", "Execution"),
    ("review", "Review"),
    ("final_approval", "Final Approval"),
    ("pr_created", "PR Created"),
]

_STAGE_ORDER = [s[0] for s in _STAGES]
_STAGE_LABELS = {s[0]: s[1] for s in _STAGES}

# Map aliases to canonical stage keys for the segment bar
_PHASE_TO_STAGE: dict[str, str] = {
    "planning": "planning",
    "planned": "planned",
    "contracts": "contracts",
    "executing": "executing",
    "in_progress": "executing",
    "review": "review",
    "in_review": "review",
    "final_approval": "final_approval",
    "pr_creating": "final_approval",
    "pr_created": "pr_created",
    "complete": "pr_created",
}


def _make_segment_bar(current_phase: str) -> str:
    """Build a 7-segment visual indicator.

    Completed segments are bright, the active one is highlighted, future ones are dim.
    """
    stage_key = _PHASE_TO_STAGE.get(current_phase, "")
    try:
        active_idx = _STAGE_ORDER.index(stage_key) if stage_key else -1
    except ValueError:
        active_idx = -1

    parts: list[str] = []
    for i, (_key, label) in enumerate(_STAGES):
        if i < active_idx:
            parts.append(f"[#3fb950]{label}[/]")
        elif i == active_idx:
            parts.append(f"[bold #f0883e]{label}[/]")
        else:
            parts.append(f"[#484f58]{label}[/]")
        if i < len(_STAGES) - 1:
            parts.append("[#30363d] → [/]")
    return "".join(parts)


def format_progress(
    done: int,
    total: int,
    cost_usd: float,
    elapsed_seconds: float,
    phase: str,
    *,
    bar_width: int = 30,
) -> str:
    minutes = int(elapsed_seconds) // 60
    seconds = int(elapsed_seconds) % 60
    time_str = f"{minutes}:{seconds:02d}"

    meta = f"[#3fb950]${cost_usd:.2f}[/] │ {time_str}"

    if phase == "planning":
        return f"[#58a6ff]◌ Planning...[/] │ {meta}"
    if phase == "planned":
        return f"[#a371f7]◉ Plan ready — review required[/] │ {meta}"
    if phase == "contracts":
        return f"[#d2a8ff]⚙ Generating contracts...[/] │ {meta}"
    if phase == "final_approval":
        return f"[#f0883e]◎ Awaiting final approval[/] │ {meta}"
    if phase == "pr_creating":
        return f"[#d2a8ff]⚙ Creating PR...[/] │ {meta}"
    if phase == "pr_created":
        return f"[#3fb950]✔ PR Created[/] │ {done}/{total} tasks │ {meta}"
    if phase == "complete":
        return f"[#3fb950]✔ Complete[/] │ {done}/{total} tasks │ {meta}"
    if phase == "error":
        return f"[#f85149]✖ Error[/] │ {meta}"
    if total == 0:
        return f"[#8b949e]{phase}[/] │ {meta}"

    pct = done / total
    filled = int(pct * bar_width)
    empty = bar_width - filled
    bar = f"[#3fb950]{'█' * filled}[/][#21262d]{'░' * empty}[/]"
    return f"{bar} {pct:.0%} │ {done}/{total} tasks │ {meta}"


def format_task_progress(
    tasks: list[dict],
    cost_usd: float,
    elapsed_seconds: float,
    phase: str,
    pulse_frame: int = 0,
) -> str:
    """Build segmented progress bar -- one block per task with pulse on active."""
    minutes = int(elapsed_seconds) // 60
    seconds = int(elapsed_seconds) % 60
    time_str = f"{minutes}:{seconds:02d}"
    meta = f"[#3fb950]${cost_usd:.2f}[/] │ {time_str}"

    # Non-task phases: show phase-specific status
    if phase == "planning":
        return f"[#58a6ff]◌ Planning...[/] │ {meta}"
    if phase == "planned":
        return f"[#a371f7]◉ Plan ready — review required[/] │ {meta}"
    if phase == "contracts":
        return f"[#d2a8ff]⚙ Generating contracts...[/] │ {meta}"
    if phase == "final_approval":
        return f"[#f0883e]◎ Awaiting final approval[/] │ {meta}"
    if phase == "pr_creating":
        return f"[#d2a8ff]⚙ Creating PR...[/] │ {meta}"
    if phase == "pr_created":
        done = sum(1 for t in tasks if t.get("state") == "done")
        return f"[#3fb950]✔ PR Created[/] │ {done}/{len(tasks)} tasks │ {meta}"
    if phase == "complete":
        done = sum(1 for t in tasks if t.get("state") == "done")
        return f"[#3fb950]✔ Complete[/] │ {done}/{len(tasks)} tasks │ {meta}"
    if phase == "error":
        return f"[#f85149]✖ Error[/] │ {meta}"

    if not tasks:
        return f"[#8b949e]{phase}[/] │ {meta}"

    # Build segmented bar
    segments: list[str] = []
    done_count = 0
    for task in tasks:
        state = task.get("state", "todo")
        if state == "done":
            segments.append("[#3fb950]█[/]")
            done_count += 1
        elif state == "error":
            segments.append("[#f85149]█[/]")
        elif state in (
            "in_progress",
            "in_review",
            "merging",
            "awaiting_approval",
            "awaiting_input",
        ):
            # Active -- use pulsing orange
            pulse_color = _PULSE_FRAMES[pulse_frame % len(_PULSE_FRAMES)]
            segments.append(f"[{pulse_color}]█[/]")
        elif state == "cancelled":
            segments.append("[#484f58]▪[/]")
        else:
            # Pending (todo, blocked)
            segments.append("[#21262d]░[/]")

    bar = "".join(segments)
    pct = done_count / len(tasks) if tasks else 0
    return f"{bar} {pct:.0%} │ {done_count}/{len(tasks)} tasks │ {meta}"


class PipelineProgress(Widget):
    """Bottom progress bar showing segment pipeline + cost + timing."""

    DEFAULT_CSS = """
    PipelineProgress {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        border-top: tall #21262d;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._done = 0
        self._total = 0
        self._cost_usd = 0.0
        self._elapsed = 0.0
        self._phase = "idle"
        self._tasks: list[dict] = []
        self._pulse_frame: int = 0
        self._pulse_timer = None

    def on_mount(self) -> None:
        self._pulse_timer = self.set_interval(_PULSE_INTERVAL, self._tick_pulse)

    def on_unmount(self) -> None:
        if self._pulse_timer is not None:
            self._pulse_timer.stop()

    def _tick_pulse(self) -> None:
        """Animate the active task segment."""
        self._pulse_frame += 1
        # Only refresh if there's an active task
        if any(
            t.get("state")
            in ("in_progress", "in_review", "merging", "awaiting_approval", "awaiting_input")
            for t in self._tasks
        ):
            self.refresh()

    def update_progress(
        self, done: int, total: int, cost_usd: float, elapsed: float, phase: str
    ) -> None:
        self._done = done
        self._total = total
        self._cost_usd = cost_usd
        self._elapsed = elapsed
        self._phase = phase
        self.refresh()

    def update_tasks(self, tasks: list[dict]) -> None:
        """Update task list for segmented progress bar."""
        self._tasks = tasks
        self.refresh()

    def render(self) -> str:
        if self._tasks:
            return format_task_progress(
                self._tasks,
                self._cost_usd,
                self._elapsed,
                self._phase,
                pulse_frame=self._pulse_frame,
            )
        return format_progress(self._done, self._total, self._cost_usd, self._elapsed, self._phase)
