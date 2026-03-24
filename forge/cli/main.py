"""Forge CLI. Entry point for all user interaction."""

from __future__ import annotations

import asyncio
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Remove CLAUDECODE immediately — before any SDK imports.
# Claude Code sets this env var in its terminal sessions. The Claude CLI
# refuses to launch if it's present ("nested session" guard). We are NOT
# a nested session — we're an orchestrator spawning independent agents.
os.environ.pop("CLAUDECODE", None)

import click

from forge.core.logging_config import configure_logging

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
    _ensure_gitignore_entries(
        gitignore_path,
        [
            "codebase_map.json",
            "codebase_map_meta.json",
        ],
    )

    # Auto-detect project commands and show helpful summary
    detected = []
    if os.path.isfile(os.path.join(project_dir, "pyproject.toml")):
        detected.append("Python project (pyproject.toml)")
    if os.path.isfile(os.path.join(project_dir, "package.json")):
        detected.append("Node.js project (package.json)")
    if os.path.isfile(os.path.join(project_dir, "Cargo.toml")):
        detected.append("Rust project (Cargo.toml)")
    if os.path.isfile(os.path.join(project_dir, "go.mod")):
        detected.append("Go project (go.mod)")
    if os.path.isfile(os.path.join(project_dir, "Makefile")):
        detected.append("Makefile detected")
    if os.path.isfile(os.path.join(project_dir, "workspace.toml")):
        detected.append("Multi-repo workspace (workspace.toml)")

    click.echo(f"Forge initialized in {forge_dir}")
    if detected:
        click.echo(f"  Detected: {', '.join(detected)}")
    click.echo(f"  Config: {os.path.join(forge_dir, 'forge.toml')} — edit to customize")
    click.echo("  Run `forge tui` to start, or `forge doctor` to verify setup")


