"""Budget enforcement for Forge pipelines."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from forge.core.errors import ForgeError

if TYPE_CHECKING:
    from forge.config.settings import ForgeSettings
    from forge.core.cost_registry import CostRegistry
    from forge.providers.base import ModelSpec, ProviderResult
    from forge.storage.db import Database


class BudgetExceededError(ForgeError):
    """Raised when a pipeline's spending reaches or exceeds its budget."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(f"Budget exceeded: spent ${spent:.4f} of ${limit:.4f} limit")


async def check_budget(
    db: Database,
    pipeline_id: str,
    settings: ForgeSettings,
    cost_registry: CostRegistry | None = None,
) -> None:
    """Check whether a pipeline has exceeded its budget.

    Uses the pipeline-level budget if set, otherwise falls back to the
    global ``settings.budget_limit_usd``.  A limit of 0 means unlimited.

    Args:
        db: Database for cost/budget lookups.
        pipeline_id: Pipeline identifier.
        settings: ForgeSettings with global budget_limit_usd.
        cost_registry: Optional CostRegistry for cost resolution.

    Raises:
        BudgetExceededError: If the budget is exceeded.
    """
    spent = await asyncio.wait_for(db.get_pipeline_cost(pipeline_id), timeout=5)
    limit = await asyncio.wait_for(db.get_pipeline_budget(pipeline_id), timeout=5)

    # Fall back to global setting if no pipeline-level budget
    if limit <= 0:
        limit = settings.budget_limit_usd

    # 0 means unlimited
    if limit <= 0:
        return

    if spent >= limit:
        raise BudgetExceededError(spent=spent, limit=limit)


def resolve_cost(
    result: ProviderResult,
    spec: ModelSpec,
    cost_registry: CostRegistry,
) -> float:
    """Resolve the cost of a provider execution.

    Uses provider_reported_cost first if available, else calculates
    from token counts using the cost registry.

    This is a convenience re-export; the canonical implementation
    is in ``forge.core.cost_registry.resolve_cost``.
    """
    from forge.core.cost_registry import resolve_cost as _resolve_cost

    return _resolve_cost(result, spec, cost_registry)
