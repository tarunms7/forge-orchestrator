"""Forge CLI. Entry point for all user interaction."""

import asyncio
import os

import click


@click.group()
@click.version_option(version="0.1.0", prog_name="Forge")
def cli() -> None:
    """Forge -- Multi-agent orchestration engine."""


@cli.command()
@click.option("--project-dir", default=".", help="Project root directory")
def init(project_dir: str) -> None:
    """Initialize Forge in a project directory."""
    forge_dir = os.path.join(project_dir, ".forge")
    os.makedirs(forge_dir, exist_ok=True)

    _write_if_missing(os.path.join(forge_dir, "build-log.md"), "# Forge Build Log\n")
    _write_if_missing(os.path.join(forge_dir, "decisions.md"), "# Architectural Decisions\n")
    _write_if_missing(os.path.join(forge_dir, "module-registry.json"), "[]")

    click.echo(f"Forge initialized in {forge_dir}")


@cli.command()
@click.argument("task")
@click.option("--project-dir", default=".", help="Project root directory")
def run(task: str, project_dir: str) -> None:
    """Run Forge to execute a task.

    TASK is the description of what to build, e.g. "Build a REST API with auth"
    """
    project_dir = os.path.abspath(project_dir)

    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        click.echo("Forge not initialized. Run 'forge init' first.")
        raise SystemExit(1)

    from forge.core.daemon import ForgeDaemon

    daemon = ForgeDaemon(project_dir)
    try:
        asyncio.run(daemon.run(task))
    except KeyboardInterrupt:
        click.echo("\nForge interrupted by user.")
    except Exception as e:
        click.echo(f"Forge failed: {e}", err=True)
        raise SystemExit(1)


def _write_if_missing(path: str, content: str) -> None:
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(content)
