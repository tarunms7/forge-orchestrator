"""Forge CLI. Entry point for all user interaction."""

from __future__ import annotations

import asyncio
import os
from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Remove CLAUDECODE immediately — before any SDK imports.
# Claude Code sets this env var in its terminal sessions. The Claude CLI
# refuses to launch if it's present ("nested session" guard). We are NOT
# a nested session — we're an orchestrator spawning independent agents.
os.environ.pop("CLAUDECODE", None)

import click

try:
    _version = _pkg_version("forge-orchestrator")
except PackageNotFoundError:
    _version = "dev"


@click.group()
@click.version_option(version=_version, prog_name="Forge")
def cli() -> None:
    """Forge -- Multi-agent orchestration engine."""


# Register subcommands from separate modules.
from forge.cli.status import status  # noqa: E402

cli.add_command(status)


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
        os.makedirs(forge_dir, exist_ok=True)
        _write_if_missing(os.path.join(forge_dir, "build-log.md"), "# Forge Build Log\n")
        _write_if_missing(os.path.join(forge_dir, "decisions.md"), "# Architectural Decisions\n")
        _write_if_missing(os.path.join(forge_dir, "module-registry.json"), "[]")

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
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality",
)
def tui(project_dir: str, strategy: str | None) -> None:
    """Launch the Forge terminal UI."""
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)

    from forge.config.settings import ForgeSettings
    from forge.tui.app import ForgeApp

    settings = ForgeSettings()
    if strategy:
        settings.model_strategy = strategy

    app = ForgeApp(project_dir=project_dir, settings=settings)
    app.run()


@cli.command()
@click.option("--port", default=8000, help="API server port")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--db-url", default=None, help="Database URL (default: forge.db in repo root)")
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
def serve(port: int, host: str, db_url: str | None, jwt_secret: str | None, build_frontend: bool):
    """Start the Forge web server."""
    import signal
    import subprocess
    import threading

    # Resolve DB path relative to repo root (not CWD) so `forge serve`
    # always uses the same database regardless of where it's invoked from.
    if db_url is None:
        repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_path = os.path.join(repo_root, "forge.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"

    import uvicorn
    from forge.api.app import create_app

    app = create_app(db_url=db_url, jwt_secret=jwt_secret)

    if not build_frontend:
        click.echo(f"Forge UI: http://{host}:{port}")
        uvicorn.run(app, host=host, port=port)
        return

    web_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "web"))

    # Auto-install dependencies if needed
    if not os.path.isdir(os.path.join(web_dir, "node_modules")):
        click.echo("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=web_dir, check=True)

    # Start uvicorn in a background thread
    server_thread = threading.Thread(
        target=uvicorn.run, args=(app,), kwargs={"host": host, "port": port}, daemon=True
    )
    server_thread.start()

    # Spawn Next.js dev server
    frontend_proc = subprocess.Popen(["npm", "run", "dev"], cwd=web_dir)

    click.echo(f"Forge UI at http://localhost:3000 (API at http://localhost:{port})")

    # Handle Ctrl+C: kill frontend, then exit cleanly
    def _shutdown(signum, frame):
        frontend_proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)

    frontend_proc.wait()


# ── Register sub-commands from other modules ─────────────────────────
from forge.cli.logs import logs  # noqa: E402

cli.add_command(logs)

from forge.cli.clean import clean  # noqa: E402

cli.add_command(clean)

from forge.cli.doctor import doctor  # noqa: E402

cli.add_command(doctor)

from forge.cli.fix import fix  # noqa: E402

cli.add_command(fix)


def _write_if_missing(path: str, content: str) -> None:
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(content)
