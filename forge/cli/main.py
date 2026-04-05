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
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Plan only — show task graph without executing agents",
)
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
@click.option("--provider", default=None, help="Default provider for all stages (e.g. claude, openai)")
@click.option("--planner", default=None, help="Model for planner stage (e.g. claude:opus)")
@click.option("--agent", default=None, help="Model for all agent complexity tiers")
@click.option("--reviewer", default=None, help="Model for reviewer stage")
@click.option("--contract-builder", default=None, help="Model for contract builder stage")
@click.option("--ci-fix", default=None, help="Model for CI fix stage")
@click.pass_context
def run(
    ctx: click.Context,
    task: str,
    project_dir: str,
    strategy: str | None,
    spec: str | None,
    deep_plan: bool,
    dry_run: bool,
    repo: tuple[str, ...],
    provider: str | None,
    planner: str | None,
    agent: str | None,
    reviewer: str | None,
    contract_builder: str | None,
    ci_fix: str | None,
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

    # Apply CLI model override flags
    if provider:
        # --provider sets all stages via provider tier mapping
        from forge.core.model_router import translate_to_provider

        translated = translate_to_provider(provider)
        # Extract representative models from translated table for settings overrides
        auto_table = translated.get("auto", {})
        if "planner" in auto_table:
            settings.planner_model = auto_table["planner"].get("medium", settings.planner_model)
        if "agent" in auto_table:
            settings.agent_model_low = auto_table["agent"].get("low", settings.agent_model_low)
            settings.agent_model_medium = auto_table["agent"].get("medium", settings.agent_model_medium)
            settings.agent_model_high = auto_table["agent"].get("high", settings.agent_model_high)
        if "reviewer" in auto_table:
            settings.reviewer_model = auto_table["reviewer"].get("medium", settings.reviewer_model)
        if "contract_builder" in auto_table:
            settings.contract_builder_model = auto_table["contract_builder"].get(
                "medium", settings.contract_builder_model
            )
        if "ci_fix" in auto_table:
            settings.ci_fix_model = auto_table["ci_fix"].get("medium", settings.ci_fix_model)

    if planner:
        settings.planner_model = planner
    if agent:
        settings.agent_model_low = agent
        settings.agent_model_medium = agent
        settings.agent_model_high = agent
    if reviewer:
        settings.reviewer_model = reviewer
    if contract_builder:
        settings.contract_builder_model = contract_builder
    if ci_fix:
        settings.ci_fix_model = ci_fix

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
        if dry_run:
            result = await daemon.dry_run(task, spec_path=spec, deep_plan=deep_plan)
            _print_dry_run(result)
        else:
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
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Launch TUI in dry-run (plan-only) mode",
)
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
def tui(project_dir: str, strategy: str | None, dry_run: bool, repo: tuple[str, ...]) -> None:
    """Launch the Forge terminal UI."""
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)

    from forge.config.project_config import (
        ProjectConfig,
        apply_project_config,
        resolve_repos,
        validate_repos_startup,
    )

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

    # Load project config and apply to settings (env vars still win)
    project_config = ProjectConfig.load(project_dir)
    settings = ForgeSettings()
    apply_project_config(settings, project_config)
    if strategy:
        settings.model_strategy = strategy

    app = ForgeApp(project_dir=project_dir, settings=settings)
    if dry_run:
        app._dry_run_mode = True  # noqa: SLF001 — wired by task-3's ForgeApp changes
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

from forge.cli.export import export  # noqa: E402

cli.add_command(export)

from forge.cli.gauntlet import gauntlet  # noqa: E402

cli.add_command(gauntlet)


@cli.group()
def providers() -> None:
    """Manage and inspect model providers."""
    pass


@providers.command("list")
def providers_list() -> None:
    """Show catalog entries with tier badges, validated stages, and health warnings."""
    from rich.console import Console
    from rich.table import Table

    from forge.providers.catalog import FORGE_MODEL_CATALOG

    console = Console()
    console.print("\n[bold]Provider Catalog[/bold]\n")

    # Load observed health
    import json

    health_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "providers",
        "health_state.json",
    )
    observed: dict[str, dict] = {}
    if os.path.isfile(health_path):
        try:
            with open(health_path, encoding="utf-8") as f:
                for item in json.load(f):
                    observed[item.get("spec", "")] = item
        except Exception:
            pass

    _TIER_BADGES = {
        "primary": "[green]primary[/green]",
        "supported": "[yellow]supported[/yellow]",
        "experimental": "[red]experimental[/red]",
    }

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Tier")
    table.add_column("Backend")
    table.add_column("Validated Stages")
    table.add_column("Health")

    for entry in FORGE_MODEL_CATALOG:
        spec_str = f"{entry.provider}:{entry.alias}"
        tier_badge = _TIER_BADGES.get(entry.tier, entry.tier)
        stages = ", ".join(sorted(entry.validated_stages))

        health_info = ""
        obs = observed.get(spec_str)
        if obs:
            failing = obs.get("stages_failing", [])
            if failing:
                health_info = f"[red]failing: {', '.join(failing)}[/red]"
            else:
                health_info = "[green]OK[/green]"
        elif entry.tier == "experimental":
            health_info = "[yellow]experimental[/yellow]"

        table.add_row(entry.provider, entry.alias, tier_badge, entry.backend, stages, health_info)

    console.print(table)
    console.print()


