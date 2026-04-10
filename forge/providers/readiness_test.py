"""Tests for the unified readiness report builder."""

from __future__ import annotations

from unittest.mock import patch

from forge.config.settings import ForgeSettings
from forge.providers.base import (
    CatalogEntry,
    ProviderHealthStatus,
)
from forge.providers.readiness import (
    _STAGE_LABELS,
    ProviderReadinessEntry,
    ReadinessReport,
    RoutingAudit,
    RoutingAuditEntry,
    StageRoutingEntry,
    build_readiness_report,
    build_routing_audit,
    format_routing_audit_rich,
    routing_audit_to_dict,
)
from forge.providers.registry import ProviderRegistry
from forge.providers.status import ProviderConnectionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    provider: str,
    alias: str,
    backend: str = "claude-code-sdk",
    validated_stages: frozenset[str] | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        provider=provider,
        alias=alias,
        canonical_id=f"{provider}-{alias}-v1",
        backend=backend,
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=200_000,
        supports_structured_output=False,
        supports_reasoning=False,
        cost_key=f"{provider}:{alias}",
        validated_stages=validated_stages
        if validated_stages is not None
        else frozenset(["planner", "agent", "reviewer", "contract_builder", "ci_fix"]),
    )


class _FakeProvider:
    def __init__(self, name: str, entries: list[CatalogEntry]) -> None:
        self._name = name
        self._entries = entries

    @property
    def name(self) -> str:
        return self._name

    def catalog_entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        return ProviderHealthStatus(healthy=True, provider=self._name, details="ok")

    def start(self, *a, **kw):
        raise NotImplementedError

    def can_resume(self, state):
        return False

    def cleanup_session(self, state):
        pass


def _connected_status(
    ui_key: str, provider_key: str, display_name: str
) -> ProviderConnectionStatus:
    return ProviderConnectionStatus(
        ui_key=ui_key,
        provider_key=provider_key,
        display_name=display_name,
        installed=True,
        connected=True,
        status="Connected",
        detail="Logged in",
        auth_source="claude.ai" if provider_key == "claude" else "codex",
    )


def _disconnected_status(
    ui_key: str, provider_key: str, display_name: str
) -> ProviderConnectionStatus:
    return ProviderConnectionStatus(
        ui_key=ui_key,
        provider_key=provider_key,
        display_name=display_name,
        installed=True,
        connected=False,
        status="Needs login",
        detail="Run login command",
        auth_source=None,
    )


def _not_installed_status(
    ui_key: str, provider_key: str, display_name: str
) -> ProviderConnectionStatus:
    return ProviderConnectionStatus(
        ui_key=ui_key,
        provider_key=provider_key,
        display_name=display_name,
        installed=False,
        connected=False,
        status="Not installed",
        detail="CLI not found",
        auth_source=None,
    )


def _build_registry_and_settings(
    provider_name: str = "claude",
    model_alias: str = "opus",
    backend: str = "claude-code-sdk",
    validated_stages: frozenset[str] | None = None,
) -> tuple[ForgeSettings, ProviderRegistry]:
    """Build a minimal settings + registry for testing."""
    settings = ForgeSettings()
    # Set all stages to the same model for simplicity
    spec_str = f"{provider_name}:{model_alias}"
    settings.planner_model = spec_str
    settings.agent_model_low = spec_str
    settings.agent_model_medium = spec_str
    settings.agent_model_high = spec_str
    settings.reviewer_model = spec_str
    settings.contract_builder_model = spec_str
    settings.ci_fix_model = spec_str

    entry = _make_entry(provider_name, model_alias, backend, validated_stages)
    provider = _FakeProvider(provider_name, [entry])
    registry = ProviderRegistry(settings)
    registry.register(provider)
    return settings, registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_both_providers_connected_all_ready():
    """When both providers are connected and models are valid, ready=True."""
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
        "codex": _connected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    assert isinstance(report, ReadinessReport)
    assert report.ready is True
    assert report.blocking_issues == []
    assert len(report.providers) == 2
    assert len(report.routing) == 7  # 7 stages in _ROUTING_PLAN
    assert all(pe.connected for pe in report.providers)


