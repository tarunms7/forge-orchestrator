"""Forge CLI status command. Shows pipeline status from the local DB."""

import asyncio
import os

import click
from rich.console import Console
from rich.table import Table


async def _fetch_pipelines(db_url: str) -> list[dict]:
    """Open the DB, fetch all pipelines with task counts, and return dicts."""
    from forge.storage.db import Database

    db = Database(db_url)
    await db.initialize()
    try:
        pipelines = await db.list_pipelines()
        results = []
        for p in pipelines:
            tasks = await db.list_tasks_by_pipeline(p.id)
            results.append(
                {
                    "id": p.id,
                    "description": p.description,
                    "status": p.status,
                    "task_count": len(tasks),
                    "created_at": p.created_at or "",
                }
            )
        return results
    finally:
        await db.close()


_STATUS_COLORS = {
    "planning": "yellow",
    "planned": "blue",
    "executing": "cyan",
    "complete": "green",
    "error": "red",
}


@click.command("status")
@click.option("--project-dir", default=".", help="Project root directory")
def status(project_dir: str) -> None:
    """Show the status of Forge pipelines."""
    project_dir = os.path.abspath(project_dir)
    db_path = os.path.join(project_dir, ".forge", "forge.db")

    if not os.path.isfile(db_path):
        click.echo(f"Error: Forge database not found at {db_path}")
        raise SystemExit(1)

    db_url = f"sqlite+aiosqlite:///{db_path}"

    try:
        pipelines = asyncio.run(_fetch_pipelines(db_url))
    except Exception as e:
        click.echo(f"Error reading database: {e}")
        raise SystemExit(1)

    if not pipelines:
        click.echo("No pipelines found.")
        return

    console = Console()
    table = Table(title="Forge Pipelines")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Status")
    table.add_column("Tasks", justify="right")
    table.add_column("Created At")

    for p in pipelines:
        color = _STATUS_COLORS.get(p["status"], "white")
        table.add_row(
            p["id"],
            p["description"],
            f"[{color}]{p['status']}[/{color}]",
            str(p["task_count"]),
            p["created_at"],
        )

    console.print(table)
