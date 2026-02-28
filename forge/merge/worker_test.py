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