@cli.command()
@click.argument("task")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality (default: auto, or $FORGE_MODEL_STRATEGY)",
)
@click.option(
    "--spec",
    default=None,
    type=click.Path(exists=True),
    help="Path to spec document (markdown or text)",
)
@click.option("--deep-plan", is_flag=True, default=False, help="Force multi-pass deep planning")
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
@click.pass_context
def run(
    ctx: click.Context,
    task: str,
    project_dir: str,
    strategy: str | None,
    spec: str | None,
    deep_plan: bool,
    repo: tuple[str, ...],
) -> None:
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

    # Pre-flight checks before starting the pipeline
    from forge.core.preflight import run_preflight

    async def _run_with_preflight():
        repos_dict = {rc.id: rc for rc in repos} if repos else None
        preflight = await run_preflight(project_dir, repos=repos_dict)
        if not preflight.passed:
            for e in preflight.errors:
                click.echo(f"  ✗ {e.name}: {e.message}", err=True)
                if e.fix_hint:
                    click.echo(f"    Fix: {e.fix_hint}", err=True)
            raise SystemExit(1)
        for w in preflight.warnings:
            click.echo(f"  ⚠ {w.name}: {w.message}", err=True)

        daemon = ForgeDaemon(project_dir, settings=settings)
        await daemon.run(task, spec_path=spec, deep_plan=deep_plan)

    try:
        asyncio.run(_run_with_preflight())
    except KeyboardInterrupt:
        click.echo("\nForge interrupted by user.")
    except SystemExit:
        raise
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

    from forge.core.paths import forge_web_dir

    web_dir = forge_web_dir()
    if not os.path.isdir(web_dir):
        click.echo(
            f"Web frontend not found at {web_dir}\n"
            "Run the installer to set it up:\n"
            "  curl -fsSL https://raw.githubusercontent.com/tarunms7/forge-orchestrator/main/install.sh | sh\n"
            "\nOr set FORGE_WEB_DIR to point to the web/ directory."
        )
        raise SystemExit(1)

    # Auto-install dependencies if needed
    if not os.path.isdir(os.path.join(web_dir, "node_modules")):
        click.echo("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=web_dir, check=True)

    # Start uvicorn in a background thread
    server_thread = threading.Thread(
        target=uvicorn.run, args=(app,), kwargs={"host": host, "port": port}, daemon=True
    )
    server_thread.start()

    # Spawn Next.js dev server with API URL pointing to the FastAPI backend
    frontend_env = {**os.environ, "NEXT_PUBLIC_API_URL": f"http://{host}:{port}/api"}
    frontend_proc = subprocess.Popen(["npm", "run", "dev"], cwd=web_dir, env=frontend_env)

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

from forge.cli.stats import stats  # noqa: E402

cli.add_command(stats)


@cli.command()
def upgrade() -> None:
    """Upgrade Forge to the latest version from GitHub (global install)."""
    import shutil
    import subprocess
    import sys

    from forge.core.paths import forge_data_dir

    uv = shutil.which("uv")
    if not uv:
        click.echo("Error: uv not found. Install it first: https://docs.astral.sh/uv/", err=True)
        sys.exit(1)

    repo_pip = "git+https://github.com/tarunms7/forge-orchestrator.git"
    click.echo(f"Upgrading forge-orchestrator from {_version}...")

    # Step 1: Detect if this is a dev install (editable pip install from a git clone)
    forge_location = shutil.which("forge") or ""
    is_dev_install = (
        "site-packages" not in forge_location
        and os.path.isfile(
            os.path.join(os.path.dirname(os.path.dirname(forge_location or "/")), "pyproject.toml")
        )
    ) or os.path.isfile(os.path.join(os.getcwd(), "forge", "cli", "main.py"))

    if is_dev_install:
        # Dev install: just git pull in the repo
        # __file__ is forge/cli/main.py — go up 3 levels to get repo root
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if os.path.isdir(os.path.join(repo_root, ".git")):
            click.echo("Dev install detected — pulling latest from git...")
            pull = subprocess.run(
                ["git", "pull"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if pull.returncode == 0:
                click.echo(pull.stdout.strip())
                # Auto-reinstall to pick up new dependencies
                click.echo("Reinstalling dependencies...")
                pip_install = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", ".[web]", "-q"],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                )
                if pip_install.returncode == 0:
                    # Check new version
                    new_ver = subprocess.run(
                        ["forge", "--version"],
                        capture_output=True,
                        text=True,
                    )
                    new_version = new_ver.stdout.strip() if new_ver.returncode == 0 else "unknown"
                    click.echo(f"Forge upgraded to {new_version}")
                else:
                    click.echo(f"pip install failed: {pip_install.stderr.strip()[:200]}", err=True)
                    click.echo("Run manually: pip install -e '.[web]'")
            else:
                click.echo(f"git pull failed: {pull.stderr.strip()}", err=True)
            return
        click.echo("Dev install detected but no git repo found. Run `git pull` manually.")
        return

    # Step 2: Ensure Python 3.12+ is available for uv tool install
    py_check = subprocess.run([uv, "python", "list"], capture_output=True, text=True)
    has_312 = any(
        f"3.{v}" in (py_check.stdout or "")
        for v in range(12, 20)  # 3.12 through 3.19
    )
    if not has_312:
        click.echo("Installing Python 3.12 (required by Forge)...")
        py_install = subprocess.run(
            [uv, "python", "install", "3.12"],
            capture_output=True,
            text=True,
        )
        if py_install.returncode != 0:
            click.echo(
                "Error: Could not install Python 3.12.\n"
                "Install it manually: brew install python@3.12 (macOS) or uv python install 3.12",
                err=True,
            )
            sys.exit(1)

    # Step 3: Upgrade Python package with web extras
    result = subprocess.run(
        [uv, "tool", "install", "--python", "3.12", "--upgrade", "--force", f"{repo_pip}[web]"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Provide helpful error message instead of raw pip output
        stderr = result.stderr or ""
        if "Python" in stderr and "does not satisfy" in stderr:
            click.echo(
                "Error: Python 3.12+ required but not available for uv.\n"
                "Fix: uv python install 3.12\n"
                "Or:  brew install python@3.12",
                err=True,
            )
        else:
            click.echo(f"Upgrade failed:\n{stderr}", err=True)
        sys.exit(1)

    # Step 2: Update the cloned repo (for web frontend)
    repo_dir = os.path.join(forge_data_dir(), "repo")
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        click.echo("Updating web frontend...")
        subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            cwd=repo_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "reset", "--hard", "origin/main", "--quiet"],
            cwd=repo_dir,
            capture_output=True,
        )
    else:
        click.echo("Cloning web frontend...")
        os.makedirs(os.path.dirname(repo_dir), exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://github.com/tarunms7/forge-orchestrator.git",
                repo_dir,
            ],
            capture_output=True,
        )

    # Step 3: Install frontend dependencies
    web_dir = os.path.join(repo_dir, "web")
    if os.path.isdir(web_dir) and shutil.which("npm"):
        click.echo("Installing frontend dependencies...")
        npm_result = subprocess.run(
            ["npm", "install", "--prefix", web_dir, "--silent"],
            capture_output=True,
            text=True,
        )
        if npm_result.returncode == 0:
            click.echo("Frontend dependencies installed.")
        else:
            click.echo("Warning: npm install failed — TUI still works, web UI may not.")

    # Get the new version — forge --version outputs "Forge, version X.Y.Z"
    ver_result = subprocess.run(
        [uv, "tool", "run", "forge", "--version"],
        capture_output=True,
        text=True,
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
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _ensure_gitignore_entries(gitignore_path: str, entries: list[str]) -> None:
    """Ensure specific entries exist in a .gitignore file."""
    existing = set()
    if os.path.exists(gitignore_path):
        with open(gitignore_path, encoding="utf-8") as f:
            existing = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    new_entries = [e for e in entries if e not in existing]
    if new_entries:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            if existing:  # Add newline separator if file already had content
                f.write("\n")
            for entry in new_entries:
                f.write(f"{entry}\n")
