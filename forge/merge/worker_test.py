import subprocess
import pytest

from forge.merge.worker import MergeWorker, MergeResult, _parse_conflict_files_from_stderr


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


# ---------------------------------------------------------------------------
# prepare_for_resolution tests
# ---------------------------------------------------------------------------


def test_prepare_for_resolution_leaves_rebase_paused(tmp_path):
    """prepare_for_resolution() should NOT abort the rebase — conflict markers
    must remain in the working tree for the Tier 2 resolver to see them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)

    (repo / "shared.py").write_text("original\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # Task branch with conflicting change
    _run(["git", "checkout", "-b", "forge/task-resolve"], cwd=repo)
    (repo / "shared.py").write_text("task version\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "task change"], cwd=repo)

    # Master with a different change to the same file
    _run(["git", "checkout", "master"], cwd=repo)
    (repo / "shared.py").write_text("master version\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "master change"], cwd=repo)

    # Create worktree
    wt = tmp_path / "worktrees" / "task-resolve"
    _run(["git", "worktree", "add", str(wt), "forge/task-resolve"], cwd=repo)

    worker = MergeWorker(repo_path=str(repo))
    result = worker.prepare_for_resolution("forge/task-resolve", worktree_path=str(wt))

    # Should report conflict
    assert result.success is False
    assert "shared.py" in result.conflicting_files

    # The rebase should STILL be in progress (not aborted)
    rebase_dir = wt / ".git"
    # In a worktree .git is a file pointing to the main repo, so check via git status
    status = subprocess.run(
        ["git", "status"], cwd=wt, capture_output=True, text=True
    )
    assert "rebase in progress" in status.stdout.lower() or "rebase" in status.stdout.lower(), (
        f"Rebase should still be in progress but git status says: {status.stdout}"
    )

    # The conflict markers should be present in the file
    content = (wt / "shared.py").read_text()
    assert "<<<<<<<" in content, (
        f"Conflict markers should be present but file contains: {content}"
    )
    assert "=======" in content
    assert ">>>>>>>" in content

    # Clean up
    subprocess.run(["git", "rebase", "--abort"], cwd=wt, capture_output=True)


def test_prepare_for_resolution_clean_rebase_returns_success(tmp_path):
    """When there's no conflict, prepare_for_resolution() should return success=True."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)

    (repo / "base.py").write_text("# base\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # Task branch with non-conflicting change
    _run(["git", "checkout", "-b", "forge/task-clean2"], cwd=repo)
    (repo / "feature.py").write_text("# feature\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "add feature"], cwd=repo)

    # Master with a different file
    _run(["git", "checkout", "master"], cwd=repo)
    (repo / "other.py").write_text("# other\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "add other"], cwd=repo)

    # Create worktree
    wt = tmp_path / "worktrees" / "task-clean2"
    _run(["git", "worktree", "add", str(wt), "forge/task-clean2"], cwd=repo)

    worker = MergeWorker(repo_path=str(repo))
    result = worker.prepare_for_resolution("forge/task-clean2", worktree_path=str(wt))

    assert result.success is True
    assert result.conflicting_files == []


def test_prepare_resolve_then_merge_full_flow(tmp_path):
    """End-to-end: prepare_for_resolution → manually resolve → git rebase --continue → merge()."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)

    (repo / "shared.py").write_text("original\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # Task branch
    _run(["git", "checkout", "-b", "forge/task-e2e"], cwd=repo)
    (repo / "shared.py").write_text("task version\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "task change"], cwd=repo)

    # Master
    _run(["git", "checkout", "master"], cwd=repo)
    (repo / "shared.py").write_text("master version\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "master change"], cwd=repo)

    # Worktree
    wt = tmp_path / "worktrees" / "task-e2e"
    _run(["git", "worktree", "add", str(wt), "forge/task-e2e"], cwd=repo)

    worker = MergeWorker(repo_path=str(repo))

    # Step 1: Prepare (leaves rebase paused)
    prep = worker.prepare_for_resolution("forge/task-e2e", worktree_path=str(wt))
    assert prep.success is False
    assert "shared.py" in prep.conflicting_files

    # Step 2: Simulate what the resolver agent would do — resolve markers
    (wt / "shared.py").write_text("merged: both task and master version\n")
    _run(["git", "add", "."], cwd=wt)

    # GIT_EDITOR=true prevents the editor from opening during rebase --continue
    env = {**subprocess.os.environ, "GIT_EDITOR": "true"}
    subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=wt, check=True, capture_output=True, env=env,
    )

    # Step 3: merge() should now succeed (rebase is complete, just need ff)
    merge_result = worker.merge("forge/task-e2e", worktree_path=str(wt))
    assert merge_result.success is True

    # Verify the merged content is on master (use git show, not checkout,
    # because update-ref advances the ref without updating the working tree)
    show_result = subprocess.run(
        ["git", "show", "master:shared.py"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    assert show_result.stdout == "merged: both task and master version\n"


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


# ---------------------------------------------------------------------------
# _parse_conflict_files_from_stderr tests
# ---------------------------------------------------------------------------


def test_parse_conflict_files_from_stderr_content_conflict():
    stderr = (
        "Auto-merging shared.py\n"
        "CONFLICT (content): Merge conflict in shared.py\n"
        "error: could not apply abc1234... task change\n"
    )
    files = _parse_conflict_files_from_stderr(stderr)
    assert files == ["shared.py"]


def test_parse_conflict_files_from_stderr_multiple_conflicts():
    stderr = (
        "CONFLICT (content): Merge conflict in src/app.py\n"
        "CONFLICT (add/add): Merge conflict in docs/plan.md\n"
        "CONFLICT (content): Merge conflict in tests/test_app.py\n"
    )
    files = _parse_conflict_files_from_stderr(stderr)
    assert files == ["src/app.py", "docs/plan.md", "tests/test_app.py"]


def test_parse_conflict_files_from_stderr_no_conflicts():
    stderr = "error: could not apply abc1234... some commit\n"
    files = _parse_conflict_files_from_stderr(stderr)
    assert files == []


def test_parse_conflict_files_from_stderr_empty():
    assert _parse_conflict_files_from_stderr("") == []
    assert _parse_conflict_files_from_stderr(None) == []


def test_parse_conflict_files_from_stderr_modify_delete():
    """modify/delete conflicts don't use 'Merge conflict in' format."""
    stderr = (
        "CONFLICT (modify/delete): src/api.py deleted in HEAD "
        "and modified in abc1234... task change\n"
    )
    files = _parse_conflict_files_from_stderr(stderr)
    assert files == ["src/api.py"]


def test_parse_conflict_files_from_stderr_rename_delete():
    """rename/delete conflicts use yet another format."""
    stderr = (
        "CONFLICT (rename/delete): old_name.py renamed to "
        "new_name.py in HEAD, deleted in abc1234\n"
    )
    files = _parse_conflict_files_from_stderr(stderr)
    assert files == ["old_name.py"]


def test_parse_conflict_files_from_stderr_mixed_types():
    """Multiple conflict types in one rebase should all be captured."""
    stderr = (
        "CONFLICT (content): Merge conflict in shared.py\n"
        "CONFLICT (modify/delete): removed.py deleted in HEAD "
        "and modified in abc1234\n"
        "CONFLICT (rename/delete): old.py renamed to new.py "
        "in HEAD, deleted in def5678\n"
    )
    files = _parse_conflict_files_from_stderr(stderr)
    assert "shared.py" in files
    assert "removed.py" in files
    assert "old.py" in files
    assert len(files) == 3


def test_parse_conflict_files_from_stderr_no_duplicates():
    """Same file mentioned twice should appear only once."""
    stderr = (
        "CONFLICT (content): Merge conflict in shared.py\n"
        "CONFLICT (content): Merge conflict in shared.py\n"
    )
    files = _parse_conflict_files_from_stderr(stderr)
    assert files == ["shared.py"]
