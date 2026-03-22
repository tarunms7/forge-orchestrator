"""Pipeline cost estimation based on model rates and task count."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.config.settings import ForgeSettings


# Average tokens per session (heuristic)
_AVG_INPUT_TOKENS = 4000
_AVG_OUTPUT_TOKENS = 2000


def _model_family(model_name: str) -> str:
    """Map a model name to its family (sonnet/haiku/opus).

    Handles names like "claude-3-opus-20240229", "opus", "sonnet", etc.
    """
    lower = model_name.lower()
    if "opus" in lower:
        return "opus"
    if "haiku" in lower:
        return "haiku"
    # Default to sonnet for any unrecognized model
    return "sonnet"


def _get_rates(family: str, settings: ForgeSettings) -> tuple[float, float]:
    """Return (input_rate, output_rate) per 1K tokens for a model family."""
    if family == "opus":
        return settings.cost_rate_opus_input, settings.cost_rate_opus_output
    if family == "haiku":
        return settings.cost_rate_haiku_input, settings.cost_rate_haiku_output
    return settings.cost_rate_sonnet_input, settings.cost_rate_sonnet_output


def _estimate_session_cost(family: str, settings: ForgeSettings) -> float:
    """Estimate the cost of a single session for a given model family."""
    input_rate, output_rate = _get_rates(family, settings)
    return (
        (_AVG_INPUT_TOKENS / 1000) * input_rate
        + (_AVG_OUTPUT_TOKENS / 1000) * output_rate
    )


async def estimate_pipeline_cost(
    task_count: int,
    settings: ForgeSettings,
    strategy: str = "auto",
) -> float:
    """Estimate total pipeline cost based on task count and model rates.

    The estimation assumes:
    - 1 planner session (uses the planner model for the strategy)
    - N agent sessions (one per task, using the agent model)
    - N reviewer sessions (one per task, using the reviewer model)

    Returns the estimated cost in USD.
    """
    from forge.core.model_router import select_model

    # Determine models used at each stage for a "medium" complexity estimate
    planner_model = select_model(strategy, "planner", "medium")
    agent_model = select_model(strategy, "agent", "medium")
    reviewer_model = select_model(strategy, "reviewer", "medium")

    planner_family = _model_family(planner_model)
    agent_family = _model_family(agent_model)
    reviewer_family = _model_family(reviewer_model)

    planner_cost = _estimate_session_cost(planner_family, settings)
    agent_cost = _estimate_session_cost(agent_family, settings) * task_count
    reviewer_cost = _estimate_session_cost(reviewer_family, settings) * task_count

    total = planner_cost + agent_cost + reviewer_cost
    return round(total, 6)
