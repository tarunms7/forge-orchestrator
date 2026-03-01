import subprocess
import pytest

from forge.merge.worker import MergeWorker, MergeResult


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "base.py").write_text("# base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _create_branch_with_commit(repo, branch: str, filename: str, content: str):
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"add {filename}"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)


def test_successful_merge(git_repo):
    _create_branch_with_commit(git_repo, "forge/task-1", "feature.py", "# feature\n")
    worker = MergeWorker(repo_path=str(git_repo))
    result = worker.merge("forge/task-1")
    assert result.success is True
    assert (git_repo / "feature.py").exists()


def test_merge_conflict_detected(git_repo):
    _create_branch_with_commit(git_repo, "forge/task-2", "conflict.py", "version A\n")
    (git_repo / "conflict.py").write_text("version B\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "conflict on master"], cwd=git_repo, check=True, capture_output=True)
    worker = MergeWorker(repo_path=str(git_repo))
    result = worker.merge("forge/task-2")
    assert result.success is False
    assert len(result.conflicting_files) > 0


def test_retry_merge_after_rebase_conflict(git_repo):
    """MergeWorker.retry_merge() should re-fetch main and retry rebase."""
    worker = MergeWorker(str(git_repo), main_branch="master")

    # Create a branch with changes
    subprocess.run(
        ["git", "checkout", "-b", "forge/task-1"],
        cwd=git_repo, check=True, capture_output=True,
    )
    (git_repo / "feature.py").write_text("# feature\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add feature"],
        cwd=git_repo, check=True, capture_output=True,
    )

    # Go back to master and add a non-conflicting change
    subprocess.run(
        ["git", "checkout", "master"],
        cwd=git_repo, check=True, capture_output=True,
    )
    (git_repo / "other.py").write_text("# other\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add other"],
        cwd=git_repo, check=True, capture_output=True,
    )

    # retry_merge should succeed since there's no conflict
    result = worker.retry_merge("forge/task-1")
    assert result.success is True


def test_merge_result_fields():
    r = MergeResult(success=True, conflicting_files=[])
    assert r.success is True
    assert r.conflicting_files == []


# ---------------------------------------------------------------------------
# Worktree-aware _find_conflicts tests
# ---------------------------------------------------------------------------

def _run(cmd, cwd, **kw):
    """Shorthand for subprocess.run with common defaults."""
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True, **kw)


@pytest.fixture
def worktree_conflict_setup(tmp_path):
    """Create a main repo + worktree with a rebase conflict in progress.

    Returns (repo_path, worktree_path, conflicting_filename).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)

    # Initial commit on master
    (repo / "shared.py").write_text("original\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # Create a task branch with a conflicting change
    _run(["git", "checkout", "-b", "forge/task-conflict"], cwd=repo)
    (repo / "shared.py").write_text("task version\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "task change"], cwd=repo)

    # Back to master and make a different change to the same file
    _run(["git", "checkout", "master"], cwd=repo)
    (repo / "shared.py").write_text("master version\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "master change"], cwd=repo)

    # Create a worktree for the task branch
    wt = tmp_path / "worktrees" / "task-conflict"
    _run(["git", "worktree", "add", str(wt), "forge/task-conflict"], cwd=repo)

    # Trigger a rebase inside the worktree (will fail with conflict)
    rebase_result = subprocess.run(
        ["git", "rebase", "master"],
        cwd=wt,
        capture_output=True,
        text=True,
    )
    assert rebase_result.returncode != 0, "Expected rebase to fail with conflict"

    return str(repo), str(wt), "shared.py"


def test_find_conflicts_with_worktree_returns_files(worktree_conflict_setup):
    """_find_conflicts should return conflicting file names when given worktree_path."""
    repo_path, worktree_path, conflicting_file = worktree_conflict_setup
    worker = MergeWorker(repo_path=repo_path)

    # Without worktree_path: main repo has no rebase state -> empty
    conflicts_main = worker._find_conflicts()
    assert conflicts_main == [], (
        "Main repo should have no conflicts (rebase is in the worktree)"
    )

    # With worktree_path: the worktree has a rebase in progress -> non-empty
    conflicts_wt = worker._find_conflicts(worktree_path)
    assert len(conflicts_wt) > 0, (
        "_find_conflicts should detect conflicts when given worktree_path"
    )
    assert conflicting_file in conflicts_wt

    # Clean up rebase state
    subprocess.run(["git", "rebase", "--abort"], cwd=worktree_path, capture_output=True)


def test_merge_with_worktree_conflict_populates_files(tmp_path):
    """merge() with worktree_path should populate conflicting_files on conflict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)

    # Initial commit
    (repo / "app.py").write_text("original\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # Task branch with conflicting edit
    _run(["git", "checkout", "-b", "forge/task-wt"], cwd=repo)
    (repo / "app.py").write_text("task edit\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "task edit"], cwd=repo)

    # Master with a different edit to the same file
    _run(["git", "checkout", "master"], cwd=repo)
    (repo / "app.py").write_text("master edit\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "master edit"], cwd=repo)

    # Create worktree for the task branch
    wt = tmp_path / "worktrees" / "task-wt"
    _run(["git", "worktree", "add", str(wt), "forge/task-wt"], cwd=repo)

    worker = MergeWorker(repo_path=str(repo))
    result = worker.merge("forge/task-wt", worktree_path=str(wt))

    assert result.success is False
    assert len(result.conflicting_files) > 0, (
        "conflicting_files must be populated when merging via worktree"
    )
    assert "app.py" in result.conflicting_files
    assert "Rebase conflict in:" in result.error
    # The error message should contain the actual file name, not "unknown files"
    assert "unknown files" not in result.error


def test_merge_with_worktree_clean_succeeds(tmp_path):
    """merge() with worktree_path should return success=True and empty conflicting_files
    when there is no conflict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)

    # Initial commit
    (repo / "base.py").write_text("# base\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # Task branch with a non-conflicting change
    _run(["git", "checkout", "-b", "forge/task-clean"], cwd=repo)
    (repo / "feature.py").write_text("# new feature\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "add feature"], cwd=repo)

    # Back to master, add a different file
    _run(["git", "checkout", "master"], cwd=repo)
    (repo / "other.py").write_text("# other\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "add other"], cwd=repo)

    # Create worktree for the task branch
    wt = tmp_path / "worktrees" / "task-clean"
    _run(["git", "worktree", "add", str(wt), "forge/task-clean"], cwd=repo)

    worker = MergeWorker(repo_path=str(repo))
    result = worker.merge("forge/task-clean", worktree_path=str(wt))

    assert result.success is True
    assert result.conflicting_files == []
    assert result.error is None


def test_find_conflicts_without_worktree_path_uses_repo(git_repo):
    """_find_conflicts with no worktree_path should use self._repo (backward compat)."""
    _create_branch_with_commit(git_repo, "forge/task-compat", "compat.py", "branch ver\n")

    # Create a conflict on master
    (git_repo / "compat.py").write_text("master ver\n")
    _run(["git", "add", "."], cwd=git_repo)
    _run(["git", "commit", "-m", "master conflict"], cwd=git_repo)

    worker = MergeWorker(repo_path=str(git_repo))
    # Merge without worktree_path (the old code path)
    result = worker.merge("forge/task-compat")
    assert result.success is False
    # In the non-worktree path, conflicts should still be found
    # because the rebase runs in the main repo itself
    assert len(result.conflicting_files) > 0
    assert "compat.py" in result.conflicting_files
