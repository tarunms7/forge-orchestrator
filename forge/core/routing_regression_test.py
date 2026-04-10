"""Mixed-routing regression tests.

Proves that a pipeline with mixed Codex/Claude routing correctly assigns
providers to each stage, that escalation stays within provider boundaries,
and that snapshot roundtrips preserve routing.
"""

from __future__ import annotations

from forge.config.settings import ForgeSettings
from forge.core.provider_config import (
    apply_provider_config_snapshot,
    build_provider_config_snapshot,
    resolve_model_for_stage,
    resolve_pipeline_models,
)
from forge.providers.base import CatalogEntry, ProviderHealthStatus
from forge.providers.readiness import build_routing_audit
from forge.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_STAGES = frozenset(["planner", "agent", "reviewer", "contract_builder", "ci_fix"])


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
        validated_stages=validated_stages if validated_stages is not None else ALL_STAGES,
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


def _build_mixed_routing_settings() -> tuple[ForgeSettings, ProviderRegistry]:
    """Configure mixed Codex/Claude routing with both providers registered.

    Layout:
      - Codex: planner, agent_low, reviewer, ci_fix  (4 stages)
      - Claude: contract_builder, agent_medium, agent_high  (3 stages)
    """
    settings = ForgeSettings()

    # Codex stages
    settings.planner_model = "openai:gpt-5.3-codex"
    settings.agent_model_low = "openai:gpt-5.3-codex"
    settings.reviewer_model = "openai:gpt-5.3-codex"
    settings.ci_fix_model = "openai:gpt-5.3-codex"

    # Claude stages
    settings.contract_builder_model = "claude:opus"
    settings.agent_model_medium = "claude:sonnet"
    settings.agent_model_high = "claude:opus"

    # Register Claude models
    claude_provider = _FakeProvider(
        "claude",
        [
            _make_entry("claude", "opus", "claude-code-sdk"),
            _make_entry("claude", "sonnet", "claude-code-sdk"),
            _make_entry("claude", "haiku", "claude-code-sdk"),
        ],
    )

    # Register OpenAI models
    openai_provider = _FakeProvider(
        "openai",
        [
            _make_entry("openai", "gpt-5.4", "codex-sdk"),
            _make_entry("openai", "gpt-5.4-mini", "codex-sdk"),
            _make_entry("openai", "gpt-5.3-codex", "codex-sdk"),
        ],
    )

    registry = ProviderRegistry(settings)
    registry.register(claude_provider)
    registry.register(openai_provider)

    return settings, registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMixedRoutingRegression:
    """Regression suite proving mixed Codex/Claude routing works end-to-end."""

    def test_planner_uses_codex(self):
        settings, registry = _build_mixed_routing_settings()
        spec = resolve_model_for_stage(settings, registry, "planner", "high")
        assert spec.provider == "openai"
        assert spec.model == "gpt-5.3-codex"

    def test_contract_builder_uses_claude(self):
        settings, registry = _build_mixed_routing_settings()
        spec = resolve_model_for_stage(settings, registry, "contract_builder", "high")
        assert spec.provider == "claude"
        assert spec.model == "opus"

    def test_agent_low_uses_codex(self):
        settings, registry = _build_mixed_routing_settings()
        spec = resolve_model_for_stage(settings, registry, "agent", "low")
        assert spec.provider == "openai"

    def test_agent_medium_uses_claude(self):
        settings, registry = _build_mixed_routing_settings()
        spec = resolve_model_for_stage(settings, registry, "agent", "medium")
        assert spec.provider == "claude"
        assert spec.model == "sonnet"

    def test_agent_high_uses_claude(self):
        settings, registry = _build_mixed_routing_settings()
        spec = resolve_model_for_stage(settings, registry, "agent", "high")
        assert spec.provider == "claude"
        assert spec.model == "opus"

    def test_reviewer_uses_codex(self):
        settings, registry = _build_mixed_routing_settings()
        spec = resolve_model_for_stage(settings, registry, "reviewer", "medium")
        assert spec.provider == "openai"

    def test_full_pipeline_snapshot_mixed(self):
        """build_provider_config_snapshot captures the correct provider for every stage."""
        settings, registry = _build_mixed_routing_settings()
        snapshot = build_provider_config_snapshot(settings, registry)

        stages = snapshot["stages"]
        assert stages["planner"]["provider"] == "openai"
        assert stages["planner"]["model"] == "gpt-5.3-codex"
        assert stages["planner"]["backend"] == "codex-sdk"

        assert stages["contract_builder"]["provider"] == "claude"
        assert stages["contract_builder"]["model"] == "opus"
        assert stages["contract_builder"]["backend"] == "claude-code-sdk"

        assert stages["agent_low"]["provider"] == "openai"
        assert stages["agent_medium"]["provider"] == "claude"
        assert stages["agent_medium"]["model"] == "sonnet"
        assert stages["agent_high"]["provider"] == "claude"
        assert stages["agent_high"]["model"] == "opus"

        assert stages["reviewer"]["provider"] == "openai"
        assert stages["ci_fix"]["provider"] == "openai"

    def test_routing_audit_flags_mixed(self):
        """Routing audit correctly reports dominant-provider mismatches.

        With 4 openai stages vs 3 claude stages, openai is the dominant
        provider.  The 3 claude stages are flagged as mismatches because
        they differ from the dominant provider.
        """
        settings, registry = _build_mixed_routing_settings()
        resolved = resolve_pipeline_models(settings, registry)
        audit = build_routing_audit(resolved, registry)

        # Dominant provider is openai (4 stages) — claude stages are mismatches
        assert audit.has_mismatches is True
        assert audit.mismatch_count == 3

        by_stage = {entry.stage: entry for entry in audit.entries}

        # Codex stages match dominant → no mismatch
        assert by_stage["planner"].actual_provider == "openai"
        assert by_stage["planner"].mismatch is False
        assert by_stage["agent_low"].actual_provider == "openai"
        assert by_stage["agent_low"].mismatch is False
        assert by_stage["reviewer"].actual_provider == "openai"
        assert by_stage["reviewer"].mismatch is False
        assert by_stage["ci_fix"].actual_provider == "openai"
        assert by_stage["ci_fix"].mismatch is False

        # Claude stages differ from dominant → mismatch
        assert by_stage["contract_builder"].actual_provider == "claude"
        assert by_stage["contract_builder"].mismatch is True
        assert by_stage["agent_medium"].actual_provider == "claude"
        assert by_stage["agent_medium"].mismatch is True
        assert by_stage["agent_high"].actual_provider == "claude"
        assert by_stage["agent_high"].mismatch is True

        # Summary contains both provider display names
        assert "Claude" in audit.summary
        assert "Codex" in audit.summary

    def test_escalation_preserves_provider(self):
        """On retry >= 2, agent models escalate within the same provider.

        Codex: gpt-5.3-codex → gpt-5.4-mini  (stays openai)
        Claude: sonnet → opus                  (stays claude)
        """
        settings, registry = _build_mixed_routing_settings()

        # Low-complexity agent (Codex) escalates within openai
        spec_low = resolve_model_for_stage(
            settings, registry, "agent", "low", retry_count=2
        )
        assert spec_low.provider == "openai"
        assert spec_low.model == "gpt-5.4-mini"

        # Medium-complexity agent (Claude) escalates within claude
        spec_med = resolve_model_for_stage(
            settings, registry, "agent", "medium", retry_count=2
        )
        assert spec_med.provider == "claude"
        assert spec_med.model == "opus"

        # High-complexity agent (Claude opus) has no further escalation
        spec_high = resolve_model_for_stage(
            settings, registry, "agent", "high", retry_count=2
        )
        assert spec_high.provider == "claude"
        assert spec_high.model == "opus"

    def test_snapshot_roundtrip_preserves_routing(self):
        """Build snapshot → apply to fresh settings → re-resolve → same models."""
        settings, registry = _build_mixed_routing_settings()

        # Capture original resolution
        original = resolve_pipeline_models(settings, registry)

        # Build and apply snapshot to fresh settings
        snapshot = build_provider_config_snapshot(settings, registry)
        fresh_settings = ForgeSettings()
        apply_provider_config_snapshot(fresh_settings, snapshot)

        # Re-resolve with the restored settings
        restored = resolve_pipeline_models(fresh_settings, registry)

        for stage_key in original:
            assert restored[stage_key] == original[stage_key], (
                f"Stage {stage_key}: expected {original[stage_key]}, "
                f"got {restored[stage_key]}"
            )
