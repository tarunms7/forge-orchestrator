"""Forge CLI status command. Shows pipeline status from the central DB."""

from __future__ import annotations

import asyncio
import os

import click
from rich.console import Console
from rich.table import Table


async def _fetch_pipelines(
    db_url: str,
    project_path: str | None = None,
) -> list[dict]:
    """Open the DB, fetch pipelines with task counts, and return dicts.

    When *project_path* is given, only pipelines for that project are returned.
    When ``None``, all pipelines across every project are returned.
    """
    from forge.storage.db import Database

    db = Database(db_url)
    await db.initialize()
    try:
        pipelines = await db.list_pipelines(project_path=project_path)
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
                    "project_name": p.project_name or "",
                    "project_path": p.project_path or "",
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
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Show pipelines from all projects",
)
def status(project_dir: str, show_all: bool) -> None:
    """Show the status of Forge pipelines."""
    from forge.core.paths import forge_db_url

    db_url = forge_db_url()
    project_dir = os.path.abspath(project_dir)

    # When --all, show all projects; otherwise filter to current project
    filter_path = None if show_all else project_dir

    try:
        pipelines = asyncio.run(_fetch_pipelines(db_url, project_path=filter_path))
    except Exception as e:
        click.echo(f"Error reading database: {e}")
        raise SystemExit(1)

    if not pipelines:
        click.echo("No pipelines found.")
        return

    console = Console()
    table = Table(title="Forge Pipelines")
    table.add_column("ID", style="cyan", no_wrap=True)
    if show_all:
        table.add_column("Project")
    table.add_column("Description")
    table.add_column("Status")
    table.add_column("Tasks", justify="right")
    table.add_column("Created At")

    for p in pipelines:
        color = _STATUS_COLORS.get(p["status"], "white")
        status_text = p["status"].replace("[", "\\[")
        description_text = p["description"].replace("[", "\\[")
        row = [p["id"]]
        if show_all:
            row.append(p["project_name"])
        row.extend(
            [
                description_text,
                f"[{color}]{status_text}[/{color}]",
                str(p["task_count"]),
                p["created_at"],
            ]
        )
        table.add_row(*row)

    console.print(table)
