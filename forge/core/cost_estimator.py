"""Pipeline cost estimation using CostRegistry and per-stage token estimates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.core.cost_registry import (
    CostRegistry,
    PipelineCostEstimate,
    StageCostEstimate,
)

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry

# Per-stage token estimates (heuristic, from design Section 9.5)
_STAGE_TOKEN_ESTIMATES: dict[str, tuple[int, int]] = {
    # (estimated_input_tokens, estimated_output_tokens)
    "planner": (6000, 4000),
    "contract_builder": (5000, 3000),
    "agent": (8000, 4000),
    "reviewer": (6000, 2000),
    "ci_fix": (5000, 3000),
}


async def estimate_pipeline_cost(
    task_count: int,
    strategy: str = "auto",
    cost_registry: CostRegistry | None = None,
    overrides: dict[str, str] | None = None,
    registry: ProviderRegistry | None = None,
) -> PipelineCostEstimate:
    """Estimate total pipeline cost based on task count and model rates.

    The estimation assumes:
    - 1 planner session
    - 1 contract_builder session
    - N agent sessions (one per task)
    - N reviewer sessions (one per task)
    - 1 ci_fix session (estimated)

    Args:
        task_count: Number of tasks in the pipeline.
        strategy: Model routing strategy ("auto", "fast", "quality").
        cost_registry: CostRegistry for rate lookups. Uses default if None.
        overrides: Per-stage model overrides dict.
        registry: ProviderRegistry for validation. Optional.

    Returns:
        PipelineCostEstimate with per-stage breakdown.
    """
    from forge.core.model_router import select_model

    if cost_registry is None:
        cost_registry = CostRegistry()

    stages: list[StageCostEstimate] = []

    # Stages with their multipliers (how many sessions of each)
    stage_multipliers = {
        "planner": 1,
        "contract_builder": 1,
        "agent": max(task_count, 1),
        "reviewer": max(task_count, 1),
        "ci_fix": 1,
    }

    for stage, multiplier in stage_multipliers.items():
        spec = select_model(
            strategy=strategy,
            stage=stage,
            complexity="medium",
            overrides=overrides,
            registry=registry,
        )
        est_input, est_output = _STAGE_TOKEN_ESTIMATES.get(stage, (4000, 2000))
        total_input = est_input * multiplier
        total_output = est_output * multiplier
        cost = cost_registry.calculate_cost(spec, total_input, total_output)

        stages.append(
            StageCostEstimate(
                stage=stage,
                model_spec=spec,
                estimated_input_tokens=total_input,
                estimated_output_tokens=total_output,
                estimated_cost_usd=round(cost, 6),
            )
        )

    return PipelineCostEstimate(stages=stages)
