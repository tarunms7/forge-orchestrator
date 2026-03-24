"""Stats screen — pipeline performance metrics, cost breakdown, retry hotspots."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_PURPLE,
    ACCENT_RED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from forge.tui.widgets.shortcut_bar import ShortcutBar

logger = logging.getLogger("forge.tui.screens.stats")


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


def _fmt_tokens(count: int) -> str:
    """Format token counts with K/M suffixes."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _trend_arrow(values: list[float]) -> str:
    """Return a trend arrow based on the last few values (newest first).

    Compares average of first half vs second half. Returns up/down/flat arrow.
    """
    if len(values) < 2:
        return f"[{TEXT_SECONDARY}]—[/]"
    mid = len(values) // 2
    recent = sum(values[:mid]) / mid
    older = sum(values[mid:]) / (len(values) - mid)
    if older == 0:
        return f"[{TEXT_SECONDARY}]—[/]"
    pct = (recent - older) / older
    if pct > 0.1:
        return f"[{ACCENT_RED}]▲[/]"  # red = cost/duration going up (bad)
    elif pct < -0.1:
        return f"[{ACCENT_GREEN}]▼[/]"  # green = cost/duration going down (good)
    return f"[{TEXT_SECONDARY}]—[/]"


def format_pipeline_summary(trends: list[dict]) -> str:
    """Section 1: Pipeline Performance Summary — compact table of recent pipelines."""
    if not trends:
        return f"[{TEXT_SECONDARY}]No pipeline data available[/]"

    _STATUS_ICONS = {
        "done": ("✔", ACCENT_GREEN),
        "complete": ("✔", ACCENT_GREEN),
        "executing": ("●", ACCENT_ORANGE),
        "planning": ("◌", ACCENT_BLUE),
        "error": ("✖", ACCENT_RED),
        "cancelled": ("✖", ACCENT_RED),
    }

    lines = [
        f"[bold {TEXT_SECONDARY}]  Status  Description                              Duration    Cost     Tasks[/]"
    ]
    for p in trends[:8]:
        status = p.get("status", "unknown")
        icon, color = _STATUS_ICONS.get(status, ("?", TEXT_SECONDARY))
        desc = (p.get("description", "Untitled") or "Untitled")[:40]
        dur = _fmt_duration(p.get("duration_s", 0.0))
        cost = f"${p.get('total_cost_usd', 0.0):.2f}"
        succeeded = p.get("tasks_succeeded", 0)
        failed = p.get("tasks_failed", 0)
        task_info = f"{succeeded}✓"
        if failed:
            task_info += f" {failed}✗"
        lines.append(
            f"  [{color}]{icon}[/] {desc:<40s}  [{TEXT_SECONDARY}]{dur:>8s}  {cost:>7s}  {task_info}[/]"
        )
    return "\n".join(lines)


def format_cost_breakdown(stats: dict) -> str:
    """Section 2: Cost Breakdown — show planner/agent/review cost split."""
    if not stats:
        return f"[{TEXT_SECONDARY}]No cost data available[/]"

    total = stats.get("total_cost_usd", 0.0)
    planner = stats.get("planner_cost_usd", 0.0)
    tasks = stats.get("tasks", [])

    agent_cost = sum(t.get("agent_cost_usd", 0.0) for t in tasks)
    review_cost = sum(t.get("review_cost_usd", 0.0) for t in tasks)

    if total <= 0:
        return f"[{TEXT_SECONDARY}]No costs recorded[/]"

    def _bar(value: float, total_val: float, color: str) -> str:
        pct = (value / total_val * 100) if total_val > 0 else 0
        bar_len = int(pct / 2.5)  # max 40 chars at 100%
        bar = "█" * bar_len + "░" * (40 - bar_len)
        return f"  [{color}]{bar}[/] {pct:5.1f}%  ${value:.3f}"

    lines = [
        f"  [bold {TEXT_PRIMARY}]Total: ${total:.3f}[/]",
        "",
        f"  [{ACCENT_PURPLE}]Planner[/]",
        _bar(planner, total, ACCENT_PURPLE),
        f"  [{ACCENT_BLUE}]Agent[/]",
        _bar(agent_cost, total, ACCENT_BLUE),
        f"  [{ACCENT_ORANGE}]Review[/]",
        _bar(review_cost, total, ACCENT_ORANGE),
    ]
    return "\n".join(lines)


