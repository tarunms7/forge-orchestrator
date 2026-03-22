"""Forge CLI stats command. Show pipeline analytics with Rich output."""

from __future__ import annotations

import asyncio
import json
import os

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def _get_db():
    from forge.core.paths import forge_db_url
    from forge.storage.db import Database

    return Database(forge_db_url())


_STATUS_COLORS = {
    "planning": "yellow",
    "planned": "blue",
    "executing": "cyan",
    "done": "green",
    "complete": "green",
    "error": "red",
    "cancelled": "dim",
}

_TASK_STATE_COLORS = {
    "todo": "dim",
    "in_progress": "cyan",
    "review": "yellow",
    "done": "green",
    "error": "red",
    "cancelled": "dim",
    "awaiting_input": "magenta",
}


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _fmt_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost <= 0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _fmt_tokens(input_t: int, output_t: int) -> str:
    """Format token counts compactly."""

    def _compact(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    return f"{_compact(input_t)}/{_compact(output_t)}"


def _truncate(s: str, max_len: int) -> str:
    """Truncate string to max_len with ellipsis."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _trend_arrow(current: float, avg: float) -> str:
    """Return trend arrow comparing current to rolling average."""
    if avg <= 0:
        return "→"
    ratio = current / avg
    if ratio > 1.15:
        return "[red]↑[/red]"
    elif ratio < 0.85:
        return "[green]↓[/green]"
    return "→"


@click.command("stats")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Show pipelines from all projects",
)
@click.option("--pipeline", "pipeline_id", default=None, help="Drill-down into a specific pipeline")
@click.option("--trends", is_flag=True, default=False, help="Show trend analysis")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
def stats(
    project_dir: str,
    show_all: bool,
    pipeline_id: str | None,
    trends: bool,
    as_json: bool,
) -> None:
    """Show pipeline analytics and metrics."""
    project_dir = os.path.abspath(project_dir)
    filter_path = None if show_all else project_dir

    if pipeline_id:
        _show_pipeline_drilldown(pipeline_id, as_json)
    elif trends:
        _show_trends(filter_path, as_json)
    else:
        _show_overview(filter_path, as_json)


def _show_overview(project_path: str | None, as_json: bool) -> None:
    """Default view: recent pipelines table + summary panel."""

    async def _fetch():
        db = _get_db()
        await db.initialize()
        try:
            return await db.get_pipeline_trends(project_path=project_path, limit=20)
        finally:
            await db.close()

    try:
        pipelines = asyncio.run(_fetch())
    except Exception as e:
        click.echo(f"Error reading stats: {e}")
        raise SystemExit(1)

    if not pipelines:
        click.echo("No pipelines found.")
        return

    if as_json:
        click.echo(json.dumps(pipelines, indent=2, default=str))
        return

    console = Console()

    # Pipeline overview table
    table = Table(title="Pipeline Overview")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Status")
    table.add_column("Tasks", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Tokens (in/out)", justify="right")
    table.add_column("Retries", justify="right")
    table.add_column("Created")

    for p in pipelines:
        color = _STATUS_COLORS.get(p["status"], "white")
        status_text = p["status"].replace("[", "\\[")
        desc = _truncate(p["description"].replace("[", "\\["), 40)
        total_tasks = p["tasks_succeeded"] + p["tasks_failed"]
        tasks_str = f"{p['tasks_succeeded']}/{total_tasks}" if total_tasks > 0 else "-"
        created = (p.get("created_at") or "")[:10]  # date only

        table.add_row(
            p["id"][:8],
            desc,
            f"[{color}]{status_text}[/{color}]",
            tasks_str,
            _fmt_duration(p["duration_s"]),
            _fmt_cost(p["total_cost_usd"]),
            _fmt_tokens(p["total_input_tokens"], p["total_output_tokens"]),
            str(p["total_retries"]) if p["total_retries"] > 0 else "-",
            created,
        )

    console.print(table)

    # Summary panel
    total_cost = sum(p["total_cost_usd"] for p in pipelines)
    avg_cost = total_cost / len(pipelines) if pipelines else 0
    durations = [p["duration_s"] for p in pipelines if p["duration_s"] > 0]
    avg_duration = sum(durations) / len(durations) if durations else 0
    fastest = min(durations) if durations else 0
    slowest = max(durations) if durations else 0
    total_retries = sum(p["total_retries"] for p in pipelines)
    total_tasks = sum(p["tasks_succeeded"] + p["tasks_failed"] for p in pipelines)
    retry_rate = total_retries / total_tasks if total_tasks > 0 else 0
    total_tokens = sum(p["total_input_tokens"] + p["total_output_tokens"] for p in pipelines)
    avg_tokens_per_task = total_tokens / total_tasks if total_tasks > 0 else 0

    summary_lines = [
        f"[bold]Pipelines:[/bold] {len(pipelines)}  |  "
        f"[bold]Total cost:[/bold] {_fmt_cost(total_cost)}  |  "
        f"[bold]Avg cost:[/bold] {_fmt_cost(avg_cost)}",
        f"[bold]Avg duration:[/bold] {_fmt_duration(avg_duration)}  |  "
        f"[bold]Fastest:[/bold] {_fmt_duration(fastest)}  |  "
        f"[bold]Slowest:[/bold] {_fmt_duration(slowest)}",
        f"[bold]Total retries:[/bold] {total_retries}  |  "
        f"[bold]Retry rate:[/bold] {retry_rate:.1%}  |  "
        f"[bold]Avg tokens/task:[/bold] {int(avg_tokens_per_task):,}",
    ]

    console.print(Panel("\n".join(summary_lines), title="Summary", border_style="blue"))


def _show_pipeline_drilldown(pipeline_id: str, as_json: bool) -> None:
    """Drill-down view for a specific pipeline."""

    async def _fetch():
        db = _get_db()
        await db.initialize()
        try:
            return await db.get_pipeline_stats(pipeline_id)
        finally:
            await db.close()

    try:
        data = asyncio.run(_fetch())
    except Exception as e:
        click.echo(f"Error reading pipeline stats: {e}")
        raise SystemExit(1)

    if not data:
        click.echo(f"Pipeline '{pipeline_id}' not found.")
        return

    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    console = Console()

    # Pipeline header
    color = _STATUS_COLORS.get(data["status"], "white")
    header_lines = [
        f"[bold]{data['description']}[/bold]",
        f"Status: [{color}]{data['status']}[/{color}]  |  "
        f"Duration: {_fmt_duration(data['duration_s'])}  |  "
        f"Total cost: {_fmt_cost(data['total_cost_usd'])}  |  "
        f"Planner cost: {_fmt_cost(data['planner_cost_usd'])}",
        f"Tasks: {data['tasks_succeeded']} done, {data['tasks_failed']} failed  |  "
        f"Tokens: {_fmt_tokens(data['total_input_tokens'], data['total_output_tokens'])}  |  "
        f"Retries: {data['total_retries']}",
    ]
    console.print(
        Panel("\n".join(header_lines), title=f"Pipeline {data['id'][:8]}", border_style="cyan")
    )

    tasks = data.get("tasks", [])
    if not tasks:
        click.echo("No tasks in this pipeline.")
        return

    # Per-task table
    table = Table(title="Task Metrics")
    table.add_column("Task ID", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("State")
    table.add_column("Agent", justify="right")
    table.add_column("Review", justify="right")
    table.add_column("Lint", justify="right")
    table.add_column("Merge", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Retries", justify="right")
    table.add_column("Turns", justify="right")

    for t in tasks:
        state_color = _TASK_STATE_COLORS.get(t["state"], "white")
        total_time = (
            t["agent_duration_s"]
            + t["review_duration_s"]
            + t["lint_duration_s"]
            + t["merge_duration_s"]
        )
        turns_str = (
            f"{t['num_turns']}/{t['max_turns']}" if t["max_turns"] > 0 else str(t["num_turns"])
        )

        table.add_row(
            t["id"][:8],
            _truncate(t["title"].replace("[", "\\["), 30),
            f"[{state_color}]{t['state']}[/{state_color}]",
            _fmt_duration(t["agent_duration_s"]),
            _fmt_duration(t["review_duration_s"]),
            _fmt_duration(t["lint_duration_s"]),
            _fmt_duration(t["merge_duration_s"]),
            _fmt_duration(total_time),
            _fmt_cost(t["cost_usd"]),
            _fmt_tokens(t["input_tokens"], t["output_tokens"]),
            str(t["retry_count"]) if t["retry_count"] > 0 else "-",
            turns_str,
        )

    console.print(table)

    # Timing waterfall
    _show_waterfall(console, tasks)


def _show_waterfall(console: Console, tasks: list[dict]) -> None:
    """Show a timing waterfall using Rich bars."""
    if not tasks:
        return

    # Find max total time for scaling
    max_time = max(
        (
            t["agent_duration_s"]
            + t["review_duration_s"]
            + t["lint_duration_s"]
            + t["merge_duration_s"]
        )
        for t in tasks
    )
    if max_time <= 0:
        return

    bar_width = 40
    lines = []

    for t in tasks:
        agent = t["agent_duration_s"]
        review = t["review_duration_s"]
        lint = t["lint_duration_s"]
        merge = t["merge_duration_s"]
        total = agent + review + lint + merge
        if total <= 0:
            continue

        # Scale each phase to bar_width
        scale = bar_width / max_time
        a_len = max(1, int(agent * scale)) if agent > 0 else 0
        r_len = max(1, int(review * scale)) if review > 0 else 0
        l_len = max(1, int(lint * scale)) if lint > 0 else 0
        m_len = max(1, int(merge * scale)) if merge > 0 else 0

        bar = (
            f"[cyan]{'█' * a_len}[/cyan]"
            f"[yellow]{'█' * r_len}[/yellow]"
            f"[blue]{'█' * l_len}[/blue]"
            f"[green]{'█' * m_len}[/green]"
        )
        remaining = bar_width - (a_len + r_len + l_len + m_len)
        if remaining > 0:
            bar += f"{'░' * remaining}"

        detail = f"  agent: {_fmt_duration(agent)}  review: {_fmt_duration(review)}"
        if merge > 0:
            detail += f"  merge: {_fmt_duration(merge)}"
        lines.append(f"  {t['id'][:8]}  {bar}{detail}")

    if lines:
        legend = "  [cyan]█[/cyan] agent  [yellow]█[/yellow] review  [blue]█[/blue] lint  [green]█[/green] merge"
        lines.append("")
        lines.append(legend)
        console.print(Panel("\n".join(lines), title="Timing Waterfall", border_style="dim"))


def _show_trends(project_path: str | None, as_json: bool) -> None:
    """Trend view: last 20 pipelines with trend arrows."""

    async def _fetch():
        db = _get_db()
        await db.initialize()
        try:
            return await db.get_pipeline_trends(project_path=project_path, limit=20)
        finally:
            await db.close()

    try:
        pipelines = asyncio.run(_fetch())
    except Exception as e:
        click.echo(f"Error reading trends: {e}")
        raise SystemExit(1)

    if not pipelines:
        click.echo("No pipelines found for trend analysis.")
        return

    if as_json:
        click.echo(json.dumps(pipelines, indent=2, default=str))
        return

    console = Console()

    # Compute rolling averages (over all pipelines)
    avg_duration = sum(p["duration_s"] for p in pipelines) / len(pipelines) if pipelines else 0
    avg_cost = sum(p["total_cost_usd"] for p in pipelines) / len(pipelines) if pipelines else 0

    table = Table(title="Pipeline Trends (recent first)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("", no_wrap=True)  # duration trend
    table.add_column("Cost", justify="right")
    table.add_column("", no_wrap=True)  # cost trend
    table.add_column("Tasks", justify="right")
    table.add_column("Retries", justify="right")
    table.add_column("Created")

    for p in pipelines:
        color = _STATUS_COLORS.get(p["status"], "white")
        status_text = p["status"].replace("[", "\\[")
        desc = _truncate(p["description"].replace("[", "\\["), 40)
        total_tasks = p["tasks_succeeded"] + p["tasks_failed"]
        tasks_str = f"{p['tasks_succeeded']}/{total_tasks}" if total_tasks > 0 else "-"
        created = (p.get("created_at") or "")[:10]

        dur_arrow = _trend_arrow(p["duration_s"], avg_duration)
        cost_arrow = _trend_arrow(p["total_cost_usd"], avg_cost)

        table.add_row(
            p["id"][:8],
            desc,
            f"[{color}]{status_text}[/{color}]",
            _fmt_duration(p["duration_s"]),
            dur_arrow,
            _fmt_cost(p["total_cost_usd"]),
            cost_arrow,
            tasks_str,
            str(p["total_retries"]) if p["total_retries"] > 0 else "-",
            created,
        )

    console.print(table)
