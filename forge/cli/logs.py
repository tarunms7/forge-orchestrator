"""Forge logs command -- display a colored timeline of pipeline events."""

from __future__ import annotations

import asyncio
import json
import os

import click
from rich.console import Console
from rich.text import Text

# ── Color mapping for event types ──────────────────────────────────────
_EVENT_COLORS: dict[str, str] = {
    "success": "green",
    "complete": "green",
    "done": "green",
    "error": "red",
    "fail": "red",
    "warning": "yellow",
    "warn": "yellow",
    "info": "cyan",
    "start": "cyan",
    "pending": "dim",
}


def _color_for_event(event_type: str) -> str:
    """Return a Rich style string for the given event_type."""
    lower = event_type.lower()
    for keyword, color in _EVENT_COLORS.items():
        if keyword in lower:
            return color
    return "white"


def _summarize_payload(payload: dict | None, max_len: int = 120) -> str:
    """Return a short human-readable summary of the event payload."""
    if not payload:
        return ""
    # Try common keys first
    for key in ("message", "error", "detail", "state", "line", "title"):
        if key in payload:
            val = str(payload[key])
            return val[:max_len] + "..." if len(val) > max_len else val
    # Fallback: compact JSON
    text = json.dumps(payload, default=str, separators=(",", ":"))
    return text[:max_len] + "..." if len(text) > max_len else text


async def _fetch_events(db_path: str, pipeline_id: str) -> list[dict]:
    """Open the forge DB and query pipeline_events for the given pipeline_id."""
    from forge.storage.db import Database

    db_url = f"sqlite+aiosqlite:///{db_path}"
    db = Database(db_url)
    await db.initialize()

    try:
        events = await db.list_events(pipeline_id)
        return [
            {
                "created_at": ev.created_at,
                "event_type": ev.event_type,
                "task_id": ev.task_id,
                "payload": ev.payload if isinstance(ev.payload, dict) else {},
            }
            for ev in events
        ]
    finally:
        await db.close()


@click.command("logs")
@click.argument("pipeline_id")
@click.option(
    "--db",
    "db_path",
    default=None,
    help="Path to forge.db (default: central data dir)",
)
def logs(pipeline_id: str, db_path: str | None) -> None:
    """Display a colored timeline of events for a pipeline.

    PIPELINE_ID is the pipeline to show logs for.
    """
    if db_path is None:
        from forge.core.paths import forge_db_path

        db_path = forge_db_path()

    if not os.path.isfile(db_path):
        click.echo(f"Database not found at {db_path}", err=True)
        raise SystemExit(1)

    events = asyncio.run(_fetch_events(db_path, pipeline_id))

    if not events:
        click.echo(f"No events found for pipeline {pipeline_id}")
        return

    console = Console()
    for ev in events:
        line = Text()

        # Timestamp (dim)
        ts = ev["created_at"] or "unknown"
        line.append(f"{ts}  ", style="dim")

        # Event type (color-coded)
        etype = ev["event_type"]
        color = _color_for_event(etype)
        line.append(f"[{etype}]", style=color)

        # Task ID if present
        if ev["task_id"]:
            line.append(f"  task={ev['task_id']}", style="blue")

        # Payload summary
        summary = _summarize_payload(ev["payload"])
        if summary:
            line.append(f"  {summary}", style="white")

        console.print(line)
