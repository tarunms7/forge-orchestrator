"""Model routing by task complexity and pipeline stage.

Returns ModelSpec objects for multi-provider support. Supports intra-provider
escalation, provider tier mapping, and configurable override precedence.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from forge.providers.base import ModelSpec

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry

logger = logging.getLogger("forge.model_router")

# Strategy -> Stage -> Complexity -> 'provider:model'
_ROUTING_TABLE: dict[str, dict[str, dict[str, str]]] = {
    "auto": {
        "planner": {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "contract_builder": {
            "low": "claude:opus",
            "medium": "claude:opus",
            "high": "claude:opus",
        },
        "agent": {"low": "claude:sonnet", "medium": "claude:opus", "high": "claude:opus"},
        "reviewer": {
            "low": "claude:sonnet",
            "medium": "claude:sonnet",
            "high": "claude:sonnet",
        },
        "ci_fix": {
            "low": "claude:sonnet",
            "medium": "claude:sonnet",
            "high": "claude:sonnet",
        },
    },
    "fast": {
        "planner": {
            "low": "claude:sonnet",
            "medium": "claude:sonnet",
            "high": "claude:sonnet",
        },
        "contract_builder": {
            "low": "claude:sonnet",
            "medium": "claude:sonnet",
            "high": "claude:sonnet",
        },
        "agent": {"low": "claude:haiku", "medium": "claude:haiku", "high": "claude:haiku"},
        "reviewer": {
            "low": "claude:haiku",
            "medium": "claude:sonnet",
            "high": "claude:sonnet",
        },
        "ci_fix": {
            "low": "claude:haiku",
            "medium": "claude:haiku",
            "high": "claude:sonnet",
        },
    },
    "quality": {
        "planner": {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "contract_builder": {
            "low": "claude:opus",
            "medium": "claude:opus",
            "high": "claude:opus",
        },
        "agent": {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "reviewer": {
            "low": "claude:sonnet",
            "medium": "claude:sonnet",
            "high": "claude:sonnet",
        },
        "ci_fix": {
            "low": "claude:sonnet",
            "medium": "claude:sonnet",
            "high": "claude:opus",
        },
    },
}

# Intra-provider escalation chains: model -> next tier up
_ESCALATION_CHAINS: dict[str, dict[str, str]] = {
    "claude": {"haiku": "sonnet", "sonnet": "opus"},
    "openai": {"gpt-5.3-codex": "gpt-5.4-mini", "gpt-5.4-mini": "gpt-5.4"},
}

# Provider tier map: maps high/medium/low tiers to provider-specific models.
# Used when --provider shorthand is set to translate routing table entries.
_PROVIDER_TIER_MAP: dict[str, dict[str, str]] = {
    "claude": {"high": "claude:opus", "medium": "claude:sonnet", "low": "claude:haiku"},
    "openai": {
        "high": "openai:gpt-5.4",
        "medium": "openai:gpt-5.4-mini",
        "low": "openai:gpt-5.3-codex",
    },
}

# Maps Claude models to tier names for provider translation
_CLAUDE_MODEL_TIER: dict[str, str] = {
    "opus": "high",
    "sonnet": "medium",
    "haiku": "low",
}


def select_model(
    strategy: str,
    stage: str,
    complexity: str = "medium",
    overrides: dict[str, str] | None = None,
    retry_count: int = 0,
    routing_table: dict | None = None,
    registry: ProviderRegistry | None = None,
) -> ModelSpec:
    """Select the model for a given strategy, pipeline stage, and task complexity.

    Override precedence: CLI per-stage overrides > forge.toml [routing] >
    ForgeSettings env vars > default routing table.

    Args:
        strategy: "auto", "fast", or "quality"
        stage: "planner", "contract_builder", "agent", "reviewer", or "ci_fix"
        complexity: "low", "medium", or "high"
        overrides: Optional dict of model overrides from user settings.
            Keys like ``planner_model``, ``reviewer_model``,
            ``agent_model_low``, ``agent_model_medium``, ``agent_model_high``,
            ``contract_builder_model``, ``ci_fix_model``.
            Values can be 'provider:model' or bare alias.
        retry_count: Current retry number. On retry 2+, agent models
            escalate one tier within the same provider.
        routing_table: Custom routing table override (3-level dict).
            Default: uses _ROUTING_TABLE.
        registry: Provider registry for validation. Default: None (skip validation).

    Returns:
        ModelSpec identifying the provider and model.
    """
    raw: str | None = None

    # 1. Check overrides (highest priority)
    if overrides:
        if stage in ("planner", "reviewer", "contract_builder", "ci_fix"):
            key = f"{stage}_model"
        else:
            key = f"agent_model_{complexity}"
        raw = overrides.get(key)

    # 2. Look up from routing table
    if not raw:
        table = (routing_table or _ROUTING_TABLE).get(strategy)
        if table is None:
            logger.warning("Unknown model_strategy '%s', falling back to 'auto'", strategy)
            table = (routing_table or _ROUTING_TABLE)["auto"]

        stage_map = table.get(stage)
        if stage_map is None:
            logger.warning(
                "Unknown stage '%s' for strategy '%s', falling back to 'agent'",
                stage,
                strategy,
            )
            stage_map = table["agent"]

        raw = stage_map.get(complexity)
        if raw is None:
            logger.warning(
                "Unknown complexity '%s' for stage '%s', falling back to 'medium'",
                complexity,
                stage,
            )
            raw = stage_map.get("medium", "claude:sonnet")

    # Parse the raw string into a ModelSpec
    spec = ModelSpec.parse(raw)

    # 3. Escalate on retry 2+ for agent stage only (intra-provider)
    if retry_count >= 2 and stage == "agent":
        chain = _ESCALATION_CHAINS.get(spec.provider, {})
        escalated = chain.get(spec.model)
        if escalated:
            logger.info(
                "Escalating model %s -> %s:%s for retry %d (stage=%s, complexity=%s)",
                spec,
                spec.provider,
                escalated,
                retry_count,
                stage,
                complexity,
            )
            spec = ModelSpec(provider=spec.provider, model=escalated)

    # 4. Validate against registry if provided
    if registry is not None:
        issues = registry.validate_model_for_stage(spec, stage)
        for issue in issues:
            if issue.startswith("BLOCKED:"):
                logger.error("Model validation failed: %s", issue)
            else:
                logger.warning("Model validation: %s", issue)

    return spec


def translate_to_provider(
    provider: str,
    routing_table: dict[str, dict[str, dict[str, str]]] | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    """Translate an entire routing table to a target provider using tier mapping.

    Used when --provider is set. Maps each entry's tier (high/medium/low)
    to the target provider's corresponding model.

    Args:
        provider: Target provider name (e.g. 'openai').
        routing_table: Source table to translate. Defaults to _ROUTING_TABLE.

    Returns:
        New routing table with all models mapped to the target provider.
    """
    tier_map = _PROVIDER_TIER_MAP.get(provider)
    if tier_map is None:
        logger.warning("No tier map for provider '%s', returning original table", provider)
        return routing_table or _ROUTING_TABLE

    source = routing_table or _ROUTING_TABLE
    translated: dict[str, dict[str, dict[str, str]]] = {}

    for strategy, stages in source.items():
        translated[strategy] = {}
        for stage, complexities in stages.items():
            translated[strategy][stage] = {}
            for complexity, raw_model in complexities.items():
                # Determine the tier of the source model
                src_spec = ModelSpec.parse(raw_model)
                tier = _CLAUDE_MODEL_TIER.get(src_spec.model)
                if tier and tier in tier_map:
                    translated[strategy][stage][complexity] = tier_map[tier]
                else:
                    # Unknown model tier — keep as-is
                    translated[strategy][stage][complexity] = raw_model

    return translated
