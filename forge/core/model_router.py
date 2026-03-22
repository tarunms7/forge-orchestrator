"""Model routing by task complexity and pipeline stage."""

from __future__ import annotations

import logging

logger = logging.getLogger("forge.model_router")

# Strategy -> Stage -> Complexity -> Model
_ROUTING_TABLE: dict[str, dict[str, dict[str, str]]] = {
    "auto": {
        "planner": {"low": "opus", "medium": "opus", "high": "opus"},
        "contract_builder": {"low": "opus", "medium": "opus", "high": "opus"},
        "agent": {"low": "sonnet", "medium": "opus", "high": "opus"},
        "reviewer": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
    },
    "fast": {
        "planner": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
        "contract_builder": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
        "agent": {"low": "haiku", "medium": "haiku", "high": "haiku"},
        "reviewer": {"low": "haiku", "medium": "sonnet", "high": "sonnet"},
    },
    "quality": {
        "planner": {"low": "opus", "medium": "opus", "high": "opus"},
        "contract_builder": {"low": "opus", "medium": "opus", "high": "opus"},
        "agent": {"low": "opus", "medium": "opus", "high": "opus"},
        "reviewer": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
    },
}


def select_model(strategy: str, stage: str, complexity: str, overrides: dict | None = None) -> str:
    """Select the Claude model for a given strategy, pipeline stage, and task complexity.

    Args:
        strategy: "auto", "fast", or "quality"
        stage: "planner", "contract_builder", "agent", or "reviewer"
        complexity: "low", "medium", or "high"
        overrides: Optional dict of model overrides from user settings.
            Keys like ``planner_model``, ``reviewer_model``,
            ``agent_model_low``, ``agent_model_medium``, ``agent_model_high``.

    Returns:
        Model name string: "opus", "sonnet", or "haiku"
    """
    if overrides:
        # Check for direct override — planner/reviewer/contract_builder use {stage}_model,
        # agent uses agent_model_{complexity}
        if stage in ("planner", "reviewer", "contract_builder"):
            key = f"{stage}_model"
        else:
            key = f"agent_model_{complexity}"
        override_val = overrides.get(key)
        if override_val:
            return override_val

    table = _ROUTING_TABLE.get(strategy)
    if table is None:
        logger.warning("Unknown model_strategy '%s', falling back to 'auto'", strategy)
        table = _ROUTING_TABLE["auto"]

    stage_map = table.get(stage)
    if stage_map is None:
        logger.warning(
            "Unknown stage '%s' for strategy '%s', falling back to 'agent'", stage, strategy
        )
        stage_map = table["agent"]

    model = stage_map.get(complexity)
    if model is None:
        logger.warning(
            "Unknown complexity '%s' for stage '%s', falling back to 'sonnet'", complexity, stage
        )
        model = "sonnet"

    return model
