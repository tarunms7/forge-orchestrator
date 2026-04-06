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
