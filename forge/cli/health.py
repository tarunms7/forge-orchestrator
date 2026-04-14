"""Forge CLI health command. Live-polling dashboard for pipeline health."""

from __future__ import annotations

import asyncio
import json
import os
import time

import click
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

# ── Formatting helpers (mirror forge.cli.stats patterns) ─────────────


def _fmt_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost <= 0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _fmt_tokens(n: int) -> str:
    """Format a single token count compactly."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ── State colors (same as forge.tui.widgets.dag) ────────────────────

_STATE_COLORS: dict[str, str] = {
    "todo": "#8b949e",
    "in_progress": "#f0883e",
    "in_review": "#a371f7",
    "awaiting_approval": "#d29922",
    "merging": "#79c0ff",
    "done": "#3fb950",
    "cancelled": "#8b949e",
    "error": "#f85149",
}

_CONTEXT_PRESSURE_COLORS: dict[str, str] = {
    "normal": "green",
    "elevated": "yellow",
    "high": "#ff8800",
    "critical": "red",
}


# ── Database helper ──────────────────────────────────────────────────


def _get_db():
    from forge.core.paths import forge_db_url
    from forge.storage.db import Database

    return Database(forge_db_url())


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


# ── Pure functions (exported for testing) ────────────────────────────


def build_health_dag(tasks: list[dict]) -> str:
    """Build a Rich-markup DAG showing task dependencies with state colors.

    Parameters
    ----------
    tasks:
        List of HealthTaskDict dicts.  Each must have at minimum:
        id (str), title (str), state (str), depends_on (list[str]).

    Returns
    -------
    str
        Rich-markup-formatted string.  Returns ``'[#8b949e]No tasks[/]'``
        when the tasks list is empty.  Otherwise newline-joined lines with
        state-colored bullets, task ID, title, and optional dependency
        arrows (``<- dep_id``).
    """
    if not tasks:
        return "[#8b949e]No tasks[/]"

    task_map = {t["id"]: t for t in tasks}
    lines: list[str] = []
    for task in tasks:
        color = _STATE_COLORS.get(task.get("state", "todo"), "#8b949e")
        deps = task.get("depends_on", [])
        title = task.get("title", task["id"])
        short_title = title[:30] + "\u2026" if len(title) > 30 else title
        escaped_title = _escape(short_title)
        escaped_id = _escape(task["id"])
        if deps:
            dep_str = ", ".join(_escape(d) for d in deps if d in task_map)
            if dep_str:
                lines.append(
                    f"  [{color}]\u25cf[/] {escaped_id}: {escaped_title} "
                    f"[#8b949e]\u2190 {dep_str}[/]"
                )
            else:
                lines.append(f"  [{color}]\u25cf[/] {escaped_id}: {escaped_title}")
        else:
            lines.append(f"  [{color}]\u25cf[/] {escaped_id}: {escaped_title}")
    return "\n".join(lines)


def build_cost_table(tasks: list[dict], pipeline: dict) -> Table:
    """Build a Rich Table showing per-agent token usage and cost.

    Parameters
    ----------
    tasks:
        List of HealthTaskDict dicts.
    pipeline:
        HealthPipelineDict with total_cost_usd, total_input_tokens,
        total_output_tokens for the footer row.

    Returns
    -------
    rich.table.Table
        Table with columns: Agent ID, Task, Input Tokens, Output Tokens,
        Cost, Model.  Footer row shows totals.
    """
    table = Table(title="Agent Token Usage & Cost", expand=True)
    table.add_column("Agent ID", style="cyan", no_wrap=True)
    table.add_column("Task", no_wrap=True)
    table.add_column("Input Tokens", justify="right")
    table.add_column("Output Tokens", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Model")

    for t in tasks:
        agent = t.get("assigned_agent") or "-"
        task_id = t.get("id", "?")
        in_tok = t.get("input_tokens", 0)
        out_tok = t.get("output_tokens", 0)
        cost = t.get("cost_usd", 0.0)

        # Extract model from model_history (latest entry)
        model = "-"
        mh = t.get("model_history", [])
        if isinstance(mh, list) and mh:
            latest = mh[-1] if isinstance(mh[-1], dict) else {}
            model = latest.get("model", latest.get("canonical_model_id", "-")) or "-"

        table.add_row(
            _escape(str(agent)),
            _escape(task_id),
            _fmt_tokens(in_tok),
            _fmt_tokens(out_tok),
            _fmt_cost(cost),
            _escape(str(model)),
        )

    # Footer row with totals
    table.add_row(
        "[bold]Total[/bold]",
        "",
        _fmt_tokens(pipeline.get("total_input_tokens", 0)),
        _fmt_tokens(pipeline.get("total_output_tokens", 0)),
        _fmt_cost(pipeline.get("total_cost_usd", 0.0)),
        "",
        style="bold",
    )
    return table


def build_context_panel(tasks: list[dict]) -> str:
    """Build Rich-markup showing context pressure for active tasks.

    Parameters
    ----------
    tasks:
        List of HealthTaskDict dicts.  Only tasks with
        state='in_progress' are displayed.

    Returns
    -------
    str
        Rich-markup string.  ``'No active agents'`` when no in_progress
        tasks exist.  Otherwise newline-joined lines formatted as:
        ``'{agent} ({task_id}): [{color}]{pressure}[/{color}] {pct}%'``
    """
    active = [t for t in tasks if t.get("state") == "in_progress"]
    if not active:
        return "No active agents"

    lines: list[str] = []
    for t in active:
        agent = t.get("assigned_agent") or "unassigned"
        task_id = t.get("id", "?")

        # Extract latest context pressure from model_history
        pressure = "normal"
        utilization_pct = 0.0
        mh = t.get("model_history", [])
        if isinstance(mh, list) and mh:
            latest = mh[-1] if isinstance(mh[-1], dict) else {}
            pressure = latest.get("context_pressure", "normal") or "normal"
            utilization_pct = latest.get("context_utilization_pct", 0.0) or 0.0

        color = _CONTEXT_PRESSURE_COLORS.get(pressure, "green")
        pct = int(utilization_pct * 100)
        lines.append(
            f"{_escape(str(agent))} ({_escape(task_id)}): "
            f"[{color}]{pressure}[/{color}] {pct}%"
        )
    return "\n".join(lines)


def build_scheduler_panel(tasks: list[dict]) -> str:
    """Build Rich-markup showing scheduler insights.

    Parameters
    ----------
    tasks:
        List of HealthTaskDict dicts.  Internally converts each to a
        TaskRecord and calls Scheduler.analyze().

    Returns
    -------
    str
        Rich-markup string with critical path length, per-ready-task
        priority data, and backpressure info.  Returns
        ``'No scheduling data'`` when tasks list is empty.
    """
    if not tasks:
        return "No scheduling data"

    from forge.core.models import Complexity, TaskRecord, TaskState
    from forge.core.scheduler import Scheduler

    _RETRY_PENALTY_PER_ATTEMPT = 30

    # Convert dicts to TaskRecord instances
    task_records: list[TaskRecord] = []
    for t in tasks:
        # Map complexity string to enum
        complexity_str = t.get("complexity", "medium")
        try:
            complexity = Complexity(complexity_str)
        except ValueError:
            complexity = Complexity.MEDIUM

        # Map state string to enum
        state_str = t.get("state", "todo")
        try:
            state = TaskState(state_str)
        except ValueError:
            state = TaskState.TODO

        task_records.append(
            TaskRecord(
                id=t["id"],
                title=t.get("title", ""),
                description=t.get("description", ""),
                files=t.get("files", []),
                depends_on=t.get("depends_on", []),
                complexity=complexity,
                state=state,
                assigned_agent=t.get("assigned_agent"),
                retry_count=t.get("retry_count", 0),
            )
        )

    analysis = Scheduler.analyze(task_records)

    lines: list[str] = []
    lines.append(f"[bold]Critical path length:[/bold] {analysis.critical_path_length}")

    # Ready tasks with priority info
    if analysis.ready_task_ids:
        lines.append("")
        lines.append("[bold]Ready tasks:[/bold]")
        for task_id in analysis.ready_task_ids:
            insight = analysis.task_insights.get(task_id)
            if insight:
                lines.append(
                    f"  {_escape(task_id)}: "
                    f"priority={insight.priority_score:.1f}  "
                    f"downstream={insight.downstream_count}"
                )

    # Backpressure: tasks with retry_count > 1
    high_retry = [t for t in tasks if t.get("retry_count", 0) > 1]
    if high_retry:
        lines.append("")
        lines.append("[bold]Backpressure:[/bold]")
        for t in high_retry:
            retries = t.get("retry_count", 0)
            penalty = retries * _RETRY_PENALTY_PER_ATTEMPT
            lines.append(
                f"  [yellow]{_escape(t['id'])}[/yellow]: "
                f"{retries} retries (penalty: -{penalty})"
            )

    return "\n".join(lines)


# ── Async data fetching ─────────────────────────────────────────────


async def _fetch_health_data(
    db, pipeline_id: str
) -> dict:
    """Fetch all health data for a pipeline in one call."""

    await db.initialize()
    try:
        # Fetch pipeline
        pipelines = await db.list_pipelines()
        pipeline_row = None
        for p in pipelines:
            if p.id == pipeline_id:
                pipeline_row = p
                break

        if pipeline_row is None:
            return {"pipeline": None, "tasks": [], "agents": []}

        # Fetch tasks and agents
        task_rows = await db.list_tasks_by_pipeline(pipeline_id)
        agent_rows = await db.list_agents(prefix=pipeline_id)

        # Convert PipelineRow to dict
        pipeline_dict = {
            "id": pipeline_row.id,
            "status": pipeline_row.status,
            "description": pipeline_row.description,
            "total_cost_usd": pipeline_row.total_cost_usd,
            "total_input_tokens": pipeline_row.total_input_tokens,
            "total_output_tokens": pipeline_row.total_output_tokens,
        }

        # Convert TaskRows to dicts
        task_dicts: list[dict] = []
        for t in task_rows:
            mh = []
            if t.model_history:
                try:
                    mh = json.loads(t.model_history)
                except (json.JSONDecodeError, TypeError):
                    mh = []

            task_dicts.append({
                "id": t.id,
                "title": t.title,
                "state": t.state,
                "depends_on": t.depends_on or [],
                "assigned_agent": t.assigned_agent,
                "complexity": t.complexity,
                "cost_usd": t.cost_usd,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "retry_count": t.retry_count,
                "model_history": mh,
                "description": t.description,
                "files": t.files or [],
            })

        return {
            "pipeline": pipeline_dict,
            "tasks": task_dicts,
            "agents": agent_rows,
        }
    finally:
        await db.close()


def _find_latest_pipeline(project_path: str) -> str | None:
    """Find the latest pipeline for a project, preferring executing ones."""

    async def _fetch() -> str | None:
        db = _get_db()
        await db.initialize()
        try:
            pipelines = await db.list_pipelines(project_path=project_path)
            if not pipelines:
                return None
            # Prefer executing pipelines
            for p in pipelines:
                if p.status == "executing":
                    return p.id
            # Fall back to latest
            return pipelines[0].id
        finally:
            await db.close()

    return asyncio.run(_fetch())


# ── Layout builder ───────────────────────────────────────────────────


def _build_layout(data: dict) -> Group:
    """Compose all 4 panels into a Rich Group."""
    pipeline = data["pipeline"]
    tasks = data["tasks"]

    if pipeline is None:
        return Group(Panel("[red]Pipeline not found[/red]", title="Health"))

    # Header
    status = pipeline.get("status", "unknown")
    desc = _escape(pipeline.get("description", ""))
    header = (
        f"[bold]{desc}[/bold]\n"
        f"Status: [{_STATE_COLORS.get(status, 'white')}]{status}[/]  |  "
        f"Cost: {_fmt_cost(pipeline.get('total_cost_usd', 0.0))}"
    )

    # Panel 1: DAG
    dag_text = build_health_dag(tasks)

    # Panel 2: Cost table
    cost_table = build_cost_table(tasks, pipeline)

    # Panel 3: Context pressure
    ctx_text = build_context_panel(tasks)

    # Panel 4: Scheduler
    sched_text = build_scheduler_panel(tasks)

    return Group(
        Panel(header, title=f"Pipeline {pipeline['id'][:8]}", border_style="cyan"),
        Panel(dag_text, title="Task Dependencies", border_style="blue"),
        cost_table,
        Panel(ctx_text, title="Context Pressure", border_style="yellow"),
        Panel(sched_text, title="Scheduler Insights", border_style="green"),
    )


# ── Click command ────────────────────────────────────────────────────


@click.command("health")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option("--pipeline", "pipeline_id", default=None, help="Monitor specific pipeline")
@click.option("--interval", default=2.0, type=float, help="Poll interval in seconds")
def health(project_dir: str, pipeline_id: str | None, interval: float) -> None:
    """Live health dashboard for a running pipeline."""
    project_dir = os.path.abspath(project_dir)
    console = Console()

    # Resolve pipeline ID
    if pipeline_id is None:
        pipeline_id = _find_latest_pipeline(project_dir)
        if pipeline_id is None:
            console.print("[red]No pipelines found for this project.[/red]")
            raise SystemExit(1)
        console.print(f"Monitoring pipeline [cyan]{pipeline_id[:8]}[/cyan]")

    try:
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                data = asyncio.run(_fetch_health_data(_get_db(), pipeline_id))
                layout = _build_layout(data)
                live.update(layout)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
