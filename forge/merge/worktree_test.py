import os
import subprocess
import pytest

from forge.merge.worktree import WorktreeManager


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def manager(git_repo):
    worktrees_dir = git_repo.parent / "worktrees"
    return WorktreeManager(repo_path=str(git_repo), worktrees_dir=str(worktrees_dir))


def test_create_worktree(manager, git_repo):
    path = manager.create("task-1")
    assert os.path.isdir(path)
    assert os.path.exists(os.path.join(path, "README.md"))


def test_create_worktree_branch_name(manager, git_repo):
    path = manager.create("task-1")
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=path, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "forge/task-1"


def test_remove_worktree(manager):
    path = manager.create("task-2")
    assert os.path.isdir(path)
    manager.remove("task-2")
    assert not os.path.isdir(path)


def test_list_worktrees(manager):
    manager.create("task-a")
    manager.create("task-b")
    active = manager.list_active()
    assert "task-a" in active
    assert "task-b" in active


def test_create_duplicate_raises(manager):
    manager.create("task-dup")
    with pytest.raises(ValueError, match="already exists"):
        manager.create("task-dup")


def test_remove_nonexistent_raises(manager):
    with pytest.raises(ValueError, match="does not exist"):
        manager.remove("ghost")
