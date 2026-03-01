"""Forge CLI clean command. Removes stale worktrees and orphaned forge branches."""

import os
import subprocess

import click
from rich.console import Console
from rich.table import Table


def _list_worktree_dirs(worktrees_dir: str) -> list[str]:
    """Return names of subdirectories in the worktrees directory."""
    if not os.path.isdir(worktrees_dir):
        return []
    return [
        name
        for name in os.listdir(worktrees_dir)
        if os.path.isdir(os.path.join(worktrees_dir, name))
    ]


def _remove_worktrees(project_dir: str, worktrees_dir: str) -> list[str]:
    """Remove all worktree directories under worktrees_dir. Returns names removed."""
    names = _list_worktree_dirs(worktrees_dir)
    removed: list[str] = []
    for name in names:
        wt_path = os.path.join(worktrees_dir, name)
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt_path],
                cwd=project_dir,
                check=True,
                capture_output=True,
            )
            removed.append(name)
        except subprocess.CalledProcessError:
            # If git worktree remove fails, try to continue
            pass
    return removed


def _prune_worktrees(project_dir: str) -> None:
    """Run git worktree prune to clean up stale worktree admin files."""
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass


def _list_forge_branches(project_dir: str) -> list[str]:
    """Return all local branch names matching 'forge/*' pattern."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "forge/*"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        branches = []
        for line in result.stdout.splitlines():
            branch = line.strip().lstrip("* ")
            if branch:
                branches.append(branch)
        return branches
    except subprocess.CalledProcessError:
        return []


def _delete_orphaned_branches(
    project_dir: str, worktrees_dir: str,
) -> list[str]:
    """Delete forge/* branches that have no corresponding worktree directory.

    Returns the task IDs (not full branch names) of deleted branches.
    """
    branches = _list_forge_branches(project_dir)
    active_worktrees = set(_list_worktree_dirs(worktrees_dir))
    deleted: list[str] = []

    for branch in branches:
        # Branch format is 'forge/{task_id}'
        task_id = branch.removeprefix("forge/")
        if task_id not in active_worktrees:
            try:
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=project_dir,
                    check=True,
                    capture_output=True,
                )
                deleted.append(task_id)
            except subprocess.CalledProcessError:
                pass

    return deleted


@click.command("clean")
@click.option("--project-dir", default=".", help="Project root directory")
def clean(project_dir: str) -> None:
    """Remove stale worktrees and orphaned forge branches."""
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")

    if not os.path.isdir(forge_dir):
        click.echo(f"Error: .forge directory not found at {forge_dir}")
        raise SystemExit(1)

    worktrees_dir = os.path.join(forge_dir, "worktrees")

    # Step 1: Remove stale worktree directories
    removed_worktrees = _remove_worktrees(project_dir, worktrees_dir)

    # Step 2: Prune worktree admin files
    _prune_worktrees(project_dir)

    # Step 3: Delete orphaned forge/* branches (after worktrees removed)
    deleted_branches = _delete_orphaned_branches(project_dir, worktrees_dir)

    # Step 4: Display summary
    if not removed_worktrees and not deleted_branches:
        click.echo("Nothing to clean.")
        return

    console = Console()
    table = Table(title="Forge Cleanup Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Names")

    if removed_worktrees:
        table.add_row(
            "Worktrees removed",
            str(len(removed_worktrees)),
            ", ".join(removed_worktrees),
        )

    if deleted_branches:
        table.add_row(
            "Branches cleaned",
            str(len(deleted_branches)),
            ", ".join(deleted_branches),
        )

    console.print(table)
