"""Tests for project snapshot gathering."""
import asyncio
import os
import subprocess
from unittest.mock import patch

import pytest

from forge.core.context import (
    ProjectSnapshot,
    gather_project_snapshot,
    gather_multi_repo_snapshots,
    format_multi_repo_snapshot,
    _truncate_file_tree,
)
from forge.core.models import RepoConfig


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with some files."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    # Create some files
    (tmp_path / "README.md").write_text("# Test Project\nThis is a test.\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\nversion = "0.1.0"\n')

    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text('"""Source package."""\n')
    (src / "main.py").write_text("def hello():\n    return 'world'\n")
    (src / "utils.py").write_text("def add(a, b):\n    return a + b\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_hello():\n    pass\n")

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


def test_snapshot_returns_dataclass(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert isinstance(snap, ProjectSnapshot)


def test_snapshot_file_tree_contains_tracked_files(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "main.py" in snap.file_tree
    assert "README.md" in snap.file_tree


def test_snapshot_total_files(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    # README.md, pyproject.toml, src/__init__.py, src/main.py, src/utils.py, tests/test_main.py
    assert snap.total_files == 6


def test_snapshot_languages(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert snap.languages.get(".py", 0) >= 4


def test_snapshot_readme_excerpt(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "Test Project" in snap.readme_excerpt


def test_snapshot_config_summary(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "test" in snap.config_summary
    assert "0.1.0" in snap.config_summary


def test_snapshot_recent_commits(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "init" in snap.recent_commits


def test_snapshot_git_branch(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    # Default branch varies (main or master)
    assert snap.git_branch in ("main", "master")


def test_snapshot_no_readme(git_repo):
    os.remove(git_repo / "README.md")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "rm readme"], cwd=git_repo, capture_output=True, check=True)
    snap = gather_project_snapshot(str(git_repo))
    assert snap.readme_excerpt == ""


def test_format_for_planner(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    text = snap.format_for_planner()
    assert "main.py" in text
    assert "Test Project" in text


def test_format_for_agent(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    text = snap.format_for_agent()
    assert "main.py" in text
    # Agent format should NOT include full README
    assert len(text) < len(snap.format_for_planner())


def test_format_for_reviewer(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    text = snap.format_for_reviewer()
    assert "main.py" in text


class TestGatherMultiRepoSnapshots:
    """Tests for parallel multi-repo snapshot gathering."""

    def test_gather_multi_repo_snapshots(self, tmp_path):
        """Parallel gathering from 2 repos produces dict keyed by repo ID."""
        # Create two minimal git repos
        for name in ("backend", "frontend"):
            repo = tmp_path / name
            repo.mkdir()
            (repo / ".git").mkdir()  # Fake git dir
            (repo / "README.md").write_text(f"# {name}")

        repos = {
            "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
            "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
        }

        with patch("forge.core.context.gather_project_snapshot") as mock_gather:
            mock_gather.side_effect = lambda path: ProjectSnapshot(
                file_tree=f"tree-for-{path.split('/')[-1]}",
                total_files=10,
                total_loc=100,
                git_branch="main",
            )
            result = asyncio.run(gather_multi_repo_snapshots(repos))

        assert set(result.keys()) == {"backend", "frontend"}
        assert result["backend"].file_tree == "tree-for-backend"
        assert result["frontend"].file_tree == "tree-for-frontend"
        # Verify parallel execution (both calls made)
        assert mock_gather.call_count == 2

    def test_gather_multi_repo_snapshot_failure_returns_empty(self, tmp_path):
        """If one repo's snapshot fails, return empty snapshot for that repo."""
        repos = {
            "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
            "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
        }

        def side_effect(path):
            if "backend" in path:
                raise OSError("git ls-files failed")
            return ProjectSnapshot(file_tree="frontend-tree", total_files=5, total_loc=50)

        with patch("forge.core.context.gather_project_snapshot", side_effect=side_effect):
            result = asyncio.run(gather_multi_repo_snapshots(repos))

        assert result["backend"].total_files == 0  # empty fallback
        assert result["frontend"].file_tree == "frontend-tree"


class TestFormatMultiRepoSnapshot:
    """Tests for multi-repo snapshot formatting."""

    def test_format_multi_repo_snapshot(self):
        """Labeled sections per repo with ### Repo: headers."""
        snapshots = {
            "backend": ProjectSnapshot(
                file_tree="src/\n  main.py",
                total_files=10,
                total_loc=500,
                git_branch="main",
            ),
            "frontend": ProjectSnapshot(
                file_tree="src/\n  App.tsx",
                total_files=8,
                total_loc=300,
                git_branch="main",
            ),
        }
        repos = {
            "backend": RepoConfig(id="backend", path="/workspace/backend", base_branch="main"),
            "frontend": RepoConfig(id="frontend", path="/workspace/frontend", base_branch="main"),
        }

        result = format_multi_repo_snapshot(snapshots, repos)

        assert "### Repo: backend (/workspace/backend)" in result
        assert "### Repo: frontend (/workspace/frontend)" in result
        assert "src/\n  main.py" in result
        assert "src/\n  App.tsx" in result


class TestTruncateFileTree:
    """Tests for large repo file tree truncation."""

    def test_truncate_large_repo_tree(self):
        """Repos with 500+ files get truncated to depth 3."""
        # Build a file tree with 500+ entries at depth 4+
        lines = []
        for i in range(100):
            lines.append("src/")
            lines.append(f"  module_{i}/")
            lines.append("    sub/")
            lines.append("      deep/")
            lines.append(f"        file_{i}.py")
            lines.append(f"        test_{i}.py")
        tree = "\n".join(lines)

        result = _truncate_file_tree(tree, total_files=600, max_depth=3)

        # Depth 3 items should be present (src/, module_X/, sub/)
        assert "src/" in result
        assert "module_0/" in result
        assert "sub/" in result
        # Depth 4+ should be truncated
        assert "deep/" not in result
        assert "file_0.py" not in result
        # Should include truncation notice
        assert "truncated" in result.lower() or "..." in result

    def test_no_truncation_for_small_repos(self):
        """Repos with <500 files are not truncated."""
        tree = "src/\n  main.py\n  utils.py"
        result = _truncate_file_tree(tree, total_files=3, max_depth=3)
        assert result == tree  # unchanged
