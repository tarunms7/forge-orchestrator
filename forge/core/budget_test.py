"""Tests for budget enforcement."""

import pytest
from unittest.mock import AsyncMock

from forge.core.budget import BudgetExceededError, check_budget


def _make_settings(budget_limit_usd: float = 0.0):
    """Create a mock settings object."""
    settings = AsyncMock()
    settings.budget_limit_usd = budget_limit_usd
    return settings


def _make_db(pipeline_cost: float = 0.0, pipeline_budget: float = 0.0):
    """Create a mock database with configurable cost/budget returns."""
    db = AsyncMock()
    db.get_pipeline_cost = AsyncMock(return_value=pipeline_cost)
    db.get_pipeline_budget = AsyncMock(return_value=pipeline_budget)
    return db


class TestBudgetExceededError:
    def test_attributes(self):
        err = BudgetExceededError(spent=1.5, limit=1.0)
        assert err.spent == 1.5
        assert err.limit == 1.0

    def test_message(self):
        err = BudgetExceededError(spent=0.5, limit=0.5)
        assert "0.5" in str(err)
        assert "Budget exceeded" in str(err)


class TestCheckBudget:
    async def test_unlimited_budget_does_not_raise(self):
        """Budget of 0 means unlimited — should never raise."""
        db = _make_db(pipeline_cost=100.0, pipeline_budget=0.0)
        settings = _make_settings(budget_limit_usd=0.0)
        # Should not raise
        await check_budget(db, "pipe-1", settings)

    async def test_under_budget_does_not_raise(self):
        """Spent < limit should not raise."""
        db = _make_db(pipeline_cost=0.5, pipeline_budget=1.0)
        settings = _make_settings()
        await check_budget(db, "pipe-1", settings)

    async def test_at_budget_raises(self):
        """Spent == limit should raise."""
        db = _make_db(pipeline_cost=1.0, pipeline_budget=1.0)
        settings = _make_settings()
        with pytest.raises(BudgetExceededError) as exc_info:
            await check_budget(db, "pipe-1", settings)
        assert exc_info.value.spent == 1.0
        assert exc_info.value.limit == 1.0

    async def test_over_budget_raises(self):
        """Spent > limit should raise."""
        db = _make_db(pipeline_cost=1.5, pipeline_budget=1.0)
        settings = _make_settings()
        with pytest.raises(BudgetExceededError):
            await check_budget(db, "pipe-1", settings)

    async def test_falls_back_to_global_settings(self):
        """When pipeline budget is 0, use global settings."""
        db = _make_db(pipeline_cost=2.0, pipeline_budget=0.0)
        settings = _make_settings(budget_limit_usd=1.5)
        with pytest.raises(BudgetExceededError) as exc_info:
            await check_budget(db, "pipe-1", settings)
        assert exc_info.value.limit == 1.5

    async def test_pipeline_budget_takes_precedence(self):
        """Pipeline-level budget should take precedence over global."""
        db = _make_db(pipeline_cost=0.8, pipeline_budget=1.0)
        settings = _make_settings(budget_limit_usd=0.5)
        # Pipeline budget is 1.0, spent is 0.8 — under budget
        await check_budget(db, "pipe-1", settings)

    async def test_global_budget_under_does_not_raise(self):
        """Global budget under limit should not raise."""
        db = _make_db(pipeline_cost=0.3, pipeline_budget=0.0)
        settings = _make_settings(budget_limit_usd=1.0)
        await check_budget(db, "pipe-1", settings)
