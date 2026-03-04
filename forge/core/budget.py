"""Budget enforcement for Forge pipelines."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.config.settings import ForgeSettings
    from forge.storage.db import Database


class BudgetExceededError(Exception):
    """Raised when a pipeline's spending reaches or exceeds its budget."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"Budget exceeded: spent ${spent:.4f} of ${limit:.4f} limit"
        )


async def check_budget(
    db: "Database", pipeline_id: str, settings: "ForgeSettings",
) -> None:
    """Check whether a pipeline has exceeded its budget.

    Uses the pipeline-level budget if set, otherwise falls back to the
    global ``settings.budget_limit_usd``.  A limit of 0 means unlimited.

    Raises:
        BudgetExceededError: If the budget is exceeded.
    """
    spent = await db.get_pipeline_cost(pipeline_id)
    limit = await db.get_pipeline_budget(pipeline_id)

    # Fall back to global setting if no pipeline-level budget
    if limit <= 0:
        limit = settings.budget_limit_usd

    # 0 means unlimited
    if limit <= 0:
        return

    if spent >= limit:
        raise BudgetExceededError(spent=spent, limit=limit)
