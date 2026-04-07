"""Helpers for provider-aware settings, registries, and routing snapshots."""

from __future__ import annotations

import json
import logging
from typing import Any

from forge.config.project_config import ProjectConfig, apply_project_config
from forge.config.settings import ForgeSettings
from forge.core.model_router import select_model
from forge.providers.base import CatalogEntry, ModelSpec
from forge.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

_USER_SETTINGS_TO_ATTR = {
    "max_agents": "max_agents",
    "timeout": "agent_timeout_seconds",
    "max_retries": "max_retries",
    "model_strategy": "model_strategy",
    "planner_model": "planner_model",
    "agent_model_low": "agent_model_low",
    "agent_model_medium": "agent_model_medium",
    "agent_model_high": "agent_model_high",
    "reviewer_model": "reviewer_model",
    "contract_builder_model": "contract_builder_model",
    "ci_fix_model": "ci_fix_model",
    "planner_reasoning_effort": "planner_reasoning_effort",
    "agent_model_low_reasoning_effort": "agent_model_low_reasoning_effort",
    "agent_model_medium_reasoning_effort": "agent_model_medium_reasoning_effort",
    "agent_model_high_reasoning_effort": "agent_model_high_reasoning_effort",
    "reviewer_reasoning_effort": "reviewer_reasoning_effort",
    "contract_builder_reasoning_effort": "contract_builder_reasoning_effort",
    "ci_fix_reasoning_effort": "ci_fix_reasoning_effort",
    "autonomy": "autonomy",
    "question_limit": "question_limit",
    "question_timeout": "question_timeout",
    "auto_pr": "auto_pr",
}

_SNAPSHOT_TO_SETTING = {
    "planner": "planner_model",
    "agent_low": "agent_model_low",
    "agent_medium": "agent_model_medium",
    "agent_high": "agent_model_high",
    "reviewer": "reviewer_model",
    "contract_builder": "contract_builder_model",
    "ci_fix": "ci_fix_model",
}

_SNAPSHOT_TO_REASONING_SETTING = {
    "planner": "planner_reasoning_effort",
    "agent_low": "agent_model_low_reasoning_effort",
    "agent_medium": "agent_model_medium_reasoning_effort",
    "agent_high": "agent_model_high_reasoning_effort",
    "reviewer": "reviewer_reasoning_effort",
    "contract_builder": "contract_builder_reasoning_effort",
    "ci_fix": "ci_fix_reasoning_effort",
}

_ROUTING_PLAN = (
    ("planner", "planner", "high"),
    ("agent_low", "agent", "low"),
    ("agent_medium", "agent", "medium"),
    ("agent_high", "agent", "high"),
    ("reviewer", "reviewer", "medium"),
    ("contract_builder", "contract_builder", "high"),
    ("ci_fix", "ci_fix", "medium"),
)


def apply_user_settings(settings: ForgeSettings, user_settings: dict[str, Any] | None) -> None:
    """Apply persisted web settings onto ForgeSettings."""
    if not user_settings:
        return
    for source_key, attr_name in _USER_SETTINGS_TO_ATTR.items():
        if source_key in user_settings and user_settings[source_key] is not None:
            setattr(settings, attr_name, user_settings[source_key])


