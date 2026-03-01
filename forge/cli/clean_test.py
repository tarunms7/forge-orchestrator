"""Tests for forge clean CLI command."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from forge.cli.main import cli


@pytest.fixture()
def forge_project(tmp_path):
    """Create a temporary project with a .forge directory."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return tmp_path


def test_clean_no_forge_dir(tmp_path):
    """Error when .forge directory doesn't exist."""
    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "error" in result.output.lower()


def test_clean_no_worktrees_no_branches(forge_project):
    """Shows 'nothing to clean' when .forge/worktrees/ is empty and no forge/* branches exist."""
    # git branch --list forge/* returns nothing
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        runner = CliRunner()
        result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])

    assert result.exit_code == 0
    assert "nothing to clean" in result.output.lower()


def test_clean_removes_stale_worktrees(forge_project):
    """Mock stale worktree directories in .forge/worktrees/, verify git worktree remove is called."""
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()
    # Create fake worktree directories
    (worktrees_dir / "task-1").mkdir()
    (worktrees_dir / "task-2").mkdir()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        runner = CliRunner()
        result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])

    assert result.exit_code == 0
    assert "task-1" in result.output
    assert "task-2" in result.output

    # Verify git worktree remove was called for each worktree
    worktree_remove_calls = [
        c for c in mock_run.call_args_list
        if c.args and "worktree" in c.args[0] and "remove" in c.args[0]
    ]
    assert len(worktree_remove_calls) == 2


def test_clean_deletes_orphaned_branches(forge_project):
    """Mock 'git branch --list forge/*' returning branches with no worktree, verify git branch -D is called."""
    # No worktrees dir means no active worktrees
    branch_result = MagicMock()
    branch_result.returncode = 0
    branch_result.stdout = "  forge/orphan-1\n  forge/orphan-2\n"

    default_result = MagicMock()
    default_result.returncode = 0
    default_result.stdout = ""

    def side_effect(cmd, **kwargs):
        if "branch" in cmd and "--list" in cmd:
            return branch_result
        return default_result

    with patch("subprocess.run", side_effect=side_effect) as mock_run:
        runner = CliRunner()
        result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])

    assert result.exit_code == 0
    assert "orphan-1" in result.output
    assert "orphan-2" in result.output

    # Verify git branch -D was called for each orphaned branch
    branch_delete_calls = [
        c for c in mock_run.call_args_list
        if c.args and "branch" in c.args[0] and "-D" in c.args[0]
    ]
    assert len(branch_delete_calls) == 2


def test_clean_combined_summary(forge_project):
    """Both stale worktrees and orphaned branches, verify summary output shows counts."""
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()
    # Create a stale worktree directory
    (worktrees_dir / "wt-stale").mkdir()

    branch_result = MagicMock()
    branch_result.returncode = 0
    branch_result.stdout = "  forge/orphan-branch\n"

    default_result = MagicMock()
    default_result.returncode = 0
    default_result.stdout = ""

    def side_effect(cmd, **kwargs):
        if "branch" in cmd and "--list" in cmd:
            return branch_result
        return default_result

    with patch("subprocess.run", side_effect=side_effect) as mock_run:
        runner = CliRunner()
        result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])

    assert result.exit_code == 0
    # Both worktree and branch appear in output
    assert "wt-stale" in result.output
    assert "orphan-branch" in result.output
    # Verify both worktree remove and branch delete were called
    worktree_remove_calls = [
        c for c in mock_run.call_args_list
        if c.args and "worktree" in c.args[0] and "remove" in c.args[0]
    ]
    branch_delete_calls = [
        c for c in mock_run.call_args_list
        if c.args and "branch" in c.args[0] and "-D" in c.args[0]
    ]
    assert len(worktree_remove_calls) >= 1
    assert len(branch_delete_calls) >= 1


def test_clean_handles_git_error(forge_project):
    """Graceful handling when git commands fail."""
    worktrees_dir = forge_project / ".forge" / "worktrees"
    worktrees_dir.mkdir()
    (worktrees_dir / "failing-task").mkdir()

    def side_effect(cmd, **kwargs):
        if "worktree" in cmd and "remove" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        if "branch" in cmd and "--list" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        if "worktree" in cmd and "prune" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        return result

    with patch("subprocess.run", side_effect=side_effect):
        runner = CliRunner()
        result = runner.invoke(cli, ["clean", "--project-dir", str(forge_project)])

    # Should not crash; exits cleanly
    assert result.exit_code == 0


def test_clean_help():
    """Verify --help works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--help"])
    assert result.exit_code == 0
    assert "clean" in result.output.lower()
