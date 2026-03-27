"""Tests for pre-flight validation system."""

import os

import pytest

from forge.core.preflight import (
    CheckResult,
    PreflightReport,
    _check_claude_cli,
    _check_disk_space,
    _check_gh_cli,
    _check_git_installed,
    run_preflight,
)


class TestCheckResult:
    def test_passed_check(self):
        r = CheckResult(name="test", passed=True, message="OK")
        assert r.passed
        assert r.severity == "error"

    def test_failed_check(self):
        r = CheckResult(name="test", passed=False, message="Bad")
        assert not r.passed


class TestPreflightReport:
    def test_all_passed(self):
        report = PreflightReport(
            checks=[
                CheckResult(name="a", passed=True),
                CheckResult(name="b", passed=True),
            ]
        )
        assert report.passed
        assert len(report.errors) == 0
        assert len(report.warnings) == 0

    def test_error_blocks(self):
        report = PreflightReport(
            checks=[
                CheckResult(name="a", passed=True),
                CheckResult(name="b", passed=False, severity="error"),
            ]
        )
        assert not report.passed
        assert len(report.errors) == 1

    def test_warning_does_not_block(self):
        report = PreflightReport(
            checks=[
                CheckResult(name="a", passed=True),
                CheckResult(name="b", passed=False, severity="warning"),
            ]
        )
        assert report.passed  # warnings don't block
        assert len(report.warnings) == 1

    def test_summary_with_errors(self):
        report = PreflightReport(
            checks=[
                CheckResult(name="a", passed=False, severity="error"),
                CheckResult(name="b", passed=True),
            ]
        )
        summary = report.summary()
        assert "1 error" in summary

    def test_summary_all_passed(self):
        report = PreflightReport(
            checks=[
                CheckResult(name="a", passed=True),
                CheckResult(name="b", passed=True),
            ]
        )
        summary = report.summary()
        assert "all 2 checks passed" in summary


class TestIndividualChecks:
    def test_git_installed(self):
        result = _check_git_installed()
        assert result.passed  # git should be installed in dev environment
        assert "git version" in result.message

    def test_claude_cli(self):
        # Just verify it doesn't crash — result depends on environment
        result = _check_claude_cli()
        assert isinstance(result, CheckResult)

    def test_gh_cli(self):
        result = _check_gh_cli()
        assert isinstance(result, CheckResult)
        # gh not installed should be a warning, not error
        if not result.passed:
            assert result.severity == "warning"

    def test_disk_space(self):
        result = _check_disk_space(os.getcwd())
        assert result.passed  # dev machine should have space


@pytest.mark.asyncio
async def test_run_preflight(tmp_path):
    """Integration test: run all preflight checks on a temp git repo."""
    # Create a minimal git repo
    import subprocess

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )

    report = await run_preflight(str(tmp_path), base_branch="main")
    # Should have multiple checks
    assert len(report.checks) >= 4
    # Git should be found
    git_check = next(c for c in report.checks if c.name == "git")
    assert git_check.passed


@pytest.mark.asyncio
async def test_preflight_bad_base_branch(tmp_path):
    """Preflight should catch a non-existent base branch."""
    import subprocess

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )

    report = await run_preflight(str(tmp_path), base_branch="nonexistent-branch-xyz")
    branch_check = next((c for c in report.checks if c.name == "base_branch"), None)
    assert branch_check is not None
    assert not branch_check.passed
    assert "nonexistent-branch-xyz" in branch_check.message


@pytest.mark.asyncio
async def test_run_preflight_super_repo(tmp_path):
    """Preflight passes when project_dir is a plain folder with git repos inside."""
    import subprocess

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    # Create two sub-repos inside a plain (non-git) folder
    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"], cwd=str(repo_dir), capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )

    from forge.core.models import RepoConfig

    repos = {
        "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
        "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
    }

    report = await run_preflight(str(tmp_path), repos=repos)

    # Git repo check must pass (checking sub-repos, not the wrapper)
    git_check = next((c for c in report.checks if c.name == "git_repo"), None)
    assert git_check is not None
    assert git_check.passed, f"git_repo check failed: {git_check.message}"

    # Base branch check must pass
    branch_check = next((c for c in report.checks if c.name == "base_branch"), None)
    assert branch_check is not None
    assert branch_check.passed, f"base_branch check failed: {branch_check.message}"

    # Overall must pass
    assert report.passed, f"Preflight failed: {report.summary()}"


@pytest.mark.asyncio
async def test_run_preflight_super_repo_git_wrapper(tmp_path):
    """Preflight passes when project_dir is a git-init'd wrapper with repos inside."""
    import subprocess

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    # Init the wrapper (but no commits, no tracked files)
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)

    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"], cwd=str(repo_dir), capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )

    from forge.core.models import RepoConfig

    repos = {
        "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
        "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
    }

    report = await run_preflight(str(tmp_path), repos=repos)
    git_check = next((c for c in report.checks if c.name == "git_repo"), None)
    assert git_check is not None
    assert git_check.passed, f"git_repo check failed: {git_check.message}"
    assert report.passed, f"Preflight failed: {report.summary()}"


@pytest.mark.asyncio
async def test_super_repo_single_repo_no_regression(tmp_path):
    """Single-repo mode still works exactly as before (no regression)."""
    import subprocess

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    # Normal single repo
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        env=git_env,
    )

    # No repos dict — single-repo mode
    report = await run_preflight(str(tmp_path), base_branch="main")
    assert report.passed, f"Single-repo preflight should pass: {report.summary()}"

    # With a single "default" repo dict — should also work
    from forge.core.models import RepoConfig

    repos = {"default": RepoConfig(id="default", path=str(tmp_path), base_branch="main")}
    report = await run_preflight(str(tmp_path), repos=repos)
    assert report.passed, f"Single default repo preflight should pass: {report.summary()}"
