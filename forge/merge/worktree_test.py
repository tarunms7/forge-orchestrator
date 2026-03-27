import os
import subprocess

import pytest

from forge.merge.worktree import WorktreeManager


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True
    )
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
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
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


def test_remove_nonexistent_is_noop(manager):
    """Removing an already-removed worktree should be a no-op, not raise."""
    manager.remove("ghost")  # Should not raise


def test_create_cleans_up_directory_on_failure(manager, git_repo):
    """When 'git worktree add' fails, any leftover directory is removed."""
    task_id = "task-fail"
    expected_path = manager._task_path(task_id)

    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # Let rev-parse succeed (has_commits check), but simulate a partial
        # worktree creation: create the directory then raise.
        if "worktree" in cmd:
            os.makedirs(expected_path, exist_ok=True)
            raise subprocess.CalledProcessError(128, cmd)
        return original_run(cmd, **kwargs)

    from unittest.mock import patch

    with patch("forge.merge.worktree.subprocess.run", side_effect=fake_run):
        with pytest.raises(subprocess.CalledProcessError):
            manager.create(task_id)

    # Directory should be cleaned up
    assert not os.path.isdir(expected_path)


def test_atomic_gitignore_write_new_file(manager, git_repo):
    """_ensure_forge_gitignored creates .gitignore atomically."""
    gitignore_path = git_repo / ".gitignore"
    # Remove any existing .gitignore
    if gitignore_path.exists():
        gitignore_path.unlink()

    manager._ensure_forge_gitignored()

    content = gitignore_path.read_text()
    assert ".forge" in content


def test_atomic_gitignore_no_duplicate(manager, git_repo):
    """Calling _ensure_forge_gitignored twice doesn't duplicate the entry."""
    manager._ensure_forge_gitignored()
    manager._ensure_forge_gitignored()

    gitignore_path = git_repo / ".gitignore"
    content = gitignore_path.read_text()
    assert content.count(".forge") == 1


def test_atomic_gitignore_appends_to_existing(manager, git_repo):
    """_ensure_forge_gitignored appends to an existing .gitignore."""
    gitignore_path = git_repo / ".gitignore"
    gitignore_path.write_text("node_modules\n")

    manager._ensure_forge_gitignored()

    content = gitignore_path.read_text()
    assert "node_modules" in content
    assert ".forge" in content


@pytest.mark.asyncio
async def test_async_create_worktree(manager, git_repo):
    """async_create runs create() in an executor without blocking."""
    path = await manager.async_create("task-async-1")
    assert os.path.isdir(path)
    assert os.path.exists(os.path.join(path, "README.md"))


@pytest.mark.asyncio
async def test_async_remove_worktree(manager):
    """async_remove runs remove() in an executor without blocking."""
    path = await manager.async_create("task-async-2")
    assert os.path.isdir(path)
    await manager.async_remove("task-async-2")
    assert not os.path.isdir(path)
