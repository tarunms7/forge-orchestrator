"""Stats screen — interactive 3-panel pipeline dashboard."""

from __future__ import annotations

import logging
from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_PURPLE,
    ACCENT_RED,
    PIPELINE_STATUS_ICONS,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from forge.tui.widgets.charts import format_stats_line, render_donut_chart, render_sparkline
from forge.tui.widgets.shortcut_bar import ShortcutBar

logger = logging.getLogger("forge.tui.screens.stats")


# ---------------------------------------------------------------------------
# Formatting helpers (kept for backward compat & detail screen)
# ---------------------------------------------------------------------------


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
    """Return a trend arrow based on the last few values (newest first)."""
    if len(values) < 2:
        return f"[{TEXT_SECONDARY}]—[/]"
    mid = len(values) // 2
    recent = sum(values[:mid]) / mid
    older = sum(values[mid:]) / (len(values) - mid)
    if older == 0:
        return f"[{TEXT_SECONDARY}]—[/]"
    pct = (recent - older) / older
    if pct > 0.1:
        return f"[{ACCENT_RED}]▲[/]"
    elif pct < -0.1:
        return f"[{ACCENT_GREEN}]▼[/]"
    return f"[{TEXT_SECONDARY}]—[/]"


def format_cost_breakdown(stats: dict) -> str:
    """Cost Breakdown — show planner/agent/review cost split."""
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
        bar_len = int(pct / 2.5)
        bar = "\u2588" * bar_len + "\u2591" * (40 - bar_len)
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
    """Retry Hotspots — top 5 most-retried error patterns."""
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
            f"  [{ACCENT_RED}]{retries}\u00d7[/] [{TEXT_PRIMARY}]{pattern}[/]\n"
            f"      [{TEXT_SECONDARY}]{task_count} task(s): {ids_str}[/]"
        )
    return "\n".join(lines)


def format_token_usage(stats: dict) -> str:
    """Token Usage — total input/output tokens with avg per task."""
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


