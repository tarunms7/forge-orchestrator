"""Tests for project snapshot gathering."""
import os
import subprocess

import pytest

from forge.core.context import ProjectSnapshot, gather_project_snapshot


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
