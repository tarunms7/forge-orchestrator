"""Forge CLI export command. Export pipeline data as JSON, Markdown, or CSV."""

from __future__ import annotations

import asyncio

import click


def _get_db():
    from forge.core.paths import forge_db_url
    from forge.storage.db import Database

    return Database(forge_db_url())


_FORMATTERS = {
    "json": "format_json",
    "md": "format_markdown",
    "csv": "format_csv",
}


@click.command("export")
@click.argument("pipeline_id")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "md", "csv"], case_sensitive=False),
    default="json",
    help="Output format (default: json)",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    type=click.Path(),
    help="Write output to file instead of stdout",
)
def export(pipeline_id: str, fmt: str, output_path: str | None) -> None:
    """Export pipeline data for reporting or analysis."""

    async def _fetch():
        db = _get_db()
        await db.initialize()
        try:
            return await db.get_pipeline_export_data(pipeline_id)
        finally:
            await db.close()

    try:
        data = asyncio.run(_fetch())
    except Exception as e:
        click.echo(f"Error reading pipeline data: {e}", err=True)
        raise SystemExit(1)

    if data is None:
        click.echo(f"Pipeline {pipeline_id} not found.", err=True)
        raise SystemExit(1)

    from forge.cli.export_formatters import format_csv, format_json, format_markdown

    formatter = {"json": format_json, "md": format_markdown, "csv": format_csv}[fmt]
    result = formatter(data)

    if output_path:
        with open(output_path, "w") as f:
            f.write(result)
        click.echo(f"Exported to {output_path}")
    else:
        click.echo(result)