def format_pipeline_summary(trends: list[dict]) -> str:
    """Legacy formatter kept for backward compatibility."""
    if not trends:
        return f"[{TEXT_SECONDARY}]No pipeline data available[/]"

    _STATUS_ICONS = {
        "done": ("\u2714", ACCENT_GREEN),
        "complete": ("\u2714", ACCENT_GREEN),
        "executing": ("\u25cf", ACCENT_ORANGE),
        "planning": ("\u25cc", ACCENT_BLUE),
        "error": ("\u2716", ACCENT_RED),
        "cancelled": ("\u2716", ACCENT_RED),
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
        task_info = f"{succeeded}\u2713"
        if failed:
            task_info += f" {failed}\u2717"
        lines.append(
            f"  [{color}]{icon}[/] {desc:<40s}  [{TEXT_SECONDARY}]{dur:>8s}  {cost:>7s}  {task_info}[/]"
        )
    return "\n".join(lines)


def format_trend_indicators(trends: list[dict]) -> str:
    """Trend Indicators — cost and duration arrows for last 5 pipelines."""
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


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------


def _fmt_timestamp(created_at: str | None) -> str:
    """Format ISO datetime to 'Mar 23 10:15'."""
    if not created_at:
        return "—"
    try:
        dt = datetime.fromisoformat(created_at)
        return dt.strftime("%b %d %H:%M")
    except (ValueError, TypeError):
        return "—"


# ---------------------------------------------------------------------------
# PurgeConfirmScreen — minimal y/n confirmation
# ---------------------------------------------------------------------------


class PurgeConfirmScreen(Screen):
    """Confirmation screen for purging old pipelines."""

    DEFAULT_CSS = """
    PurgeConfirmScreen {
        align: center middle;
    }
    #purge-dialog {
        width: 50;
        height: 5;
        padding: 1 2;
        background: #161b22;
        border: tall #30363d;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=True),
        Binding("n", "cancel", "No", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, callback: object = None) -> None:
        super().__init__()
        self._callback = callback

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold {TEXT_PRIMARY}]Delete pipelines older than 30 days? [y/n][/]",
            id="purge-dialog",
        )

    def action_confirm(self) -> None:
        self.app.pop_screen()
        if self._callback:
            self._callback()

    def action_cancel(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# PipelineDetailScreen — read-only detail view for a single pipeline
# ---------------------------------------------------------------------------


class PipelineDetailScreen(Screen):
    """Read-only detail view showing full stats for a single pipeline."""

    DEFAULT_CSS = """
    PipelineDetailScreen {
        layout: vertical;
    }
    #detail-header {
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #58a6ff;
    }
    #detail-body {
        padding: 1 2;
        overflow-y: auto;
    }
    .detail-section-title {
        margin: 1 0 0 0;
        color: #58a6ff;
    }
    .detail-section {
        margin: 0 0 0 0;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True, priority=True),
    ]

    def __init__(self, stats: dict, retry_summary: list[dict] | None = None) -> None:
        super().__init__()
        self._stats = stats
        self._retry_summary: list[dict] = retry_summary or []

    def compose(self) -> ComposeResult:
        desc = self._stats.get("description", "Pipeline Details")
        yield Static(f"[bold {ACCENT_BLUE}]{desc}[/]", id="detail-header")
        with VerticalScroll(id="detail-body"):
            yield Static(
                f"[bold {ACCENT_BLUE}]Cost Breakdown[/]",
                classes="detail-section-title",
            )
            yield Static(
                format_cost_breakdown(self._stats),
                classes="detail-section",
            )
            yield Static(
                f"[bold {ACCENT_BLUE}]Token Usage[/]",
                classes="detail-section-title",
            )
            yield Static(
                format_token_usage(self._stats),
                classes="detail-section",
            )
            yield Static(
                f"[bold {ACCENT_BLUE}]Retry Hotspots[/]",
                classes="detail-section-title",
            )
            yield Static(
                format_retry_hotspots(self._retry_summary),
                classes="detail-section",
            )
        yield ShortcutBar([("Esc", "Back")])

    def action_close(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# StatsScreen — main 3-panel dashboard
# ---------------------------------------------------------------------------


class StatsScreen(Screen):
    """Pipeline statistics — interactive 3-panel dashboard."""

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
    #stats-loading {
        height: 3;
        content-align: center middle;
        padding: 1 0;
    }
    #pipeline-table {
        height: 1fr;
        min-height: 8;
    }
    #bottom-panels {
        height: 1fr;
        min-height: 8;
    }
    #trends-panel {
        width: 1fr;
        padding: 1 2;
        overflow-y: auto;
    }
    #success-panel {
        width: 1fr;
        padding: 1 2;
        overflow-y: auto;
    }
    .panel-title {
        margin: 0 0 1 0;
        color: #58a6ff;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True, priority=True),
        Binding("r", "refresh_stats", "Refresh", show=True, priority=True),
        Binding("p", "purge_old", "Purge old", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, db: object = None) -> None:
        super().__init__()
        self._db = db
        self._trends: list[dict] = []
        self._analytics: dict = {}
        self._pipeline_ids: list[str] = []
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Static(f"[bold {ACCENT_BLUE}]STATS[/]", id="stats-header")
        yield Static(
            f"[{TEXT_SECONDARY}]\u23f3 Loading pipeline data...[/]",
            id="stats-loading",
        )
        yield DataTable(id="pipeline-table")
        with Horizontal(id="bottom-panels"):
            with Vertical(id="trends-panel"):
                yield Static(
                    f"[bold {ACCENT_BLUE}]Cost & Duration Trends[/]",
                    classes="panel-title",
                )
                yield Static("", id="cost-sparkline")
                yield Static("", id="duration-sparkline")
            with Vertical(id="success-panel"):
                yield Static(
                    f"[bold {ACCENT_BLUE}]Success Rate[/]",
                    classes="panel-title",
                )
                yield Static("", id="donut-chart")
                yield Static("", id="streak-info")
        yield ShortcutBar(
            [
                ("j/k", "Navigate"),
                ("\u21b5", "Details"),
                ("r", "Refresh"),
                ("p", "Purge"),
                ("Esc", "Back"),
            ]
        )

    async def on_mount(self) -> None:
        # Hide table and panels until data loads
        self.query_one("#pipeline-table", DataTable).display = False
        self.query_one("#bottom-panels", Horizontal).display = False

        # Set up the DataTable columns
        table = self.query_one("#pipeline-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "Timestamp",
            "Task",
            "Tasks",
            "Pass",
            "Fail",
            "Cost",
            "Duration",
            "Status",
        )

        await self._load_data()
        self._refresh_timer = self.set_interval(30, self._auto_refresh)

    async def _load_data(self) -> None:
        """Load all data from the database and populate panels."""
        if not self._db:
            self._show_empty()
            return

        try:
            self._trends = await self._db.get_pipeline_trends(limit=20)
        except Exception:
            logger.debug("Failed to load pipeline trends", exc_info=True)
            self._trends = []

        try:
            self._analytics = await self._db.get_pipeline_analytics()
        except Exception:
            logger.debug("Failed to load pipeline analytics", exc_info=True)
            self._analytics = {}

        self._populate_table()
        self._populate_trends()
        self._populate_success_rate()

        # Hide loading, show panels
        self.query_one("#stats-loading", Static).display = False
        self.query_one("#pipeline-table", DataTable).display = True
        self.query_one("#bottom-panels", Horizontal).display = True

    def _show_empty(self) -> None:
        """Show empty state when no db or no data."""
        self.query_one("#stats-loading", Static).update(
            f"[{TEXT_SECONDARY}]No pipeline data available[/]"
        )

    def _populate_table(self) -> None:
        """Fill the DataTable with pipeline trends data."""
        table = self.query_one("#pipeline-table", DataTable)
        table.clear()
        self._pipeline_ids = []

        if not self._trends:
            return

        for p in self._trends:
            self._pipeline_ids.append(p.get("id", ""))
            ts = _fmt_timestamp(p.get("created_at"))
            desc = (p.get("description", "Untitled") or "Untitled")[:40]
            total_tasks = p.get("total_tasks", 0)
            succeeded = p.get("tasks_succeeded", 0)
            failed = p.get("tasks_failed", 0)
            cost = f"${p.get('total_cost_usd', 0.0):.2f}"
            dur = _fmt_duration(p.get("duration_s", 0.0))

            status = p.get("status", "unknown")
            icon, color = PIPELINE_STATUS_ICONS.get(status, ("?", TEXT_SECONDARY))
            status_badge = f"[{color}]{icon} {status}[/]"

            table.add_row(
                ts, desc, str(total_tasks), str(succeeded), str(failed), cost, dur, status_badge
            )

    def _populate_trends(self) -> None:
        """Render cost and duration sparklines."""
        if not self._trends:
            self.query_one("#cost-sparkline", Static).update(f"[{TEXT_SECONDARY}]No trend data[/]")
            self.query_one("#duration-sparkline", Static).update("")
            return

        costs = [p.get("total_cost_usd", 0.0) for p in self._trends]
        durations = [p.get("duration_s", 0.0) for p in self._trends]

        cost_spark = render_sparkline(costs, width=30, color=ACCENT_GREEN)
        cost_stats = format_stats_line(
            label="Cost",
            min_val=min(costs),
            avg_val=sum(costs) / len(costs),
            max_val=max(costs),
            fmt_fn=lambda x: f"${x:.2f}",
        )
        self.query_one("#cost-sparkline", Static).update(
            f"  [{TEXT_SECONDARY}]Cost:[/]\n  {cost_spark.split(chr(10))[0]}\n  {cost_stats}"
        )

        dur_spark = render_sparkline(durations, width=30, color=ACCENT_BLUE)
        dur_stats = format_stats_line(
            label="Duration",
            min_val=min(durations),
            avg_val=sum(durations) / len(durations),
            max_val=max(durations),
            fmt_fn=lambda x: _fmt_duration(x),
        )
        self.query_one("#duration-sparkline", Static).update(
            f"  [{TEXT_SECONDARY}]Duration:[/]\n  {dur_spark.split(chr(10))[0]}\n  {dur_stats}"
        )

    def _populate_success_rate(self) -> None:
        """Render donut chart and streak info from analytics."""
        a = self._analytics
        if not a:
            self.query_one("#donut-chart", Static).update(f"[{TEXT_SECONDARY}]No analytics data[/]")
            self.query_one("#streak-info", Static).update("")
            return

        passed = a.get("passed", 0)
        failed = a.get("failed", 0)
        partial = a.get("partial", 0)

        segments = [
            ("Pass", passed, ACCENT_GREEN),
            ("Fail", failed, ACCENT_RED),
            ("Partial", partial, ACCENT_ORANGE),
        ]
        donut = render_donut_chart(segments)
        self.query_one("#donut-chart", Static).update(f"  {donut.replace(chr(10), chr(10) + '  ')}")

        current_streak = a.get("current_streak", 0)
        longest_streak = a.get("longest_streak", 0)
        self.query_one("#streak-info", Static).update(
            f"\n  [{TEXT_PRIMARY}]Current streak:[/] [{ACCENT_GREEN}]{current_streak}[/] consecutive successes\n"
            f"  [{TEXT_PRIMARY}]Longest streak:[/] [{ACCENT_GREEN}]{longest_streak}[/]"
        )

    async def _auto_refresh(self) -> None:
        """Timer callback to reload data."""
        try:
            await self._load_data()
        except Exception:
            logger.debug("Auto-refresh failed", exc_info=True)

    # -- Actions ---------------------------------------------------------------

    def action_close(self) -> None:
        self.app.pop_screen()

    async def action_refresh_stats(self) -> None:
        """Reload stats data from the database."""
        await self._load_data()

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#pipeline-table", DataTable).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#pipeline-table", DataTable).action_cursor_up()
        except Exception:
            pass

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter on a pipeline row — push detail screen."""
        row_index = event.cursor_row
        if row_index < 0 or row_index >= len(self._pipeline_ids):
            return
        pipeline_id = self._pipeline_ids[row_index]
        if not self._db or not pipeline_id:
            return

        try:
            stats = await self._db.get_pipeline_stats(pipeline_id)
            if stats:
                retry_summary: list[dict] = []
                try:
                    retry_summary = await self._db.get_retry_summary(pipeline_id)
                except Exception:
                    logger.debug("Failed to load retry summary", exc_info=True)
                self.app.push_screen(PipelineDetailScreen(stats=stats, retry_summary=retry_summary))
        except Exception:
            logger.debug("Failed to load pipeline detail", exc_info=True)

    def action_purge_old(self) -> None:
        """Show purge confirmation prompt."""
        from forge.core.async_utils import safe_create_task

        def _do_purge() -> None:
            safe_create_task(self._execute_purge(), logger=logger, name="purge-pipelines")

        self.app.push_screen(PurgeConfirmScreen(callback=_do_purge))

    async def _execute_purge(self) -> None:
        """Actually purge old pipelines after confirmation."""
        if not self._db:
            return
        try:
            count = await self._db.purge_old_pipelines(30)
            self.notify(f"Purged {count} pipeline(s) older than 30 days")
            await self._load_data()
        except Exception:
            logger.debug("Failed to purge pipelines", exc_info=True)
            self.notify("Failed to purge pipelines", severity="error")
