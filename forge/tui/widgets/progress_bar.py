"""Pipeline progress bar with cost and timing."""

from __future__ import annotations

from textual.widget import Widget

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

    def update_progress(
        self, done: int, total: int, cost_usd: float, elapsed: float, phase: str
    ) -> None:
        self._done = done
        self._total = total
        self._cost_usd = cost_usd
        self._elapsed = elapsed
        self._phase = phase
        self.refresh()

    def render(self) -> str:
        return format_progress(self._done, self._total, self._cost_usd, self._elapsed, self._phase)
