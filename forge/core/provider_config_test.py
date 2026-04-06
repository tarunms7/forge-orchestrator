"""Tests for provider-aware settings and registry helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from forge.config.project_config import CustomModelConfig, ProjectConfig
from forge.config.settings import ForgeSettings
from forge.core.provider_config import (
    apply_provider_config_snapshot,
    build_provider_config_snapshot,
    build_provider_registry,
    resolve_registry_model,
)
from forge.providers.base import ModelSpec


def test_apply_provider_config_snapshot_ignores_non_json_objects() -> None:
    """Unexpected provider_config objects should be ignored safely."""
    settings = ForgeSettings()

    apply_provider_config_snapshot(settings, MagicMock())

    assert settings.planner_model is None
    assert settings.agent_model_medium is None


def test_build_provider_registry_includes_valid_custom_model() -> None:
    """Project custom models should become executable registry entries."""
    settings = ForgeSettings()
    project_config = ProjectConfig(
        custom_models=[
            CustomModelConfig(
                alias="sonnet-plus",
                provider="claude",
                canonical_id="claude-sonnet-plus-20260401",
                backend="claude-code-sdk",
            )
        ]
    )

    registry = build_provider_registry(settings, project_config)
    entry = registry.get_catalog_entry(ModelSpec.parse("claude:sonnet-plus"))

    assert entry.canonical_id == "claude-sonnet-plus-20260401"
    assert entry.backend == "claude-code-sdk"
    assert entry.tier == "experimental"


def test_build_provider_registry_skips_unregistered_custom_provider() -> None:
    """Custom models for providers that are not registered should be skipped."""
    settings = ForgeSettings()
    project_config = ProjectConfig(
        custom_models=[
            CustomModelConfig(
                alias="my-model",
                provider="custom",
                canonical_id="custom/my-model",
                backend="codex-sdk",
            )
        ]
    )

    registry = build_provider_registry(settings, project_config)

    assert not registry.validate_model(ModelSpec.parse("custom:my-model"))


def test_build_provider_registry_auto_registers_openai_for_routed_stage() -> None:
    """OpenAI should be registered when stage routing references an OpenAI model."""
    settings = ForgeSettings(reviewer_model="openai:gpt-5.4")

    registry = build_provider_registry(settings)

    assert registry.get_provider("openai").name == "openai"


def test_build_provider_config_snapshot_records_stage_metadata() -> None:
    """Pipeline snapshots should persist provider, backend, and canonical_id."""
    settings = ForgeSettings()
    registry = build_provider_registry(settings)

    snapshot = build_provider_config_snapshot(settings, registry)
    planner = snapshot["stages"]["planner"]

    assert planner["provider"] == "claude"
    assert planner["spec"] == "claude:opus"
    assert planner["backend"] == "claude-code-sdk"
    assert planner["canonical_id"]


def test_build_provider_config_snapshot_records_reasoning_effort() -> None:
    """Explicit reasoning-effort overrides should persist in the pipeline snapshot."""
    settings = ForgeSettings(
        openai_enabled=True,
        reviewer_model="openai:gpt-5.4",
        reviewer_reasoning_effort="high",
    )
    registry = build_provider_registry(settings)

    snapshot = build_provider_config_snapshot(settings, registry)

    assert snapshot["stages"]["reviewer"]["reasoning_effort"] == "high"


def test_apply_provider_config_snapshot_restores_reasoning_effort() -> None:
    """Persisted reasoning effort should be replayed onto fresh settings."""
    settings = ForgeSettings()

    apply_provider_config_snapshot(
        settings,
        {
            "stages": {
                "reviewer": {
                    "spec": "openai:gpt-5.4",
                    "reasoning_effort": "high",
                }
            }
        },
    )

    assert settings.reviewer_model == "openai:gpt-5.4"
    assert settings.reviewer_reasoning_effort == "high"


def test_resolve_registry_model_uses_registry_settings_overrides() -> None:
    """Registry-backed helper selection should honor per-stage overrides."""
    settings = ForgeSettings(
        openai_enabled=True,
        reviewer_model="openai:gpt-5.4-mini",
    )
    registry = build_provider_registry(settings)

    result = resolve_registry_model(registry, "reviewer", "low")

    assert result == ModelSpec("openai", "gpt-5.4-mini")


def test_build_provider_config_snapshot_mixed_provider_routing() -> None:
    """Mixed-provider routing should record correct specs and providers in snapshot."""
    settings = ForgeSettings(
        planner_model="claude:opus",
        agent_model_medium="claude:sonnet",
        reviewer_model="openai:gpt-5.4",
        reviewer_reasoning_effort="high",
    )
    registry = build_provider_registry(settings)

    snapshot = build_provider_config_snapshot(settings, registry)

    # Assert planner stage
    planner = snapshot["stages"]["planner"]
    assert planner["spec"] == "claude:opus"
    assert planner["provider"] == "claude"

    # Assert agent_medium stage
    agent_medium = snapshot["stages"]["agent_medium"]
    assert agent_medium["spec"] == "claude:sonnet"
    assert agent_medium["provider"] == "claude"

    # Assert reviewer stage
    reviewer = snapshot["stages"]["reviewer"]
    assert reviewer["spec"] == "openai:gpt-5.4"
    assert reviewer["provider"] == "openai"
    assert reviewer["reasoning_effort"] == "high"


def test_mixed_provider_settings_roundtrip() -> None:
    """Create ForgeSettings with mixed providers, build snapshot, apply to fresh settings, assert match."""
    # Create original settings with mixed providers
    original_settings = ForgeSettings(
        planner_model="claude:opus",
        reviewer_model="openai:gpt-5.4",
        agent_model_medium="claude:sonnet",
        openai_enabled=True,
        reviewer_reasoning_effort="high",
    )

    # Build registry and snapshot
    registry = build_provider_registry(original_settings)
    snapshot = build_provider_config_snapshot(original_settings, registry)

    # Apply snapshot to fresh settings
    fresh_settings = ForgeSettings()
    apply_provider_config_snapshot(fresh_settings, snapshot)

    # Assert fresh settings match original per-stage models and reasoning effort
    assert fresh_settings.planner_model == "claude:opus"
    assert fresh_settings.reviewer_model == "openai:gpt-5.4"
    assert fresh_settings.agent_model_medium == "claude:sonnet"
    assert fresh_settings.reviewer_reasoning_effort == "high"


def test_provider_config_snapshot_preservation() -> None:
    """Build snapshot, serialize to JSON, deserialize, apply to fresh settings, assert all 7 stages round-trip."""
    import json

    # Create settings with all 7 stage models set
    original_settings = ForgeSettings(
        planner_model="claude:opus",
        agent_model_low="claude:haiku",
        agent_model_medium="claude:sonnet",
        agent_model_high="claude:opus",
        reviewer_model="openai:gpt-5.4",
        contract_builder_model="claude:opus",
        ci_fix_model="claude:sonnet",
        openai_enabled=True,
        planner_reasoning_effort="high",
        reviewer_reasoning_effort="medium",
    )

    # Build registry and snapshot
    registry = build_provider_registry(original_settings)
    snapshot = build_provider_config_snapshot(original_settings, registry)

    # Serialize to JSON string and deserialize
    json_string = json.dumps(snapshot)
    deserialized_snapshot = json.loads(json_string)

    # Apply to fresh settings
    fresh_settings = ForgeSettings()
    apply_provider_config_snapshot(fresh_settings, deserialized_snapshot)

    # Assert all 7 stage specs round-trip correctly
    assert fresh_settings.planner_model == "claude:opus"
    assert fresh_settings.agent_model_low == "claude:haiku"
    assert fresh_settings.agent_model_medium == "claude:sonnet"
    assert fresh_settings.agent_model_high == "claude:opus"
    assert fresh_settings.reviewer_model == "openai:gpt-5.4"
    assert fresh_settings.contract_builder_model == "claude:opus"
    assert fresh_settings.ci_fix_model == "claude:sonnet"
    assert fresh_settings.planner_reasoning_effort == "high"
    assert fresh_settings.reviewer_reasoning_effort == "medium"


def test_apply_provider_config_snapshot_from_json_string() -> None:
    """Pass a JSON string (not dict) to apply_provider_config_snapshot and verify it parses and applies correctly."""
    import json

    # Create JSON string directly
    json_config = json.dumps(
        {
            "model_strategy": "quality",
            "stages": {
                "planner": {
                    "spec": "claude:opus",
                    "provider": "claude",
                    "model": "opus",
                    "reasoning_effort": "high",
                },
                "reviewer": {
                    "spec": "openai:gpt-5.4",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "reasoning_effort": "medium",
                },
            }
        }
    )

    # Apply JSON string to fresh settings
    settings = ForgeSettings()
    apply_provider_config_snapshot(settings, json_config)

    # Verify it parses and applies correctly
    assert settings.model_strategy == "quality"
    assert settings.planner_model == "claude:opus"
    assert settings.reviewer_model == "openai:gpt-5.4"
    assert settings.planner_reasoning_effort == "high"
    assert settings.reviewer_reasoning_effort == "medium"


def test_apply_provider_config_snapshot_ignores_empty_string() -> None:
    """Pass empty string to apply_provider_config_snapshot and verify no settings change."""
    # Create settings with some initial values
    settings = ForgeSettings(
        planner_model="claude:sonnet",
        reviewer_model="claude:haiku",
        model_strategy="fast",
    )

    # Store original values
    original_planner = settings.planner_model
    original_reviewer = settings.reviewer_model
    original_strategy = settings.model_strategy

    # Apply empty string
    apply_provider_config_snapshot(settings, "")

    # Verify no settings changed
    assert settings.planner_model == original_planner
    assert settings.reviewer_model == original_reviewer
    assert settings.model_strategy == original_strategy


def test_apply_provider_config_snapshot_ignores_invalid_json() -> None:
    """Pass malformed JSON string to apply_provider_config_snapshot and verify no crash and no settings change."""
    # Create settings with some initial values
    settings = ForgeSettings(
        planner_model="claude:sonnet",
        reviewer_model="claude:haiku",
        model_strategy="fast",
    )

    # Store original values
    original_planner = settings.planner_model
    original_reviewer = settings.reviewer_model
    original_strategy = settings.model_strategy

    # Apply malformed JSON (should not crash and should not change settings)
    malformed_json = '{"invalid": "json", "missing": bracket'
    apply_provider_config_snapshot(settings, malformed_json)

    # Verify no settings changed
    assert settings.planner_model == original_planner
    assert settings.reviewer_model == original_reviewer
    assert settings.model_strategy == original_strategy


def test_build_provider_config_snapshot_all_stages_present() -> None:
    """Build snapshot with default settings and assert all 7 expected stage keys exist in snapshot['stages']."""
    # Create default settings
    settings = ForgeSettings()
    registry = build_provider_registry(settings)

    # Build snapshot
    snapshot = build_provider_config_snapshot(settings, registry)

    # Assert all 7 expected stage keys exist in snapshot['stages']
    expected_stages = {
        "planner",
        "agent_low",
        "agent_medium",
        "agent_high",
        "reviewer",
        "contract_builder",
        "ci_fix",
    }

    stages = snapshot["stages"]
    assert isinstance(stages, dict)

    # Verify all 7 stages are present
    actual_stages = set(stages.keys())
    assert actual_stages == expected_stages, f"Expected stages {expected_stages}, got {actual_stages}"

    # Verify each stage has required fields
    for stage_name in expected_stages:
        stage = stages[stage_name]
        assert "spec" in stage
        assert "provider" in stage
        assert "model" in stage
        assert "backend" in stage
        assert "canonical_id" in stage
