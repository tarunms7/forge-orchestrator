"""Forge CLI lessons command. Inspect and manage the learning system."""

from __future__ import annotations

import asyncio
import os

import click
from rich.console import Console
from rich.table import Table


async def _list_lessons(db_path: str) -> list[dict]:
    from forge.learning.store import LessonStore

    store = LessonStore(db_path)
    await store.initialize()
    lessons = await store.all_lessons()
    return [
        {
            "id": l.id[:8],
            "scope": l.scope,
            "category": l.category,
            "title": l.title,
            "trigger": l.trigger[:60],
            "resolution": l.resolution[:80],
            "hits": l.hit_count,
            "last_hit": l.last_hit_at[:10] if l.last_hit_at else "",
        }
        for l in lessons
    ]


async def _clear_lessons(db_path: str) -> int:
    from forge.learning.store import LessonStore

    store = LessonStore(db_path)
    await store.initialize()
    import aiosqlite

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM lessons")
        row = await cursor.fetchone()
        count = row[0] if row else 0
        await conn.execute("DELETE FROM lessons")
        await conn.commit()
    return count


@click.group("lessons")
def lessons() -> None:
    """Inspect and manage the agent learning system."""


@lessons.command("list")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--global", "show_global", is_flag=True, default=False,
    help="Show global lessons instead of project lessons",
)
def lessons_list(project_dir: str, show_global: bool) -> None:
    """List all captured lessons."""
    project_dir = os.path.abspath(project_dir)

    if show_global:
        db_path = os.path.expanduser("~/.forge/forge_lessons.db")
        label = "Global"
    else:
        db_path = os.path.join(project_dir, ".forge", "lessons.db")
        label = "Project"

    if not os.path.exists(db_path):
        click.echo(f"No {label.lower()} lessons DB found at {db_path}")
        return

    try:
        items = asyncio.run(_list_lessons(db_path))
    except Exception as e:
        click.echo(f"Error reading lessons: {e}")
        raise SystemExit(1)

    if not items:
        click.echo(f"No {label.lower()} lessons captured yet.")
        return

    console = Console()
    table = Table(title=f"{label} Lessons ({len(items)})")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Category", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Trigger")
    table.add_column("Hits", justify="right", style="yellow")
    table.add_column("Last Hit", style="dim")

    for item in items:
        table.add_row(
            item["id"],
            item["category"],
            item["title"],
            item["trigger"],
            str(item["hits"]),
            item["last_hit"],
        )

    console.print(table)


@lessons.command("clear")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--global", "clear_global", is_flag=True, default=False,
    help="Clear global lessons instead of project lessons",
)
@click.confirmation_option(prompt="Are you sure you want to delete all lessons?")
def lessons_clear(project_dir: str, clear_global: bool) -> None:
    """Clear all captured lessons."""
    project_dir = os.path.abspath(project_dir)

    if clear_global:
        db_path = os.path.expanduser("~/.forge/forge_lessons.db")
        label = "global"
    else:
        db_path = os.path.join(project_dir, ".forge", "lessons.db")
        label = "project"

    if not os.path.exists(db_path):
        click.echo(f"No {label} lessons DB found.")
        return

    try:
        count = asyncio.run(_clear_lessons(db_path))
    except Exception as e:
        click.echo(f"Error clearing lessons: {e}")
        raise SystemExit(1)

    click.echo(f"Cleared {count} {label} lesson(s).")
