"""Forge CLI fix command — resolve a GitHub issue via the Forge pipeline."""

from __future__ import annotations

import subprocess

import click
from rich.console import Console


def _parse_ref(ref: str):
    """Lazy wrapper around parse_issue_ref."""
    from forge.issue import parse_issue_ref

    return parse_issue_ref(ref)


def _check_auth() -> bool:
    """Lazy wrapper around check_gh_auth."""
    from forge.issue.github import check_gh_auth

    return check_gh_auth()


def _fetch(number: int, repo: str | None = None):
    """Lazy wrapper around fetch_issue."""
    from forge.issue.github import fetch_issue

    return fetch_issue(number, repo=repo)


def _compose(issue):
    """Lazy wrapper around compose_prompt."""
    from forge.issue.prompt import compose_prompt

    return compose_prompt(issue)


def _slugify(title: str) -> str:
    """Lazy wrapper around slugify_title."""
    from forge.issue.github import slugify_title

    return slugify_title(title)


@click.command("fix")
@click.argument("issue_ref")
@click.option("--branch", default=None, help="Custom branch name override")
@click.option("--dry-run", is_flag=True, help="Plan only — print composed prompt and plan, then exit")
@click.option("--max-budget", type=float, default=None, help="Override FORGE_BUDGET_LIMIT_USD")
@click.option("--no-pr", is_flag=True, help="Skip PR creation after pipeline completes")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality (default: auto, or $FORGE_MODEL_STRATEGY)",
)
def fix(
    issue_ref: str,
    branch: str | None,
    dry_run: bool,
    max_budget: float | None,
    no_pr: bool,
    yes: bool,
    project_dir: str,
    strategy: str | None,
) -> None:
    """Fix a GitHub issue via the Forge pipeline.

    ISSUE_REF is an issue number (42) or full GitHub URL
    (https://github.com/org/repo/issues/42).
    """
    import asyncio
    import os

    console = Console()

    # ── 1. Parse issue reference ──────────────────────────────────────
    try:
        issue_number, repo_nwo = _parse_ref(issue_ref)
    except ValueError as exc:
        click.echo(f"Error: {exc}")
        raise SystemExit(1)

    # ── 2. Validate gh auth ───────────────────────────────────────────
    try:
        if not _check_auth():
            click.echo("Error: gh is not authenticated. Run `gh auth login`.")
            raise SystemExit(1)
    except FileNotFoundError:
        click.echo("Error: gh CLI is not installed. Install it from https://cli.github.com")
        raise SystemExit(1)

    # ── 3. Validate we're in a git repo ───────────────────────────────
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            click.echo("Error: not inside a git repository.")
            raise SystemExit(1)
    except FileNotFoundError:
        click.echo("Error: git is not installed.")
        raise SystemExit(1)

    # ── 4. Fetch issue ────────────────────────────────────────────────
    with console.status(f"[bold]Fetching issue #{issue_number}...[/bold]"):
        try:
            issue = _fetch(issue_number, repo=repo_nwo)
        except FileNotFoundError:
            click.echo("Error: gh CLI is not installed.")
            raise SystemExit(1)
        except RuntimeError as exc:
            click.echo(f"Error: {exc}")
            raise SystemExit(1)
        except ValueError as exc:
            click.echo(f"Error: {exc}")
            raise SystemExit(1)

    # ── 5. Compose prompt ─────────────────────────────────────────────
    prompt = _compose(issue)

    console.print("\n[bold]Composed prompt:[/bold]\n")
    console.print(prompt)
    console.print()

    # ── 6. Confirmation ──────────────────────────────────────────────
    if not yes and not dry_run:
        if not click.confirm("Proceed with this task?"):
            click.echo("Aborted.")
            raise SystemExit(0)

    # ── 7. Setup ─────────────────────────────────────────────────────
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon

    settings = ForgeSettings()
    if strategy:
        settings.model_strategy = strategy
    if max_budget is not None:
        settings.budget_limit_usd = max_budget

    # ── 8. Determine branch name ─────────────────────────────────────
    branch_name = branch or f"fix/{issue_number}-{_slugify(issue.title)}"

    # ── 9. Dry-run: plan only ────────────────────────────────────────
    if dry_run:
        daemon = ForgeDaemon(project_dir, settings=settings)
        try:
            plan_result = asyncio.run(_plan_only(daemon, prompt))
            console.print("\n[bold]Plan:[/bold]\n")
            console.print(str(plan_result))
        except Exception as exc:
            click.echo(f"Planning failed: {exc}")
            raise SystemExit(1)
        return

    # ── 10. Create branch & run pipeline ─────────────────────────────
    branch_result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        capture_output=True, text=True, timeout=30,
    )
    if branch_result.returncode != 0:
        click.echo(f"Error: failed to create branch '{branch_name}': {branch_result.stderr.strip()}")
        raise SystemExit(1)

    daemon = ForgeDaemon(project_dir, settings=settings)
    try:
        asyncio.run(daemon.run(prompt))
    except KeyboardInterrupt:
        click.echo("\nForge interrupted by user.")
        click.echo(f"\nNote: you are now on orphan branch '{branch_name}'.")
        click.echo("To return to your original branch, run:")
        click.echo("  git checkout <original-branch>")
        raise SystemExit(1)
    except Exception as exc:
        click.echo(f"Forge failed: {exc}")
        click.echo(f"\nNote: you are now on orphan branch '{branch_name}'.")
        click.echo("To return to your original branch, run:")
        click.echo("  git checkout <original-branch>")
        raise SystemExit(1)

    # ── 11. Create PR ────────────────────────────────────────────────
    if not no_pr:
        pr_title = f"Fix #{issue_number}: {issue.title}"
        pr_body = f"Fixes #{issue_number}\n\nGenerated by Forge."
        pr_cmd = [
            "gh", "pr", "create",
            "--title", pr_title,
            "--body", pr_body,
        ]
        if repo_nwo:
            pr_cmd += ["--repo", repo_nwo]

        try:
            pr_result = subprocess.run(
                pr_cmd, capture_output=True, text=True, timeout=60,
            )
            if pr_result.returncode == 0:
                console.print(f"\n[green]PR created:[/green] {pr_result.stdout.strip()}")
            else:
                click.echo(f"Warning: PR creation failed: {pr_result.stderr.strip()}")
        except Exception as exc:
            click.echo(f"Warning: PR creation failed: {exc}")

    console.print(f"\n[green]Done! Issue #{issue_number} fix complete.[/green]")


async def _plan_only(daemon, prompt: str):
    """Run the daemon's plan phase only, returning the TaskGraph."""
    import os
    import uuid

    from forge.storage.db import Database

    db_path = os.path.join(daemon._project_dir, ".forge", "forge.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.initialize()
    try:
        pipeline_id = str(uuid.uuid4())
        await db.create_pipeline(
            id=pipeline_id, description=prompt,
            project_dir=daemon._project_dir, model_strategy=daemon._strategy,
            budget_limit_usd=daemon._settings.budget_limit_usd,
        )
        return await daemon.plan(prompt, db, pipeline_id=pipeline_id)
    finally:
        await db.close()
