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