def format_retry_hotspots(retry_summary: list[dict]) -> str:
    """Section 3: Retry Hotspots — top 5 most-retried error patterns."""
    if not retry_summary:
        return f"[{TEXT_SECONDARY}]No retries recorded — clean runs![/]"

    lines = []
    for entry in retry_summary[:5]:
        pattern = entry.get("error_pattern", "unknown")[:80]
        retries = entry.get("total_retries", 0)
        task_count = entry.get("task_count", 0)
        task_ids = entry.get("task_ids", [])
        ids_str = ", ".join(task_ids[:3])
        if len(task_ids) > 3:
            ids_str += f" +{len(task_ids) - 3}"
        lines.append(
            f"  [{ACCENT_RED}]{retries}×[/] [{TEXT_PRIMARY}]{pattern}[/]\n"
            f"      [{TEXT_SECONDARY}]{task_count} task(s): {ids_str}[/]"
        )
    return "\n".join(lines)


def format_token_usage(stats: dict) -> str:
    """Section 4: Token Usage — total input/output tokens with avg per task."""
    if not stats:
        return f"[{TEXT_SECONDARY}]No token data available[/]"

    total_in = stats.get("total_input_tokens", 0)
    total_out = stats.get("total_output_tokens", 0)
    tasks = stats.get("tasks", [])
    n_tasks = len(tasks) if tasks else 1

    avg_in = total_in / n_tasks if n_tasks else 0
    avg_out = total_out / n_tasks if n_tasks else 0

    lines = [
        f"  [{ACCENT_BLUE}]Input tokens:[/]   {_fmt_tokens(total_in):>8s}  [{TEXT_SECONDARY}](avg {_fmt_tokens(int(avg_in))}/task)[/]",
        f"  [{ACCENT_ORANGE}]Output tokens:[/]  {_fmt_tokens(total_out):>8s}  [{TEXT_SECONDARY}](avg {_fmt_tokens(int(avg_out))}/task)[/]",
        f"  [{TEXT_SECONDARY}]Total:[/]          {_fmt_tokens(total_in + total_out):>8s}",
    ]
    return "\n".join(lines)


def format_trend_indicators(trends: list[dict]) -> str:
    """Section 5: Trend Indicators — cost and duration arrows for last 5 pipelines."""
    if len(trends) < 2:
        return f"[{TEXT_SECONDARY}]Need at least 2 pipelines for trend data[/]"

    recent = trends[:5]
    costs = [p.get("total_cost_usd", 0.0) for p in recent]
    durations = [p.get("duration_s", 0.0) for p in recent]
    retries = [float(p.get("total_retries", 0)) for p in recent]

    cost_arrow = _trend_arrow(costs)
    dur_arrow = _trend_arrow(durations)
    retry_arrow = _trend_arrow(retries)

    avg_cost = sum(costs) / len(costs)
    avg_dur = sum(durations) / len(durations)
    avg_retries = sum(retries) / len(retries)

    lines = [
        f"  {cost_arrow} [{TEXT_PRIMARY}]Cost[/]       avg ${avg_cost:.3f}/pipeline  [{TEXT_SECONDARY}](last {len(recent)})[/]",
        f"  {dur_arrow} [{TEXT_PRIMARY}]Duration[/]   avg {_fmt_duration(avg_dur)}/pipeline",
        f"  {retry_arrow} [{TEXT_PRIMARY}]Retries[/]    avg {avg_retries:.1f}/pipeline",
    ]
    return "\n".join(lines)


