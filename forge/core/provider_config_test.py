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


def test_resolve_registry_model_uses_registry_settings_overrides() -> None:
    """Registry-backed helper selection should honor per-stage overrides."""
    settings = ForgeSettings(
        openai_enabled=True,
        reviewer_model="openai:gpt-5.4-mini",
    )
    registry = build_provider_registry(settings)

    result = resolve_registry_model(registry, "reviewer", "low")

    assert result == ModelSpec("openai", "gpt-5.4-mini")
