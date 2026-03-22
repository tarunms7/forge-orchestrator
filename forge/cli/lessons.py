"""Forge CLI lessons command. Inspect and manage the learning system."""

from __future__ import annotations

import asyncio
import os

import click
from rich.console import Console
from rich.table import Table


def _get_db():
    from forge.core.paths import forge_db_url
    from forge.storage.db import Database
    return Database(forge_db_url())


@click.group("lessons")
def lessons() -> None:
    """Inspect and manage the agent learning system."""


@lessons.command("list")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--global", "show_global", is_flag=True, default=False,
    help="Show only global lessons",
)
def lessons_list(project_dir: str, show_global: bool) -> None:
    """List all captured lessons."""
    project_dir = os.path.abspath(project_dir)

    async def _run():
        db = _get_db()
        await db.initialize()
        try:
            if show_global:
                rows = await db.get_relevant_lessons(max_count=100)
            else:
                rows = await db.get_relevant_lessons(project_dir=project_dir, max_count=100)
            return rows
        finally:
            await db.close()

    try:
        rows = asyncio.run(_run())
    except Exception as e:
        click.echo(f"Error reading lessons: {e}")
        raise SystemExit(1)

    if not rows:
        click.echo("No lessons captured yet.")
        return

    console = Console()
    label = "Global" if show_global else "All"
    table = Table(title=f"{label} Lessons ({len(rows)})")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Scope", style="blue")
    table.add_column("Category", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Trigger")
    table.add_column("Hits", justify="right", style="yellow")

    for row in rows:
        table.add_row(
            row.id[:8],
            row.scope,
            row.category,
            row.title,
            row.trigger[:60],
            str(row.hit_count),
        )

    console.print(table)


@lessons.command("add")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--global", "is_global", is_flag=True, default=False,
    help="Add as a global lesson",
)
@click.option("--category", type=click.Choice(["command_failure", "review_failure", "code_pattern"]), default="command_failure")
@click.argument("title")
@click.argument("resolution")
@click.option("--trigger", default=None, help="Trigger pattern (defaults to title)")
def lessons_add(project_dir: str, is_global: bool, category: str, title: str, resolution: str, trigger: str | None) -> None:
    """Add a lesson manually. TITLE and RESOLUTION are required."""
    project_dir = os.path.abspath(project_dir)
    scope = "global" if is_global else "project"

    effective_trigger = trigger or title
    effective_project_dir = None if is_global else project_dir

    async def _check_dup():
        db = _get_db()
        await db.initialize()
        try:
            return await db.find_matching_lesson(
                effective_trigger, project_dir=effective_project_dir,
            )
        finally:
            await db.close()

    # Check for similar existing lesson before inserting
    try:
        existing = asyncio.run(_check_dup())
    except Exception:
        existing = None

    if existing:
        if not click.confirm(
            f"Similar lesson exists: {existing.title}. Add anyway?",
        ):
            click.echo("Aborted.")
            return

    async def _run():
        db = _get_db()
        await db.initialize()
        try:
            return await db.add_lesson(
                scope=scope, category=category,
                title=title, content=title,
                trigger=effective_trigger, resolution=resolution,
                project_dir=effective_project_dir,
            )
        finally:
            await db.close()

    try:
        lid = asyncio.run(_run())
    except Exception as e:
        click.echo(f"Error adding lesson: {e}")
        raise SystemExit(1)

    click.echo(f"Added {scope} lesson: {title} (id: {lid[:8]})")


@lessons.command("clear")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--all", "clear_all", is_flag=True, default=False,
    help="Clear ALL lessons (global + project)",
)
@click.confirmation_option(prompt="Are you sure you want to delete lessons?")
def lessons_clear(project_dir: str, clear_all: bool) -> None:
    """Clear lessons for the current project (or all with --all)."""
    project_dir = os.path.abspath(project_dir)

    async def _run():
        db = _get_db()
        await db.initialize()
        try:
            if clear_all:
                return await db.clear_lessons()
            return await db.clear_lessons(project_dir=project_dir)
        finally:
            await db.close()

    try:
        count = asyncio.run(_run())
    except Exception as e:
        click.echo(f"Error clearing lessons: {e}")
        raise SystemExit(1)

    click.echo(f"Cleared {count} lesson(s).")