@providers.command("test")
@click.argument("model_spec")
def providers_test(model_spec: str) -> None:
    """Run conformance tests against a provider:model (e.g. claude:sonnet)."""
    click.echo(f"Conformance suite for '{model_spec}' not yet implemented.")
    click.echo("This will run provider-specific validation tests in a future release.")


cli.add_command(providers)


@cli.command()
def upgrade() -> None:
    """Upgrade Forge to the latest version from GitHub."""
    import shutil
    import subprocess
    import sys

    from forge.core.paths import forge_data_dir

    uv = shutil.which("uv")
    if not uv:
        click.echo("Error: uv not found. Install: https://docs.astral.sh/uv/", err=True)
        sys.exit(1)

    click.echo(f"Upgrading forge-orchestrator from {_version}...")

    # ── Step 1: Find a local repo clone ──
    # Priority: FORGE_DATA_DIR/repo (installer) > __file__ parent (editable) > cwd
    repo_clone = None
    for candidate in [
        os.path.join(forge_data_dir(), "repo"),
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        os.getcwd(),
    ]:
        if (
            candidate
            and os.path.isdir(os.path.join(candidate, ".git"))
            and os.path.isfile(os.path.join(candidate, "forge", "cli", "main.py"))
        ):
            repo_clone = candidate
            break

    # ── Step 2: git pull ──
    if repo_clone:
        click.echo(f"Pulling latest from {repo_clone}...")
        pull = subprocess.run(["git", "pull"], cwd=repo_clone, capture_output=True, text=True)
        if pull.returncode != 0:
            click.echo(f"git pull failed: {pull.stderr.strip()}", err=True)
        elif pull.stdout.strip() != "Already up to date.":
            click.echo(pull.stdout.strip())

    # ── Step 3: Ensure Python 3.12+ ──
    py_check = subprocess.run([uv, "python", "list"], capture_output=True, text=True)
    has_312 = any(f"3.{v}" in (py_check.stdout or "") for v in range(12, 20))
    if not has_312:
        click.echo("Installing Python 3.12...")
        subprocess.run([uv, "python", "install", "3.12"], capture_output=True, text=True)

    # ── Step 4: uv tool install (from local clone if available, else GitHub) ──
    install_source = (
        f"{repo_clone}[web]"
        if repo_clone
        else "git+https://github.com/tarunms7/forge-orchestrator.git[web]"
    )
    click.echo("Installing...")
    result = subprocess.run(
        [uv, "tool", "install", "--python", "3.12", "--upgrade", "--force", install_source],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr or ""
        if "does not satisfy" in stderr:
            click.echo("Error: Python 3.12+ required. Fix: uv python install 3.12", err=True)
        else:
            click.echo(f"Upgrade failed:\n{stderr}", err=True)
        sys.exit(1)

    # ── Step 5: Update web frontend ──
    repo_dir = repo_clone or os.path.join(forge_data_dir(), "repo")
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


def _print_dry_run(result: dict) -> None:
    """Format and print dry-run plan summary using Rich."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    graph = result["graph"]
    cost = result["cost_estimate"]
    model_assignments = result["model_assignments"]

    _COMPLEXITY_COLORS = {"low": "green", "medium": "yellow", "high": "red"}

    console.print()
    console.print(Panel("[bold cyan]DRY RUN — Plan Summary[/]", expand=False))
    console.print()

    for i, task in enumerate(graph.tasks, 1):
        complexity = task.complexity.value if hasattr(task.complexity, "value") else task.complexity
        color = _COMPLEXITY_COLORS.get(complexity, "white")
        model = model_assignments.get(task.id, "unknown")

        console.print(f"  [bold cyan]{i}. {task.title}[/]  [{color}]{complexity}[/]")
        if task.description:
            console.print(f"     [dim]{task.description[:120]}[/]")
        if task.files:
            console.print(f"     [dim]Files:[/] {', '.join(task.files[:8])}")
            if len(task.files) > 8:
                console.print(f"           [dim]… and {len(task.files) - 8} more[/]")
        if task.depends_on:
            console.print(f"     [dim]Depends on:[/] {', '.join(task.depends_on)}")
        console.print(f"     [dim]Model:[/] {model}")
        console.print()

    # Footer summary
    tasks = graph.tasks
    complexity_counts: dict[str, int] = {}
    for t in tasks:
        c = t.complexity.value if hasattr(t.complexity, "value") else t.complexity
        complexity_counts[c] = complexity_counts.get(c, 0) + 1

    parts = [f"[bold]{len(tasks)} tasks[/]"]
    for level in ("low", "medium", "high"):
        n = complexity_counts.get(level, 0)
        if n:
            color = _COMPLEXITY_COLORS.get(level, "white")
            parts.append(f"[{color}]{n} {level}[/]")
    if cost > 0:
        parts.append(f"[green]~${cost:.2f}[/]")

    console.print("  " + " · ".join(parts))
    console.print()
    console.print("  [dim]Run without --dry-run to execute[/]")
    console.print()


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
