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
    _check_models_in_catalog,
    _check_provider_health,
    _check_routing_validity,
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
    from unittest.mock import patch

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

    # Mock Claude CLI checks — not available in CI
    mock_claude_check = CheckResult(name="claude", passed=True, message="mocked")
    mock_claude_auth = CheckResult(name="claude_auth", passed=True, message="mocked")
    with (
        patch("forge.core.preflight._check_claude_cli", return_value=mock_claude_check),
        patch("forge.core.preflight._check_claude_auth", return_value=mock_claude_auth),
    ):
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
    from unittest.mock import patch

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

    # Mock Claude CLI checks — not available in CI
    mock_claude_check = CheckResult(name="claude", passed=True, message="mocked")
    mock_claude_auth = CheckResult(name="claude_auth", passed=True, message="mocked")
    with (
        patch("forge.core.preflight._check_claude_cli", return_value=mock_claude_check),
        patch("forge.core.preflight._check_claude_auth", return_value=mock_claude_auth),
    ):
        report = await run_preflight(str(tmp_path), repos=repos)
    git_check = next((c for c in report.checks if c.name == "git_repo"), None)
    assert git_check is not None
    assert git_check.passed, f"git_repo check failed: {git_check.message}"
    assert report.passed, f"Preflight failed: {report.summary()}"


@pytest.mark.asyncio
async def test_super_repo_single_repo_no_regression(tmp_path):
    """Single-repo mode still works exactly as before (no regression)."""
    import subprocess
    from unittest.mock import patch

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

    # Mock Claude CLI checks — not available in CI
    mock_claude_check = CheckResult(name="claude", passed=True, message="mocked")
    mock_claude_auth = CheckResult(name="claude_auth", passed=True, message="mocked")
    with (
        patch("forge.core.preflight._check_claude_cli", return_value=mock_claude_check),
        patch("forge.core.preflight._check_claude_auth", return_value=mock_claude_auth),
    ):
        # No repos dict — single-repo mode
        report = await run_preflight(str(tmp_path), base_branch="main")
        assert report.passed, f"Single-repo preflight should pass: {report.summary()}"

        # With a single "default" repo dict — should also work
        from forge.core.models import RepoConfig

        repos = {"default": RepoConfig(id="default", path=str(tmp_path), base_branch="main")}
        report = await run_preflight(str(tmp_path), repos=repos)
        assert report.passed, f"Single default repo preflight should pass: {report.summary()}"


# ── Provider-aware preflight checks ────────────────────────────────


class TestProviderHealthChecks:
    def test_no_registry_falls_back_to_claude_cli(self):
        """Without registry, falls back to Claude CLI check."""
        results = _check_provider_health(registry=None)
        assert len(results) >= 1
        assert results[0].name == "claude_cli"

    def test_registry_preflight_all(self):
        """With registry but no resolved_models, uses preflight_all()."""
        from unittest.mock import MagicMock

        mock_status = MagicMock()
        mock_status.healthy = True
        mock_status.details = "claude-code-sdk OK"
        mock_status.errors = []

        mock_registry = MagicMock()
        mock_registry.preflight_all.return_value = {"claude": mock_status}

        results = _check_provider_health(registry=mock_registry)
        mock_registry.preflight_all.assert_called_once()
        assert len(results) == 1
        assert results[0].passed
        assert "claude" in results[0].message

    def test_registry_preflight_for_pipeline(self):
        """With registry and resolved_models, uses preflight_for_pipeline()."""
        from unittest.mock import MagicMock

        from forge.providers.base import ModelSpec

        mock_status = MagicMock()
        mock_status.healthy = True
        mock_status.details = "OK"
        mock_status.errors = []

        mock_registry = MagicMock()
        mock_registry.preflight_for_pipeline.return_value = {"claude": mock_status}

        resolved = {"planner": ModelSpec("claude", "opus")}
        results = _check_provider_health(registry=mock_registry, resolved_models=resolved)
        mock_registry.preflight_for_pipeline.assert_called_once_with(resolved)
        assert len(results) == 1
        assert results[0].passed

    def test_unhealthy_provider_fails(self):
        """Unhealthy provider produces a failing check."""
        from unittest.mock import MagicMock

        mock_status = MagicMock()
        mock_status.healthy = False
        mock_status.details = ""
        mock_status.errors = ["API key not set"]

        mock_registry = MagicMock()
        mock_registry.preflight_all.return_value = {"openai": mock_status}

        results = _check_provider_health(registry=mock_registry)
        assert len(results) == 1
        assert not results[0].passed
        assert "API key not set" in results[0].message


class TestModelsInCatalog:
    def test_valid_model_no_errors(self):
        """Known model should produce no errors."""
        from unittest.mock import MagicMock

        from forge.providers.base import ModelSpec

        mock_registry = MagicMock()
        mock_registry.validate_model.return_value = True

        resolved = {"planner": ModelSpec("claude", "opus")}
        results = _check_models_in_catalog(mock_registry, resolved)
        assert results == []

    def test_unknown_model_produces_error(self):
        """Unknown model should produce a failing CheckResult."""
        from unittest.mock import MagicMock

        from forge.providers.base import ModelSpec

        mock_registry = MagicMock()
        mock_registry.validate_model.return_value = False

        resolved = {"agent": ModelSpec("openai", "gpt-99")}
        results = _check_models_in_catalog(mock_registry, resolved)
        assert len(results) == 1
        assert not results[0].passed
        assert "gpt-99" in results[0].message
        assert "agent" in results[0].message


