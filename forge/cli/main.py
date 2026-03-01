"""Forge CLI. Entry point for all user interaction."""

import asyncio
import os

# Remove CLAUDECODE immediately — before any SDK imports.
# Claude Code sets this env var in its terminal sessions. The Claude CLI
# refuses to launch if it's present ("nested session" guard). We are NOT
# a nested session — we're an orchestrator spawning independent agents.
os.environ.pop("CLAUDECODE", None)

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
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality (default: auto, or $FORGE_MODEL_STRATEGY)",
)
def run(task: str, project_dir: str, strategy: str | None) -> None:
    """Run Forge to execute a task.

    TASK is the description of what to build, e.g. "Build a REST API with auth"
    """
    project_dir = os.path.abspath(project_dir)

    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        click.echo("Forge not initialized. Run 'forge init' first.")
        raise SystemExit(1)

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon

    settings = ForgeSettings()
    if strategy:
        settings.model_strategy = strategy

    daemon = ForgeDaemon(project_dir, settings=settings)
    try:
        asyncio.run(daemon.run(task))
    except KeyboardInterrupt:
        click.echo("\nForge interrupted by user.")
    except Exception as e:
        click.echo(f"Forge failed: {e}")
        raise SystemExit(1)


@cli.command()
@click.option("--port", default=8000, help="API server port")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--db-url", default="sqlite+aiosqlite:///forge.db", help="Database URL")
@click.option(
    "--jwt-secret",
    default=None,
    envvar="FORGE_JWT_SECRET",
    help="JWT signing secret (default: $FORGE_JWT_SECRET or random)",
)
@click.option(
    "--build-frontend/--no-build-frontend",
    default=True,
    help="Build Next.js before serving",
)
def serve(port: int, host: str, db_url: str, jwt_secret: str | None, build_frontend: bool):
    """Start the Forge web server."""
    if build_frontend:
        _build_frontend()
    import uvicorn
    from forge.api.app import create_app
    app = create_app(db_url=db_url, jwt_secret=jwt_secret)
    click.echo(f"Forge UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


def _build_frontend():
    """Build the Next.js frontend if web/ directory exists."""
    import subprocess
    web_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web")
    web_dir = os.path.normpath(web_dir)
    if not os.path.isdir(web_dir):
        return
    out_dir = os.path.join(web_dir, "out")
    if os.path.isdir(out_dir):
        return  # already built
    click.echo("Building frontend...")
    subprocess.run(["npm", "run", "build"], cwd=web_dir, check=True)


# ── Register sub-commands from other modules ─────────────────────────
from forge.cli.logs import logs  # noqa: E402

cli.add_command(logs)


def _write_if_missing(path: str, content: str) -> None:
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(content)