def test_provider_disconnected_but_used_blocks_readiness():
    """A disconnected provider used by routing creates a blocking issue."""
    settings, registry = _build_registry_and_settings()

    # Claude is used but disconnected
    statuses = {
        "claude": _disconnected_status("claude", "claude", "Claude"),
        "codex": _connected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    assert report.ready is False
    assert len(report.blocking_issues) > 0
    assert any("not connected" in issue for issue in report.blocking_issues)


def test_provider_not_installed_but_used_blocks_readiness():
    """A not-installed provider used by routing creates a blocking issue."""
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _not_installed_status("claude", "claude", "Claude"),
        "codex": _connected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    assert report.ready is False
    assert any("not installed" in issue for issue in report.blocking_issues)


def test_model_blocked_for_stage_creates_blocking_issue():
    """A model that is BLOCKED for a stage shows up in blocking_issues."""
    # Model has no validated stages -> validate_model_for_stage returns warnings
    # but we need a model that returns BLOCKED
    settings = ForgeSettings()
    spec_str = "claude:opus"
    settings.planner_model = spec_str
    settings.agent_model_low = spec_str
    settings.agent_model_medium = spec_str
    settings.agent_model_high = spec_str
    settings.reviewer_model = spec_str
    settings.contract_builder_model = spec_str
    settings.ci_fix_model = spec_str

    # Entry lacks can_run_shell which is required for agent stage
    entry = CatalogEntry(
        provider="claude",
        alias="opus",
        canonical_id="claude-opus-v1",
        backend="claude-code-sdk",
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=False,  # Missing capability
        can_edit_files=False,  # Missing capability
        supports_mcp_servers=False,
        max_context_tokens=200_000,
        supports_structured_output=False,
        supports_reasoning=False,
        cost_key="claude:opus",
        validated_stages=frozenset(["planner", "agent", "reviewer", "contract_builder", "ci_fix"]),
    )
    provider = _FakeProvider("claude", [entry])
    registry = ProviderRegistry(settings)
    registry.register(provider)

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
        "codex": _connected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    # Should have BLOCKED warnings for stages requiring can_run_shell
    blocked = [i for i in report.blocking_issues if i.startswith("BLOCKED:")]
    assert len(blocked) > 0
    assert report.ready is False


def test_no_blocking_issues_means_ready():
    """No blocking issues anywhere means report.ready is True."""
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
        "codex": _connected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    assert report.ready is True
    assert report.blocking_issues == []


def test_stage_labels_populated():
    """All routing entries have the correct human-readable labels."""
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    for entry in report.routing:
        assert entry.label == _STAGE_LABELS[entry.stage]


def test_routing_entries_have_correct_spec_format():
    """Each routing entry's spec matches 'provider:model'."""
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    for entry in report.routing:
        assert entry.spec == f"{entry.provider}:{entry.model}"


def test_provider_entry_reflects_connection_status():
    """Provider entries mirror the connection status fields."""
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
        "codex": _disconnected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    by_key = {pe.ui_key: pe for pe in report.providers}
    assert by_key["claude"].connected is True
    assert by_key["claude"].auth_source == "claude.ai"
    assert by_key["codex"].connected is False
    assert by_key["codex"].auth_source is None


def test_warnings_exclude_blocked_items():
    """report.warnings contains only non-BLOCKED items from stage validation."""
    # Use a model with no validated stages -> gets WARNING but not BLOCKED
    settings, registry = _build_registry_and_settings(
        validated_stages=frozenset(),
    )

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    # Should have warnings (unvalidated stages) but may still be ready
    # if no BLOCKED items
    for w in report.warnings:
        assert not w.startswith("BLOCKED:")
    # Warnings should include the unvalidated stage warnings
    assert len(report.warnings) > 0
    assert any("WARNING:" in w for w in report.warnings)


def test_reasoning_effort_populated():
    """Reasoning effort from settings is reflected in routing entries."""
    settings, registry = _build_registry_and_settings()
    settings.planner_reasoning_effort = "high"

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    planner_entry = next(e for e in report.routing if e.stage == "planner")
    assert planner_entry.reasoning_effort == "high"


def test_disconnected_provider_not_used_does_not_block():
    """A disconnected provider that is NOT used by any stage doesn't block."""
    # All stages use claude, codex is disconnected but unused
    settings, registry = _build_registry_and_settings()

    statuses = {
        "claude": _connected_status("claude", "claude", "Claude"),
        "codex": _disconnected_status("codex", "openai", "Codex"),
    }

    with patch(
        "forge.providers.readiness.collect_provider_connection_statuses",
        return_value=statuses,
    ):
        report = build_readiness_report(settings, registry)

    # openai not used by any stage, so no blocking issue for it
    assert report.ready is True
    assert not any("openai" in issue for issue in report.blocking_issues)


def test_dataclasses_are_frozen():
    """Verify dataclasses are immutable."""
    entry = ProviderReadinessEntry(
        ui_key="claude",
        provider_key="claude",
        display_name="Claude",
        installed=True,
        connected=True,
        status="Connected",
        detail="ok",
    )
    try:
        entry.installed = False  # type: ignore[misc]
        raise AssertionError("Should not reach here")
    except AttributeError:
        pass

    routing = StageRoutingEntry(
        stage="planner",
        label="Planner",
        provider="claude",
        model="opus",
        spec="claude:opus",
        backend="claude-code-sdk",
    )
    try:
        routing.provider = "openai"  # type: ignore[misc]
        raise AssertionError("Should not reach here")
    except AttributeError:
        pass


class TestRoutingAudit:
    def test_all_claude_no_mismatches(self):
        settings, registry = _build_registry_and_settings()
        resolved = {
            "planner": _make_entry("claude", "opus").spec,
            "agent_low": _make_entry("claude", "opus").spec,
            "agent_medium": _make_entry("claude", "opus").spec,
            "agent_high": _make_entry("claude", "opus").spec,
            "reviewer": _make_entry("claude", "opus").spec,
            "contract_builder": _make_entry("claude", "opus").spec,
            "ci_fix": _make_entry("claude", "opus").spec,
        }

        audit = build_routing_audit(resolved, registry)

        assert isinstance(audit, RoutingAudit)
        assert audit.has_mismatches is False
        assert audit.mismatch_count == 0
        assert len(audit.entries) == 7
        assert all(entry.expected_provider == "claude" for entry in audit.entries)
        assert all(entry.actual_provider == "claude" for entry in audit.entries)
        assert all(entry.backend == "claude-code-sdk" for entry in audit.entries)
        assert all(entry.mismatch is False for entry in audit.entries)
        assert audit.summary == (
            "Planner: Claude | Contracts: Claude | Agent L: Claude | "
            "Agent M/H: Claude | Reviewer: Claude"
        )

    def test_mixed_codex_planner_claude_agents(self):
        settings = ForgeSettings()
        registry = ProviderRegistry(settings)
        for entry in [
            _make_entry("claude", "opus", "claude-code-sdk"),
            _make_entry("claude", "sonnet", "claude-code-sdk"),
            _make_entry("openai", "gpt-5.3-codex", "codex-sdk"),
        ]:
            registry.register_catalog_entry(entry)

        resolved = {
            "planner": _make_entry("openai", "gpt-5.3-codex", "codex-sdk").spec,
            "agent_low": _make_entry("openai", "gpt-5.3-codex", "codex-sdk").spec,
            "agent_medium": _make_entry("claude", "sonnet", "claude-code-sdk").spec,
            "agent_high": _make_entry("claude", "opus", "claude-code-sdk").spec,
            "reviewer": _make_entry("openai", "gpt-5.3-codex", "codex-sdk").spec,
            "contract_builder": _make_entry("claude", "opus", "claude-code-sdk").spec,
            "ci_fix": _make_entry("claude", "sonnet", "claude-code-sdk").spec,
        }

        audit = build_routing_audit(resolved, registry)
        by_stage = {entry.stage: entry for entry in audit.entries}

        assert audit.has_mismatches is True
        assert audit.mismatch_count == 3
        assert by_stage["planner"].actual_provider == "openai"
        assert by_stage["planner"].backend == "codex-sdk"
        assert by_stage["planner"].mismatch is True
        assert by_stage["planner"].mismatch_detail == "Expected claude (dominant), got openai"
        assert by_stage["agent_low"].actual_provider == "openai"
        assert by_stage["agent_low"].mismatch is True
        assert by_stage["agent_medium"].actual_provider == "claude"
        assert by_stage["agent_medium"].mismatch is False
        assert by_stage["agent_high"].actual_provider == "claude"
        assert by_stage["agent_high"].actual_model == "opus"
        assert by_stage["reviewer"].actual_provider == "openai"
        assert by_stage["reviewer"].mismatch is True
        assert by_stage["contract_builder"].actual_provider == "claude"
        assert by_stage["contract_builder"].mismatch is False
        assert by_stage["ci_fix"].actual_provider == "claude"
        assert by_stage["ci_fix"].mismatch is False

    def test_audit_summary_format(self):
        settings = ForgeSettings()
        registry = ProviderRegistry(settings)
        for entry in [
            _make_entry("claude", "opus", "claude-code-sdk"),
            _make_entry("openai", "gpt-5.3-codex", "codex-sdk"),
        ]:
            registry.register_catalog_entry(entry)

        resolved = {
            "planner": _make_entry("openai", "gpt-5.3-codex", "codex-sdk").spec,
            "agent_low": _make_entry("openai", "gpt-5.3-codex", "codex-sdk").spec,
            "agent_medium": _make_entry("claude", "opus", "claude-code-sdk").spec,
            "agent_high": _make_entry("claude", "opus", "claude-code-sdk").spec,
            "reviewer": _make_entry("openai", "gpt-5.3-codex", "codex-sdk").spec,
            "contract_builder": _make_entry("claude", "opus", "claude-code-sdk").spec,
            "ci_fix": _make_entry("claude", "opus", "claude-code-sdk").spec,
        }

        audit = build_routing_audit(resolved, registry)
        rich = format_routing_audit_rich(audit)

        assert audit.summary == (
            "Planner: Codex | Contracts: Claude | Agent L: Codex | "
            "Agent M/H: Claude | Reviewer: Codex"
        )
        assert "Planner: Codex" in audit.summary
        assert "Contracts: Claude" in audit.summary
        assert "Agent L: Codex" in audit.summary
        assert "Agent M/H: Claude" in audit.summary
        assert "Reviewer: Codex" in audit.summary
        assert "[#58a6ff]Codex[/] planner [yellow]![/]" in rich
        assert "[#22c55e]Claude[/] contracts" in rich

    def test_audit_to_dict_roundtrip(self):
        settings, registry = _build_registry_and_settings()
        resolved = {
            "planner": _make_entry("claude", "opus").spec,
            "agent_low": _make_entry("claude", "opus").spec,
            "agent_medium": _make_entry("claude", "opus").spec,
            "agent_high": _make_entry("claude", "opus").spec,
            "reviewer": _make_entry("claude", "opus").spec,
            "contract_builder": _make_entry("claude", "opus").spec,
            "ci_fix": _make_entry("claude", "opus").spec,
        }

        audit = build_routing_audit(resolved, registry)
        payload = routing_audit_to_dict(audit)

        assert payload["has_mismatches"] is False
        assert payload["mismatch_count"] == 0
        assert payload["summary"] == audit.summary
        entries = payload["entries"]
        assert isinstance(entries, list)
        assert entries[0] == {
            "stage": "planner",
            "label": "Planner",
            "expected_provider": "claude",
            "actual_provider": "claude",
            "actual_model": "opus",
            "actual_spec": "claude:opus",
            "backend": "claude-code-sdk",
            "mismatch": False,
            "mismatch_detail": None,
        }
        assert all(isinstance(entry, dict) for entry in entries)
        assert len(entries) == len(audit.entries)
        assert isinstance(audit.entries[0], RoutingAuditEntry)