def _parse_provider_config(provider_config: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if provider_config is None:
        return None
    if isinstance(provider_config, dict):
        return provider_config
    if isinstance(provider_config, (bytes, bytearray)):
        try:
            provider_config = provider_config.decode()
        except UnicodeDecodeError:
            logger.warning("Failed to decode provider_config bytes", exc_info=True)
            return None
    if not isinstance(provider_config, str):
        return None
    if not provider_config.strip():
        return None
    try:
        parsed = json.loads(provider_config)
    except json.JSONDecodeError:
        logger.warning("Failed to parse provider_config JSON", exc_info=True)
        return None
    return parsed if isinstance(parsed, dict) else None


def apply_provider_config_snapshot(
    settings: ForgeSettings,
    provider_config: str | dict[str, Any] | None,
) -> None:
    """Apply a persisted provider snapshot onto ForgeSettings."""
    parsed = _parse_provider_config(provider_config)
    if not parsed:
        return

    model_strategy = parsed.get("model_strategy")
    if isinstance(model_strategy, str) and model_strategy:
        settings.model_strategy = model_strategy

    stages = parsed.get("stages", parsed)
    if not isinstance(stages, dict):
        return

    for snapshot_key, settings_attr in _SNAPSHOT_TO_SETTING.items():
        entry = stages.get(snapshot_key)
        if not isinstance(entry, dict):
            continue
        spec = entry.get("spec")
        if not isinstance(spec, str) or not spec:
            provider = entry.get("provider")
            model = entry.get("model")
            if isinstance(provider, str) and isinstance(model, str) and provider and model:
                spec = f"{provider}:{model}"
        if isinstance(spec, str) and spec:
            setattr(settings, settings_attr, spec)
        reasoning_effort = entry.get("reasoning_effort")
        reasoning_attr = _SNAPSHOT_TO_REASONING_SETTING.get(snapshot_key)
        if (
            reasoning_attr
            and isinstance(reasoning_effort, str)
            and reasoning_effort in {"low", "medium", "high"}
        ):
            setattr(settings, reasoning_attr, reasoning_effort)


def build_settings_for_project(
    project_dir: str,
    *,
    user_settings: dict[str, Any] | None = None,
    model_strategy: str | None = None,
    provider_config: str | dict[str, Any] | None = None,
) -> tuple[ForgeSettings, ProjectConfig]:
    """Build execution settings from project config, saved user settings, and snapshot."""
    settings = ForgeSettings()
    project_config = ProjectConfig.load(project_dir)
    apply_project_config(settings, project_config)
    apply_user_settings(settings, user_settings)
    if model_strategy is not None:
        settings.model_strategy = model_strategy
    apply_provider_config_snapshot(settings, provider_config)
    return settings, project_config


def _custom_entry_from_config(
    provider_name: str,
    alias: str,
    canonical_id: str,
    backend: str,
) -> CatalogEntry:
    is_claude_sdk = backend == "claude-code-sdk"
    is_codex_sdk = backend == "codex-sdk"
    is_agents_sdk = backend == "openai-agents-sdk"
    return CatalogEntry(
        provider=provider_name,
        alias=alias,
        canonical_id=canonical_id,
        backend=backend,
        tier="experimental",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=is_claude_sdk,
        can_run_shell=is_claude_sdk or is_codex_sdk,
        can_edit_files=is_claude_sdk or is_codex_sdk,
        supports_mcp_servers=is_claude_sdk,
        max_context_tokens=200_000 if is_agents_sdk else 128_000,
        supports_structured_output=is_agents_sdk or is_codex_sdk,
        supports_reasoning=is_agents_sdk,
        cost_key=f"{provider_name}:{alias}",
        validated_stages=frozenset(),
    )


def _settings_reference_provider(settings: ForgeSettings, provider_name: str) -> bool:
    """Return True when any explicit per-stage override references a provider."""
    model_fields = (
        settings.planner_model,
        settings.agent_model_low,
        settings.agent_model_medium,
        settings.agent_model_high,
        settings.reviewer_model,
        settings.contract_builder_model,
        settings.ci_fix_model,
    )
    for raw in model_fields:
        if not raw:
            continue
        try:
            if ModelSpec.parse(raw).provider == provider_name:
                return True
        except ValueError:
            continue
    return False


def resolve_reasoning_effort_for_stage(
    settings: ForgeSettings,
    stage: str,
    complexity: str = "medium",
) -> str | None:
    """Resolve the explicit reasoning-effort override for a stage, if configured."""
    return settings.resolve_reasoning_effort(stage, complexity)


def build_provider_registry(
    settings: ForgeSettings,
    project_config: ProjectConfig | None = None,
) -> ProviderRegistry:
    """Build a registry with built-in providers plus valid project custom models."""
    from forge.providers.claude import ClaudeProvider

    registry = ProviderRegistry(settings)
    registry.register(ClaudeProvider())

    needs_openai = settings.openai_enabled or _settings_reference_provider(settings, "openai")
    if not needs_openai and project_config is not None:
        needs_openai = any(cm.provider == "openai" for cm in project_config.custom_models)

    if needs_openai:
        try:
            from forge.providers.openai import OpenAIProvider

            registry.register(OpenAIProvider())
        except ImportError:
            logger.warning(
                "openai_enabled=True but OpenAI provider is unavailable; continuing without it"
            )
        except Exception:
            logger.warning("Failed to register OpenAI provider", exc_info=True)

    if project_config is None:
        return registry

    providers = {provider.name for provider in registry.all_providers()}
    seen_specs: set[str] = set()
    for custom_model in project_config.custom_models:
        spec = f"{custom_model.provider}:{custom_model.alias}"
        if spec in seen_specs:
            continue
        seen_specs.add(spec)

        if custom_model.provider not in providers:
            logger.warning("Skipping custom model %s: provider not registered", spec)
            continue
        if not custom_model.backend or not custom_model.canonical_id:
            logger.warning("Skipping custom model %s: backend/canonical_id missing", spec)
            continue

        registry.register_catalog_entry(
            _custom_entry_from_config(
                provider_name=custom_model.provider,
                alias=custom_model.alias,
                canonical_id=custom_model.canonical_id,
                backend=custom_model.backend,
            )
        )

    return registry


def ensure_provider_registry(
    registry: ProviderRegistry | None,
    *,
    settings: ForgeSettings | None = None,
    project_config: ProjectConfig | None = None,
) -> ProviderRegistry | None:
    """Return an existing registry or build one from the current settings."""
    if registry is not None:
        return registry

    try:
        return build_provider_registry(settings or ForgeSettings(), project_config)
    except Exception:
        logger.warning("Failed to build fallback ProviderRegistry", exc_info=True)
        return None


def resolve_registry_model(
    registry: ProviderRegistry,
    stage: str,
    complexity: str = "medium",
    *,
    retry_count: int = 0,
    strategy: str | None = None,
) -> ModelSpec:
    """Resolve a stage model using the settings that produced ``registry``."""
    settings = registry.settings
    return resolve_model_for_stage(
        settings,
        registry,
        stage,
        complexity,
        retry_count=retry_count,
        strategy=strategy or settings.model_strategy,
    )


def resolve_model_for_stage(
    settings: ForgeSettings,
    registry: ProviderRegistry,
    stage: str,
    complexity: str = "medium",
    *,
    retry_count: int = 0,
    strategy: str | None = None,
) -> ModelSpec:
    """Resolve a stage model using settings overrides plus registry validation."""
    return select_model(
        strategy or settings.model_strategy,
        stage,
        complexity,
        overrides=settings.build_routing_overrides(),
        retry_count=retry_count,
        registry=registry,
    )


def resolve_pipeline_models(
    settings: ForgeSettings,
    registry: ProviderRegistry,
    *,
    strategy: str | None = None,
) -> dict[str, ModelSpec]:
    """Resolve the full pipeline routing table to concrete model specs."""
    resolved: dict[str, ModelSpec] = {}
    for snapshot_key, stage, complexity in _ROUTING_PLAN:
        resolved[snapshot_key] = resolve_model_for_stage(
            settings,
            registry,
            stage,
            complexity,
            strategy=strategy,
        )
    return resolved


def build_provider_config_snapshot(
    settings: ForgeSettings,
    registry: ProviderRegistry,
    *,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Build the persisted per-stage provider routing snapshot for a pipeline."""
    stages: dict[str, dict[str, str]] = {}
    resolved = resolve_pipeline_models(
        settings,
        registry,
        strategy=strategy,
    )
    for snapshot_key, stage, complexity in _ROUTING_PLAN:
        spec = resolved[snapshot_key]
        entry = registry.get_catalog_entry(spec)
        stages[snapshot_key] = {
            "spec": str(spec),
            "provider": spec.provider,
            "model": spec.model,
            "backend": entry.backend,
            "canonical_id": entry.canonical_id,
        }
        reasoning_effort = resolve_reasoning_effort_for_stage(settings, stage, complexity)
        if reasoning_effort is not None:
            stages[snapshot_key]["reasoning_effort"] = reasoning_effort
    return {
        "model_strategy": strategy or settings.model_strategy,
        "stages": stages,
    }