class StatsScreen(Screen):
    """Pipeline statistics and performance metrics display."""

    DEFAULT_CSS = """
    StatsScreen {
        layout: vertical;
    }
    #stats-header {
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #58a6ff;
    }
    #stats-body {
        padding: 1 2;
        overflow-y: auto;
    }
    .stats-section-title {
        margin: 1 0 0 0;
        color: #58a6ff;
    }
    .stats-section {
        margin: 0 0 0 0;
    }
    #stats-footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True, priority=True),
        Binding("r", "refresh_stats", "Refresh", show=True, priority=True),
    ]

    def __init__(
        self,
        stats: dict | None = None,
        trends: list[dict] | None = None,
        retry_summary: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self._stats = stats or {}
        self._trends = trends or []
        self._retry_summary = retry_summary or []

    def compose(self) -> ComposeResult:
        yield Static(f"[bold {ACCENT_BLUE}]STATS[/]", id="stats-header")
        with VerticalScroll(id="stats-body"):
            yield Static(
                f"[bold {ACCENT_BLUE}]Pipeline Performance[/]", classes="stats-section-title"
            )
            yield Static(
                format_pipeline_summary(self._trends),
                id="pipeline-summary",
                classes="stats-section",
            )
            yield Static(f"[bold {ACCENT_BLUE}]Cost Breakdown[/]", classes="stats-section-title")
            yield Static(
                format_cost_breakdown(self._stats),
                id="cost-breakdown",
                classes="stats-section",
            )
            yield Static(f"[bold {ACCENT_BLUE}]Retry Hotspots[/]", classes="stats-section-title")
            yield Static(
                format_retry_hotspots(self._retry_summary),
                id="retry-hotspots",
                classes="stats-section",
            )
            yield Static(f"[bold {ACCENT_BLUE}]Token Usage[/]", classes="stats-section-title")
            yield Static(
                format_token_usage(self._stats),
                id="token-usage",
                classes="stats-section",
            )
            yield Static(f"[bold {ACCENT_BLUE}]Trends[/]", classes="stats-section-title")
            yield Static(
                format_trend_indicators(self._trends),
                id="trend-indicators",
                classes="stats-section",
            )
        yield Static(
            "[r] refresh  [Esc] close",
            id="stats-footer",
        )
        yield ShortcutBar(
            [
                ("r", "Refresh"),
                ("Esc", "Back"),
            ]
        )

    def _refresh_content(self) -> None:
        """Update all stat sections from current data."""
        try:
            self.query_one("#pipeline-summary", Static).update(
                format_pipeline_summary(self._trends)
            )
            self.query_one("#cost-breakdown", Static).update(format_cost_breakdown(self._stats))
            self.query_one("#retry-hotspots", Static).update(
                format_retry_hotspots(self._retry_summary)
            )
            self.query_one("#token-usage", Static).update(format_token_usage(self._stats))
            self.query_one("#trend-indicators", Static).update(
                format_trend_indicators(self._trends)
            )
        except Exception:
            logger.debug("Failed to refresh stats content", exc_info=True)

    def action_close(self) -> None:
        self.app.pop_screen()

    async def action_refresh_stats(self) -> None:
        """Reload stats data from the database."""
        try:
            app = self.app
            db = getattr(app, "_db", None)
            if not db:
                return

            # Load trends
            self._trends = await db.get_pipeline_trends(limit=20)

            # Load stats for the most recent pipeline if available
            pipeline_id = getattr(app, "_pipeline_id", None)
            if pipeline_id:
                self._stats = await db.get_pipeline_stats(pipeline_id)
            elif self._trends:
                self._stats = await db.get_pipeline_stats(self._trends[0]["id"])

            # Load retry summary
            self._retry_summary = await db.get_retry_summary()

            self._refresh_content()
        except Exception:
            logger.debug("Failed to refresh stats", exc_info=True)
