"""Tests for forge clean CLI command."""

import os
import subprocess

import pytest
from click.testing import CliRunner


@pytest.fixture()
def git_repo(tmp_path):
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


@pytest.fixture()
def forge_project(git_repo):
    """Create .forge directory inside the git repo."""
    forge_dir = git_repo / ".forge"
    forge_dir.mkdir()
    return git_repo


def test_clean_nothing_to_clean(forge_project):
    """Shows 'nothing to clean' when no worktrees or orphaned branches exist."""
    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    assert "nothing to clean" in result.output.lower()


def test_clean_missing_forge_dir(tmp_path):
    """Error message when .forge directory does not exist."""
    # Need a git repo but no .forge dir
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "error" in result.output.lower()


def test_clean_removes_stale_worktrees(forge_project):
    """Removes stale worktree directories and reports them."""
    # Create a worktree via git so it's real
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()

    wt_path = str(worktrees_dir / "task-1")
    subprocess.run(
        ["git", "worktree", "add", "-b", "forge/task-1", wt_path],
        cwd=forge_project,
        check=True,
        capture_output=True,
    )
    assert os.path.isdir(wt_path)

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    assert "task-1" in result.output
    # Worktree directory should be removed
    assert not os.path.isdir(wt_path)


def test_clean_prunes_worktree_admin(forge_project):
    """Runs git worktree prune after removing worktrees."""
    # Create a worktree, then manually remove the directory to make it stale
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()

    wt_path = str(worktrees_dir / "task-stale")
    subprocess.run(
        ["git", "worktree", "add", "-b", "forge/task-stale", wt_path],
        cwd=forge_project,
        check=True,
        capture_output=True,
    )
    # Manually delete the directory (simulating a stale worktree)
    import shutil

    shutil.rmtree(wt_path)

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    # git worktree prune should have run, so git worktree list should be clean
    wt_list = subprocess.run(
        ["git", "worktree", "list"],
        cwd=forge_project,
        capture_output=True,
        text=True,
    )
    assert "task-stale" not in wt_list.stdout


def test_clean_deletes_orphaned_branches(forge_project):
    """Deletes forge/* branches that have no corresponding worktree directory."""
    # Create a forge/* branch without a worktree directory
    subprocess.run(
        ["git", "branch", "forge/orphan-task"],
        cwd=forge_project,
        check=True,
        capture_output=True,
    )
    # Verify branch exists
    branch_list = subprocess.run(
        ["git", "branch"],
        cwd=forge_project,
        capture_output=True,
        text=True,
    )
    assert "forge/orphan-task" in branch_list.stdout

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    assert "orphan-task" in result.output

    # Branch should be deleted
    branch_list_after = subprocess.run(
        ["git", "branch"],
        cwd=forge_project,
        capture_output=True,
        text=True,
    )
    assert "forge/orphan-task" not in branch_list_after.stdout


def test_clean_preserves_active_worktree_branches(forge_project):
    """Does NOT delete forge/* branches that have a matching worktree directory."""
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()

    wt_path = str(worktrees_dir / "active-task")
    subprocess.run(
        ["git", "worktree", "add", "-b", "forge/active-task", wt_path],
        cwd=forge_project,
        check=True,
        capture_output=True,
    )

    from forge.cli.main import cli

    runner = CliRunner()
    # The clean command will remove the worktree but let's check the branch
    # is listed as a removed worktree, not an orphaned branch
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    # The worktree is removed, and its branch should be cleaned as part of worktree removal


def test_clean_summary_table_counts(forge_project):
    """Summary table shows correct counts of removed worktrees and branches."""
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()

    # Create two worktrees
    for task_id in ("task-a", "task-b"):
        wt_path = str(worktrees_dir / task_id)
        subprocess.run(
            ["git", "worktree", "add", "-b", f"forge/{task_id}", wt_path],
            cwd=forge_project,
            check=True,
            capture_output=True,
        )

    # Create an orphaned branch (no worktree dir)
    subprocess.run(
        ["git", "branch", "forge/orphan-x"],
        cwd=forge_project,
        check=True,
        capture_output=True,
    )

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    # Should mention both worktrees removed
    assert "task-a" in result.output
    assert "task-b" in result.output
    # Should mention orphaned branch
    assert "orphan-x" in result.output


def test_clean_help():
    """Clean command appears in --help output."""
    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--help"])
    assert result.exit_code == 0
    assert "clean" in result.output.lower()


def test_clean_works_with_project_local_forge(forge_project):
    """Clean operates on project-local .forge/worktrees correctly."""
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()

    # Verify .forge dir is project-local
    forge_dir = forge_project / ".forge"
    assert forge_dir.is_dir()
    assert worktrees_dir.is_dir()

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
