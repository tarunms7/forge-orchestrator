"""Forge CLI costs command. Show per-pipeline cost breakdown with Rich output."""

from __future__ import annotations

import asyncio
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


def _fmt_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost <= 0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


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


def _truncate(s: str, max_len: int) -> str:
    """Truncate string to max_len with ellipsis."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _fmt_tokens(input_t: int, output_t: int) -> str:
    """Format token counts compactly."""

    def _compact(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    return f"{_compact(input_t)}/{_compact(output_t)}"


@click.command("costs")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option("--pipeline", "pipeline_id", default=None, help="Drill-down into a specific pipeline")
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Show pipelines from all projects",
)
def costs(
    project_dir: str,
    pipeline_id: str | None,
    show_all: bool,
) -> None:
    """Show per-pipeline cost breakdown."""
    project_dir = os.path.abspath(project_dir)
    filter_path = None if show_all else project_dir

    if pipeline_id:
        _show_pipeline_costs(pipeline_id)
    else:
        _show_cost_overview(filter_path)


def _show_cost_overview(project_path: str | None) -> None:
    """Default view: recent pipelines with cost breakdown columns."""

    async def _fetch():
        db = _get_db()
        await db.initialize()
        try:
            pipelines = await db.get_pipeline_trends(project_path=project_path, limit=20)
            # Fetch per-pipeline stats for cost breakdown
            detailed = []
            for p in pipelines:
                stats = await db.get_pipeline_stats(p["id"])
                if stats:
                    detailed.append(stats)
            return detailed
        finally:
            await db.close()

    try:
        pipelines = asyncio.run(_fetch())
    except Exception as e:
        click.echo(f"Error reading costs: {e}")
        raise SystemExit(1)

    if not pipelines:
        click.echo("No pipelines found.")
        return

    console = Console()

    table = Table(title="Pipeline Cost Breakdown")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Status")
    table.add_column("Planner", justify="right")
    table.add_column("Agent", justify="right")
    table.add_column("Review", justify="right")
    table.add_column("CI Fix", justify="right")
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Created")

    total_all = 0.0
    for p in pipelines:
        color = _STATUS_COLORS.get(p["status"], "white")
        status_text = p["status"].replace("[", "\\[")
        desc = _truncate(p["description"].replace("[", "\\["), 40)
        created = (p.get("created_at") or "")[:10]

        planner_cost = p.get("planner_cost_usd", 0.0)
        tasks = p.get("tasks", [])
        agent_cost = sum(t.get("agent_cost_usd", 0.0) for t in tasks)
        review_cost = sum(t.get("review_cost_usd", 0.0) for t in tasks)
        total_cost = p.get("total_cost_usd", 0.0)
        # CI fix cost = total minus known cost categories
        ci_fix_cost = max(0.0, total_cost - planner_cost - agent_cost - review_cost)

        total_all += total_cost

        table.add_row(
            p["id"][:8],
            desc,
            f"[{color}]{status_text}[/{color}]",
            _fmt_cost(planner_cost),
            _fmt_cost(agent_cost),
            _fmt_cost(review_cost),
            _fmt_cost(ci_fix_cost),
            _fmt_cost(total_cost),
            created,
        )

    console.print(table)

    # Summary panel
    avg_cost = total_all / len(pipelines) if pipelines else 0
    summary = (
        f"[bold]Pipelines:[/bold] {len(pipelines)}  |  "
        f"[bold]Total cost:[/bold] {_fmt_cost(total_all)}  |  "
        f"[bold]Avg cost:[/bold] {_fmt_cost(avg_cost)}"
    )
    console.print(Panel(summary, title="Cost Summary", border_style="blue"))


def _show_pipeline_costs(pipeline_id: str) -> None:
    """Drill-down view: stage breakdown, top tasks, burn rate trend."""

    async def _fetch():
        db = _get_db()
        await db.initialize()
        try:
            stats = await db.get_pipeline_stats(pipeline_id)
            events = await db.list_events(pipeline_id, event_type="pipeline:cost_update")
            return stats, events
        finally:
            await db.close()

    try:
        data, events = asyncio.run(_fetch())
    except Exception as e:
        click.echo(f"Error reading pipeline costs: {e}")
        raise SystemExit(1)

    if not data:
        click.echo(f"Pipeline '{pipeline_id}' not found.")
        return

    console = Console()

    # ── 1. Stage cost breakdown panel ────────────────────────────────
    tasks = data.get("tasks", [])
    planner_cost = data.get("planner_cost_usd", 0.0)
    agent_cost = sum(t.get("agent_cost_usd", 0.0) for t in tasks)
    review_cost = sum(t.get("review_cost_usd", 0.0) for t in tasks)
    total_cost = data.get("total_cost_usd", 0.0)
    ci_fix_cost = max(0.0, total_cost - planner_cost - agent_cost - review_cost)

    stage_table = Table(title="Stage Cost Breakdown")
    stage_table.add_column("Stage")
    stage_table.add_column("Cost", justify="right")
    stage_table.add_column("% of Total", justify="right")

    for label, cost in [
        ("Planner", planner_cost),
        ("Agent", agent_cost),
        ("Review", review_cost),
        ("CI Fix", ci_fix_cost),
    ]:
        pct = f"{cost / total_cost * 100:.1f}%" if total_cost > 0 else "-"
        stage_table.add_row(label, _fmt_cost(cost), pct)

    stage_table.add_row("[bold]Total[/bold]", f"[bold]{_fmt_cost(total_cost)}[/bold]", "100%")

    color = _STATUS_COLORS.get(data["status"], "white")
    header = (
        f"[bold]{data['description']}[/bold]\n"
        f"Status: [{color}]{data['status']}[/{color}]  |  "
        f"Duration: {_fmt_duration(data.get('duration_s', 0.0))}"
    )
    console.print(Panel(header, title=f"Pipeline {data['id'][:8]}", border_style="cyan"))
    console.print(stage_table)

    # ── 2. Top 5 most expensive tasks ────────────────────────────────
    if tasks:
        sorted_tasks = sorted(tasks, key=lambda t: t.get("cost_usd", 0.0), reverse=True)[:5]

        task_table = Table(title="Top 5 Most Expensive Tasks")
        task_table.add_column("Task ID", style="cyan", no_wrap=True)
        task_table.add_column("Title")
        task_table.add_column("Agent Cost", justify="right")
        task_table.add_column("Review Cost", justify="right")
        task_table.add_column("Total Cost", justify="right", style="bold")
        task_table.add_column("Tokens (in/out)", justify="right")

        for t in sorted_tasks:
            task_table.add_row(
                t["id"][:8],
                _truncate(t["title"].replace("[", "\\["), 40),
                _fmt_cost(t.get("agent_cost_usd", 0.0)),
                _fmt_cost(t.get("review_cost_usd", 0.0)),
                _fmt_cost(t.get("cost_usd", 0.0)),
                _fmt_tokens(t.get("input_tokens", 0), t.get("output_tokens", 0)),
            )

        console.print(task_table)

    # ── 3. Burn rate trend (last 10 cost_update events) ──────────────
    if events:
        # Events are PipelineEventRow ORM objects, ordered by created_at ASC
        recent = events[-10:]

        burn_table = Table(title="Cost Burn Rate (recent)")
        burn_table.add_column("Timestamp")
        burn_table.add_column("Cumulative Cost", justify="right")
        burn_table.add_column("Delta", justify="right")

        prev_cost = 0.0
        for evt in recent:
            payload = evt.payload if isinstance(evt.payload, dict) else {}
            cumulative = payload.get("total_cost_usd", 0.0)
            delta = cumulative - prev_cost
            ts = str(evt.created_at)[:19] if evt.created_at else "-"

            burn_table.add_row(
                ts,
                _fmt_cost(cumulative),
                _fmt_cost(delta) if delta > 0 else "-",
            )
            prev_cost = cumulative

        console.print(burn_table)
    else:
        console.print("[dim]No cost update events found for this pipeline.[/dim]")