class TestRoutingValidity:
    """Tests for _check_routing_validity."""

    def test_no_issues_returns_empty(self):
        """Valid routing produces no check results."""
        from unittest.mock import MagicMock, patch

        from forge.providers.base import ModelSpec
        from forge.providers.status import ProviderConnectionStatus

        mock_registry = MagicMock()
        mock_registry.validate_model_for_stage.return_value = []

        resolved = {"planner": ModelSpec("claude", "opus")}

        mock_cs = ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=True,
            status="Connected",
            detail="user@example.com",
        )
        with patch(
            "forge.providers.status.collect_provider_connection_statuses",
            return_value={"claude": mock_cs},
        ):
            results = _check_routing_validity(mock_registry, resolved)

        assert results == []

    def test_blocked_model_fails(self):
        """BLOCKED model should produce a failing CheckResult."""
        from unittest.mock import MagicMock, patch

        from forge.providers.base import ModelSpec
        from forge.providers.status import ProviderConnectionStatus

        mock_registry = MagicMock()
        mock_registry.validate_model_for_stage.return_value = [
            "BLOCKED: model 'openai:gpt-99' not found in catalog"
        ]

        resolved = {"agent_high": ModelSpec("openai", "gpt-99")}

        mock_cs = ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=True,
            status="Connected",
            detail="OK",
        )
        with patch(
            "forge.providers.status.collect_provider_connection_statuses",
            return_value={"codex": mock_cs},
        ):
            results = _check_routing_validity(mock_registry, resolved)

        blocked = [r for r in results if "routing_validity" in r.name]
        assert len(blocked) == 1
        assert not blocked[0].passed
        assert "BLOCKED" in blocked[0].message
        assert "agent_high" in blocked[0].message

    def test_disconnected_provider_fails(self):
        """Disconnected provider used by a stage should produce a failing CheckResult."""
        from unittest.mock import MagicMock, patch

        from forge.providers.base import ModelSpec
        from forge.providers.status import ProviderConnectionStatus

        mock_registry = MagicMock()
        mock_registry.validate_model_for_stage.return_value = []

        resolved = {
            "planner": ModelSpec("openai", "gpt-5.4"),
            "reviewer": ModelSpec("openai", "gpt-5.4"),
        }

        mock_cs = ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=False,
            status="Needs login",
            detail="Run `codex login`",
        )
        with patch(
            "forge.providers.status.collect_provider_connection_statuses",
            return_value={"codex": mock_cs},
        ):
            results = _check_routing_validity(mock_registry, resolved)

        provider_checks = [r for r in results if "routing_provider" in r.name]
        assert len(provider_checks) == 1
        assert not provider_checks[0].passed
        assert "not connected" in provider_checks[0].message
        assert "codex login" in provider_checks[0].fix_hint

    def test_uninstalled_provider_fails(self):
        """Uninstalled provider used by a stage should produce a failing CheckResult."""
        from unittest.mock import MagicMock, patch

        from forge.providers.base import ModelSpec
        from forge.providers.status import ProviderConnectionStatus

        mock_registry = MagicMock()
        mock_registry.validate_model_for_stage.return_value = []

        resolved = {"agent_low": ModelSpec("openai", "gpt-5.4-mini")}

        mock_cs = ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=False,
            connected=False,
            status="Not installed",
            detail="Install codex CLI",
        )
        with patch(
            "forge.providers.status.collect_provider_connection_statuses",
            return_value={"codex": mock_cs},
        ):
            results = _check_routing_validity(mock_registry, resolved)

        provider_checks = [r for r in results if "routing_provider" in r.name]
        assert len(provider_checks) == 1
        assert not provider_checks[0].passed
        assert "not installed" in provider_checks[0].message

    def test_non_blocking_warnings_ignored(self):
        """Non-BLOCKED warnings from validate_model_for_stage should not produce failures."""
        from unittest.mock import MagicMock, patch

        from forge.providers.base import ModelSpec
        from forge.providers.status import ProviderConnectionStatus

        mock_registry = MagicMock()
        mock_registry.validate_model_for_stage.return_value = [
            "opus is expensive for reviewer stage"
        ]

        resolved = {"reviewer": ModelSpec("claude", "opus")}

        mock_cs = ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=True,
            status="Connected",
            detail="OK",
        )
        with patch(
            "forge.providers.status.collect_provider_connection_statuses",
            return_value={"claude": mock_cs},
        ):
            results = _check_routing_validity(mock_registry, resolved)

        # No BLOCKED warnings, so no routing_validity failures
        blocked_checks = [r for r in results if "routing_validity" in r.name]
        assert blocked_checks == []
