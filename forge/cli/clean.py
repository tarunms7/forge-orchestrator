"""Forge CLI clean command. Removes stale worktrees and orphaned forge branches."""

import logging
import os
import subprocess

import click
from rich.console import Console
from rich.table import Table

logger = logging.getLogger("forge.cli.clean")


def _is_git_repo(path: str) -> bool:
    """Check if a path is a valid git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=path,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


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
    if not names:
        return []
    if not _is_git_repo(project_dir):
        import shutil as _shutil
        removed = []
        for name in names:
            wt_path = os.path.join(worktrees_dir, name)
            try:
                _shutil.rmtree(wt_path)
                removed.append(name)
            except OSError:
                pass
        return removed
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
            pass
    return removed


def _prune_worktrees(project_dir: str) -> None:
    """Run git worktree prune to clean up stale worktree admin files."""
    if not _is_git_repo(project_dir):
        return
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
    if not _is_git_repo(project_dir):
        return []
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
    project_dir: str,
    worktrees_dir: str,
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


def _discover_repo_paths(project_dir: str) -> list[str]:
    """Discover all known repo paths for multi-repo support.

    Scans for .forge/worktrees/ directories in:
    1. The top-level project dir (single-repo / default)
    2. Immediate subdirectories that contain .forge/worktrees/ (multi-repo)

    Returns a list of repo root paths that have .forge/worktrees/ directories.
    """
    repo_paths: list[str] = []

    # Always include the top-level project dir
    top_wt = os.path.join(project_dir, ".forge", "worktrees")
    if os.path.isdir(top_wt):
        repo_paths.append(project_dir)

    # Scan immediate subdirectories for multi-repo worktree dirs
    try:
        for entry in os.listdir(project_dir):
            subdir = os.path.join(project_dir, entry)
            if not os.path.isdir(subdir) or entry.startswith("."):
                continue
            sub_wt = os.path.join(subdir, ".forge", "worktrees")
            if os.path.isdir(sub_wt):
                repo_paths.append(subdir)
    except OSError:
        pass

    return repo_paths


@click.command("clean")
@click.option("--project-dir", default=".", help="Project root directory")
def clean(project_dir: str) -> None:
    """Remove stale worktrees and orphaned forge branches."""
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")

    if not os.path.isdir(forge_dir):
        click.echo(f"Error: .forge directory not found at {forge_dir}")
        raise SystemExit(1)

    # Discover all repo paths (top-level + multi-repo subdirectories)
    repo_paths = _discover_repo_paths(project_dir)
    if not repo_paths:
        # Fall back to top-level worktrees dir even if empty
        repo_paths = [project_dir]

    all_removed_worktrees: list[str] = []
    all_deleted_branches: list[str] = []

    for repo_path in repo_paths:
        worktrees_dir = os.path.join(repo_path, ".forge", "worktrees")

        # Step 1: Remove stale worktree directories
        removed = _remove_worktrees(repo_path, worktrees_dir)
        all_removed_worktrees.extend(removed)

        # Step 2: Prune worktree admin files
        _prune_worktrees(repo_path)

        # Step 3: Delete orphaned forge/* branches (after worktrees removed)
        deleted = _delete_orphaned_branches(repo_path, worktrees_dir)
        all_deleted_branches.extend(deleted)

    # Step 4: Display summary
    if not all_removed_worktrees and not all_deleted_branches:
        click.echo("Nothing to clean.")
        return

    console = Console()
    table = Table(title="Forge Cleanup Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Names")

    if all_removed_worktrees:
        table.add_row(
            "Worktrees removed",
            str(len(all_removed_worktrees)),
            ", ".join(all_removed_worktrees),
        )

    if all_deleted_branches:
        table.add_row(
            "Branches cleaned",
            str(len(all_deleted_branches)),
            ", ".join(all_deleted_branches),
        )

    console.print(table)
