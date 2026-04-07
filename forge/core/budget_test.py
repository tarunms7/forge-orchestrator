"""Tests for budget enforcement."""

from unittest.mock import AsyncMock

import pytest

from forge.core.budget import BudgetExceededError, check_budget, resolve_cost
from forge.core.errors import ForgeError


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

    def test_is_forge_error_subclass(self):
        err = BudgetExceededError(spent=1.0, limit=0.5)
        assert isinstance(err, ForgeError)


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

    async def test_timeout_on_slow_db(self):
        """Budget check should raise TimeoutError if DB is too slow."""
        import asyncio

        db = AsyncMock()

        async def _slow(*args, **kwargs):
            await asyncio.sleep(60)
            return 0.0

        db.get_pipeline_cost = _slow
        db.get_pipeline_budget = _slow
        settings = _make_settings(budget_limit_usd=1.0)

        with pytest.raises(asyncio.TimeoutError):
            await check_budget(db, "pipe-1", settings)

    async def test_cost_registry_param_accepted(self):
        """cost_registry parameter is accepted without error."""
        from forge.core.cost_registry import CostRegistry

        db = _make_db(pipeline_cost=0.5, pipeline_budget=1.0)
        settings = _make_settings()
        cost_registry = CostRegistry()
        await check_budget(db, "pipe-1", settings, cost_registry=cost_registry)


class TestResolveCost:
    def test_uses_provider_reported_cost(self):
        from forge.core.cost_registry import CostRegistry
        from forge.providers.base import ModelSpec, ProviderResult

        result = ProviderResult(
            text="done",
            is_error=False,
            input_tokens=1000,
            output_tokens=500,
            resume_state=None,
            duration_ms=1000,
            provider_reported_cost_usd=0.42,
            model_canonical_id="claude-sonnet-4",
        )
        spec = ModelSpec("claude", "sonnet")
        registry = CostRegistry()
        cost = resolve_cost(result, spec, registry)
        assert cost == 0.42

    def test_calculates_from_tokens(self):
        from forge.core.cost_registry import CostRegistry
        from forge.providers.base import ModelSpec, ProviderResult

        result = ProviderResult(
            text="done",
            is_error=False,
            input_tokens=1000,
            output_tokens=1000,
            resume_state=None,
            duration_ms=1000,
            provider_reported_cost_usd=None,
            model_canonical_id="claude-sonnet-4",
        )
        spec = ModelSpec("claude", "sonnet")
        registry = CostRegistry()
        cost = resolve_cost(result, spec, registry)
        # 1000/1000 * 0.003 + 1000/1000 * 0.015 = 0.018
        assert abs(cost - 0.018) < 0.001
