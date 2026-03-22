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

from forge.core.logging_config import configure_logging

import click

try:
    _version = _pkg_version("forge-orchestrator")
except PackageNotFoundError:
    _version = "dev"


@click.group()
@click.version_option(version=_version, prog_name="Forge")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable DEBUG logging")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Forge -- Multi-agent orchestration engine."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    configure_logging(level="DEBUG" if verbose else "INFO")


# Register subcommands from separate modules.
from forge.cli.status import status  # noqa: E402

cli.add_command(status)


@cli.command()
@click.option("--project-dir", default=".", help="Project root directory")
def init(project_dir: str) -> None:
    """Initialize Forge in a project directory."""
    from forge.config.project_config import DEFAULT_FORGE_TOML

    forge_dir = os.path.join(project_dir, ".forge")
    os.makedirs(forge_dir, exist_ok=True)

    _write_if_missing(os.path.join(forge_dir, "forge.toml"), DEFAULT_FORGE_TOML)
    _write_if_missing(os.path.join(forge_dir, "build-log.md"), "# Forge Build Log\n")
    _write_if_missing(os.path.join(forge_dir, "decisions.md"), "# Architectural Decisions\n")
    _write_if_missing(os.path.join(forge_dir, "module-registry.json"), "[]")

    gitignore_path = os.path.join(forge_dir, ".gitignore")
    _ensure_gitignore_entries(gitignore_path, [
        "codebase_map.json",
        "codebase_map_meta.json",
    ])

    click.echo(f"Forge initialized in {forge_dir}")
    click.echo(f"  Config: {os.path.join(forge_dir, 'forge.toml')} — edit to customize")


@cli.command()
@click.argument("task")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality (default: auto, or $FORGE_MODEL_STRATEGY)",
)
@click.option("--spec", default=None, type=click.Path(exists=True), help="Path to spec document (markdown or text)")
@click.option("--deep-plan", is_flag=True, default=False, help="Force multi-pass deep planning")
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
@click.pass_context
def run(ctx: click.Context, task: str, project_dir: str, strategy: str | None, spec: str | None, deep_plan: bool, repo: tuple[str, ...]) -> None:
    """Run Forge to execute a task.

    TASK is the description of what to build, e.g. "Build a REST API with auth"
    """
    project_dir = os.path.abspath(project_dir)

    from forge.config.project_config import DEFAULT_FORGE_TOML, ProjectConfig, apply_project_config

    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)
        _write_if_missing(os.path.join(forge_dir, "forge.toml"), DEFAULT_FORGE_TOML)
        _write_if_missing(os.path.join(forge_dir, "build-log.md"), "# Forge Build Log\n")
        _write_if_missing(os.path.join(forge_dir, "decisions.md"), "# Architectural Decisions\n")
        _write_if_missing(os.path.join(forge_dir, "module-registry.json"), "[]")

    from forge.config.project_config import resolve_repos, validate_repos_startup

    # Resolve repos: CLI flags → workspace.toml → single-repo default
    repos = resolve_repos(repo_flags=repo, project_dir=project_dir)
    validate_repos_startup(repos)

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon

    # Load project config and apply to settings (env vars still win)
    project_config = ProjectConfig.load(project_dir)
    settings = ForgeSettings()
    apply_project_config(settings, project_config)
    if strategy:
        settings.model_strategy = strategy

    daemon = ForgeDaemon(project_dir, settings=settings)
    try:
        asyncio.run(daemon.run(task, spec_path=spec, deep_plan=deep_plan))
    except KeyboardInterrupt:
        click.echo("\nForge interrupted by user.")
    except Exception as e:
        click.echo(f"Forge failed: {e}")
        if ctx.obj.get("verbose"):
            import traceback
            traceback.print_exc()
        else:
            click.echo("Run with --verbose for full traceback.")
        raise SystemExit(1)


@cli.command()
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality",
)
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
def tui(project_dir: str, strategy: str | None, repo: tuple[str, ...]) -> None:
    """Launch the Forge terminal UI."""
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)

    from forge.config.project_config import resolve_repos, validate_repos_startup

    # Resolve repos: CLI flags → workspace.toml → single-repo default
    repos = resolve_repos(repo_flags=repo, project_dir=project_dir)
    validate_repos_startup(repos)

    # Suppress Rich console and redirect logging to file BEFORE importing
    # any daemon modules — module-level console = make_console() checks
    # _TUI_MODE at call time, so this must happen first.
    from forge.core.logging_config import configure_tui_logging
    configure_tui_logging()

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
    try:
        import uvicorn
        from forge.api.app import create_app
    except ImportError:
        click.echo(
            "Web UI requires additional dependencies.\n"
            "Install them with: pip install forge-orchestrator[web]\n\n"
            "Note: 'forge serve' also requires a git clone of the repository\n"
            "for the Next.js frontend. See: https://github.com/tarunms7/forge-orchestrator"
        )
        raise SystemExit(1)

    import signal
    import subprocess
    import threading

    # Use the central Forge database when no explicit DB URL is provided.
    if db_url is None:
        from forge.core.paths import forge_db_url
        db_url = forge_db_url()

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

    # Handle Ctrl+C / SIGTERM: kill frontend, then exit cleanly
    def _shutdown(signum, frame):
        frontend_proc.terminate()
        try:
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frontend_proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

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

from forge.cli.ping import ping  # noqa: E402

cli.add_command(ping)

from forge.cli.lessons import lessons  # noqa: E402

cli.add_command(lessons)


@cli.command()
def upgrade() -> None:
    """Upgrade Forge to the latest version from GitHub."""
    import shutil
    import subprocess
    import sys

    uv = shutil.which("uv")
    if not uv:
        click.echo("Error: uv not found. Install it first: https://docs.astral.sh/uv/", err=True)
        sys.exit(1)

    repo = "git+https://github.com/tarunms7/forge-orchestrator.git"
    click.echo(f"Upgrading forge-orchestrator from {_version}...")

    result = subprocess.run(
        [uv, "tool", "install", "--upgrade", "--force", repo],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        click.echo(f"Upgrade failed:\n{result.stderr}", err=True)
        sys.exit(1)

    # Get the new version — forge --version outputs "Forge, version X.Y.Z"
    ver_result = subprocess.run(
        [uv, "tool", "run", "forge", "--version"],
        capture_output=True, text=True,
    )
    if ver_result.returncode == 0 and ver_result.stdout.strip():
        raw = ver_result.stdout.strip()
        # Extract version from "Forge, version X.Y.Z" format
        if "version" in raw:
            new_version = raw.split("version")[-1].strip()
        else:
            new_version = raw
    else:
        new_version = "latest"
    click.echo(f"Done. {new_version}")


def _write_if_missing(path: str, content: str) -> None:
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(content)


def _ensure_gitignore_entries(gitignore_path: str, entries: list[str]) -> None:
    """Ensure specific entries exist in a .gitignore file."""
    existing = set()
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r") as f:
            existing = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    new_entries = [e for e in entries if e not in existing]
    if new_entries:
        with open(gitignore_path, "a") as f:
            if existing:  # Add newline separator if file already had content
                f.write("\n")
            for entry in new_entries:
                f.write(f"{entry}\n")
