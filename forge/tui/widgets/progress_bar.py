"""Pipeline progress bar with cost and timing."""

from __future__ import annotations

from textual.widget import Widget


def format_progress(done: int, total: int, cost_usd: float, elapsed_seconds: float, phase: str, *, bar_width: int = 30) -> str:
    minutes = int(elapsed_seconds) // 60
    seconds = int(elapsed_seconds) % 60
    time_str = f"{minutes}:{seconds:02d}"

    if phase == "planning":
        return f"[#58a6ff]◌ Planning...[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if phase == "planned":
        return f"[#a371f7]◉ Plan ready — review required[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if phase == "complete":
        return f"[#3fb950]✔ Complete[/] │ {done}/{total} tasks │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if phase == "error":
        return f"[#f85149]✖ Error[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if total == 0:
        return f"[#8b949e]{phase}[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"

    pct = done / total
    filled = int(pct * bar_width)
    empty = bar_width - filled
    bar = f"[#3fb950]{'█' * filled}[/][#21262d]{'░' * empty}[/]"
    return f"{bar} {pct:.0%} │ {done}/{total} tasks │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"


class PipelineProgress(Widget):
    """Bottom progress bar."""

    DEFAULT_CSS = """
    PipelineProgress {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._done = 0
        self._total = 0
        self._cost_usd = 0.0
        self._elapsed = 0.0
        self._phase = "idle"

    def update_progress(self, done: int, total: int, cost_usd: float, elapsed: float, phase: str) -> None:
        self._done = done
        self._total = total
        self._cost_usd = cost_usd
        self._elapsed = elapsed
        self._phase = phase
        self.refresh()

    def render(self) -> str:
        return format_progress(self._done, self._total, self._cost_usd, self._elapsed, self._phase)
