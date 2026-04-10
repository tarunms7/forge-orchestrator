"""Tests for forge readiness CLI command."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from forge.cli.readiness import readiness
from forge.providers.readiness import (
    ProviderReadinessEntry,
    ReadinessReport,
    StageRoutingEntry,
)


@pytest.fixture()
def runner():
    return CliRunner()


def _make_report(
    *, ready: bool = True, blocking: list[str] | None = None, warnings: list[str] | None = None
) -> ReadinessReport:
    """Build a ReadinessReport for testing."""
    providers = [
        ProviderReadinessEntry(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=True,
            auth_source="claude.ai",
            status="Connected",
            detail="Authenticated via claude.ai",
            blocking_issues=[],
        ),
        ProviderReadinessEntry(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=False,
            connected=False,
            auth_source=None,
            status="Not installed",
            detail="Install codex CLI",
            blocking_issues=[] if ready else ["Provider openai is not installed"],
        ),
    ]
    routing = [
        StageRoutingEntry(
            stage="planner",
            label="Planner",
            provider="claude",
            model="opus",
            spec="claude:opus",
            backend="claude-code-sdk",
            reasoning_effort="high",
            warnings=[],
        ),
        StageRoutingEntry(
            stage="agent_medium",
            label="Agent Medium",
            provider="claude",
            model="sonnet",
            spec="claude:sonnet",
            backend="claude-code-sdk",
            reasoning_effort=None,
            warnings=[],
        ),
    ]
    return ReadinessReport(
        providers=providers,
        routing=routing,
        blocking_issues=blocking or [],
        warnings=warnings or [],
        ready=ready,
    )


def _mock_readiness(report: ReadinessReport):
    """Context manager that mocks all readiness deps, only patching build_readiness_report."""
    stack = ExitStack()
    mock_settings = MagicMock()
    mock_registry = MagicMock()

    stack.enter_context(
        patch("forge.config.project_config.ProjectConfig.load", return_value=MagicMock())
    )
    stack.enter_context(patch("forge.config.settings.ForgeSettings", return_value=mock_settings))
    stack.enter_context(patch("forge.config.project_config.apply_project_config"))
    stack.enter_context(
        patch("forge.config.user_settings.load_local_user_settings", return_value={})
    )
    stack.enter_context(patch("forge.core.provider_config.apply_user_settings"))
    stack.enter_context(
        patch("forge.core.provider_config.build_provider_registry", return_value=mock_registry)
    )
    stack.enter_context(patch("forge.core.provider_config.ensure_routing_defaults"))
    stack.enter_context(patch("forge.core.provider_config.normalize_routing_settings"))
    stack.enter_context(
        patch("forge.providers.status.collect_provider_connection_statuses", return_value={})
    )
    stack.enter_context(
        patch("forge.providers.status.preferred_default_provider", return_value="claude")
    )
    stack.enter_context(
        patch("forge.providers.readiness.build_readiness_report", return_value=report)
    )
    return stack


# ── Ready state ──────────────────────────────────────────────────────


def test_ready_exit_zero(runner):
    """Exit code 0 when pipeline is ready."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert result.exit_code == 0


def test_ready_shows_providers(runner):
    """Output includes provider names."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Claude" in result.output
    assert "Codex" in result.output


def test_ready_shows_routing(runner):
    """Output includes stage routing info."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Planner" in result.output
    assert "opus" in result.output
    assert "sonnet" in result.output
    assert "claude-code-sdk" in result.output


def test_ready_shows_ready_message(runner):
    """Output shows ready message when no blocking issues."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Ready to run pipelines" in result.output


def test_ready_shows_effort(runner):
    """Output shows reasoning effort for stages that have it."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "high" in result.output
    assert "auto" in result.output


# ── Not ready state ──────────────────────────────────────────────────


def test_not_ready_exit_one(runner):
    """Exit code 1 when blocking issues exist."""
    report = _make_report(
        ready=False,
        blocking=["Provider openai is not installed but used by stage agent_high"],
    )
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert result.exit_code == 1


def test_not_ready_shows_blocking_issues(runner):
    """Output lists blocking issues."""
    report = _make_report(
        ready=False,
        blocking=["Provider openai is not connected but used by stage agent_high"],
    )
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Blocking issues" in result.output
    assert "openai" in result.output
    assert "agent_high" in result.output


def test_not_ready_does_not_show_ready_message(runner):
    """Output does not show ready message when blocking issues exist."""
    report = _make_report(
        ready=False,
        blocking=["Provider openai is not installed"],
    )
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Ready to run pipelines" not in result.output


# ── Warnings ─────────────────────────────────────────────────────────


def test_warnings_shown(runner):
    """Non-blocking warnings are displayed."""
    report = _make_report(
        ready=True,
        warnings=["Model claude:haiku not validated for stage planner"],
    )
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Warnings" in result.output
    assert "haiku" in result.output
    assert result.exit_code == 0


def test_no_warnings_section_when_empty(runner):
    """Warnings section is omitted when there are no warnings."""
    report = _make_report(ready=True, warnings=[])
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Warnings:" not in result.output


# ── Provider table columns ───────────────────────────────────────────


def test_provider_table_shows_auth_source(runner):
    """Auth source column displays for connected providers."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "claude.ai" in result.output


def test_provider_table_shows_install_status(runner):
    """Installed/connected columns render Yes/No."""
    report = _make_report(ready=True)
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Yes" in result.output
    assert "No" in result.output


# ── Stage routing warnings ───────────────────────────────────────────


def test_routing_warnings_in_table(runner):
    """Stage-level warnings appear in the routing table."""
    report = ReadinessReport(
        providers=[],
        routing=[
            StageRoutingEntry(
                stage="planner",
                label="Planner",
                provider="claude",
                model="haiku",
                spec="claude:haiku",
                backend="claude-code-sdk",
                reasoning_effort=None,
                warnings=["Not validated for stage planner"],
            ),
        ],
        blocking_issues=[],
        warnings=[],
        ready=True,
    )
    with _mock_readiness(report):
        result = runner.invoke(readiness)
    assert "Not validated" in result.output
