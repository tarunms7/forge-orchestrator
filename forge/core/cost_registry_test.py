"""Tests for forge/core/cost_registry.py — cost tracking and estimation."""

from __future__ import annotations

import pytest

from forge.core.cost_registry import (
    CostRegistry,
    ModelRates,
    PipelineCostEstimate,
    StageCostEstimate,
    UnknownCostBehavior,
    UnknownModelCostError,
    migrate_legacy_cost_settings,
    resolve_cost,
)
from forge.providers.base import ModelSpec, ProviderResult


# ---------------------------------------------------------------------------
# Rate lookup
# ---------------------------------------------------------------------------


class TestRateLookup:
    def test_exact_key_match(self) -> None:
        registry = CostRegistry()
        rates = registry.get_rates(ModelSpec("claude", "sonnet"))
        assert rates.input_per_1k == 0.003
        assert rates.output_per_1k == 0.015

    def test_opus_rates(self) -> None:
        registry = CostRegistry()
        rates = registry.get_rates(ModelSpec("claude", "opus"))
        assert rates.input_per_1k == 0.015
        assert rates.output_per_1k == 0.075

    def test_openai_rates(self) -> None:
        registry = CostRegistry()
        rates = registry.get_rates(ModelSpec("openai", "gpt-5.4"))
        assert rates.input_per_1k == 0.005

    def test_overrides_take_precedence(self) -> None:
        overrides = {"claude:sonnet": ModelRates(input_per_1k=0.01, output_per_1k=0.05)}
        registry = CostRegistry(overrides=overrides)
        rates = registry.get_rates(ModelSpec("claude", "sonnet"))
        assert rates.input_per_1k == 0.01
        assert rates.output_per_1k == 0.05


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_falls_back_to_provider_default(self) -> None:
        registry = CostRegistry()
        rates = registry.get_rates(ModelSpec("claude", "unknown-model"))
        # Should get claude:default rates
        assert rates.input_per_1k == 0.003
        assert rates.output_per_1k == 0.015

    def test_openai_fallback(self) -> None:
        registry = CostRegistry()
        rates = registry.get_rates(ModelSpec("openai", "future-model"))
        assert rates.input_per_1k == 0.005
        assert rates.output_per_1k == 0.015


# ---------------------------------------------------------------------------
# Unknown behavior modes
# ---------------------------------------------------------------------------


class TestUnknownBehavior:
    def test_block_raises(self) -> None:
        registry = CostRegistry(unknown_behavior=UnknownCostBehavior.BLOCK)
        with pytest.raises(UnknownModelCostError) as exc_info:
            registry.get_rates(ModelSpec("unknown-provider", "model"))
        assert "unknown-provider:model" in str(exc_info.value)

    def test_estimate_high_uses_highest(self) -> None:
        registry = CostRegistry(unknown_behavior=UnknownCostBehavior.ESTIMATE_HIGH)
        rates = registry.get_rates(ModelSpec("unknown-provider", "model"))
        # opus has the highest rates: 0.015 input, 0.075 output
        assert rates.input_per_1k == 0.015
        assert rates.output_per_1k == 0.075

    def test_allow_returns_zero(self) -> None:
        registry = CostRegistry(unknown_behavior=UnknownCostBehavior.ALLOW)
        rates = registry.get_rates(ModelSpec("unknown-provider", "model"))
        assert rates.input_per_1k == 0.0
        assert rates.output_per_1k == 0.0


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


class TestCalculation:
    def test_basic_calculation(self) -> None:
        registry = CostRegistry()
        cost = registry.calculate_cost(
            ModelSpec("claude", "sonnet"),
            input_tokens=10_000,
            output_tokens=5_000,
        )
        # (10000/1000)*0.003 + (5000/1000)*0.015 = 0.03 + 0.075 = 0.105
        assert abs(cost - 0.105) < 1e-9

    def test_zero_tokens(self) -> None:
        registry = CostRegistry()
        cost = registry.calculate_cost(
            ModelSpec("claude", "sonnet"), input_tokens=0, output_tokens=0
        )
        assert cost == 0.0


# ---------------------------------------------------------------------------
# resolve_cost
# ---------------------------------------------------------------------------


class TestResolveCost:
    def test_prefers_provider_reported(self) -> None:
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
        registry = CostRegistry()
        cost = resolve_cost(result, ModelSpec("claude", "sonnet"), registry)
        assert cost == 0.42

    def test_calculates_when_no_provider_cost(self) -> None:
        result = ProviderResult(
            text="done",
            is_error=False,
            input_tokens=10_000,
            output_tokens=5_000,
            resume_state=None,
            duration_ms=1000,
            provider_reported_cost_usd=None,
            model_canonical_id="claude-sonnet-4",
        )
        registry = CostRegistry()
        cost = resolve_cost(result, ModelSpec("claude", "sonnet"), registry)
        assert abs(cost - 0.105) < 1e-9


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def test_converts_settings(self) -> None:
        overrides = migrate_legacy_cost_settings(
            sonnet_input=0.003,
            sonnet_output=0.015,
            haiku_input=0.00025,
            haiku_output=0.00125,
            opus_input=0.015,
            opus_output=0.075,
        )
        assert "claude:sonnet" in overrides
        assert "claude:haiku" in overrides
        assert "claude:opus" in overrides
        assert overrides["claude:sonnet"].input_per_1k == 0.003

    def test_migration_works_with_registry(self) -> None:
        overrides = migrate_legacy_cost_settings(
            sonnet_input=0.1,
            sonnet_output=0.2,
            haiku_input=0.01,
            haiku_output=0.02,
            opus_input=0.5,
            opus_output=1.0,
        )
        registry = CostRegistry(overrides=overrides)
        rates = registry.get_rates(ModelSpec("claude", "sonnet"))
        assert rates.input_per_1k == 0.1


# ---------------------------------------------------------------------------
# Pipeline cost estimate
# ---------------------------------------------------------------------------


class TestPipelineCostEstimate:
    def test_total_cost(self) -> None:
        estimate = PipelineCostEstimate(
            stages=[
                StageCostEstimate(
                    stage="planner",
                    model_spec=ModelSpec("claude", "opus"),
                    estimated_input_tokens=4000,
                    estimated_output_tokens=2000,
                    estimated_cost_usd=0.21,
                ),
                StageCostEstimate(
                    stage="agent",
                    model_spec=ModelSpec("claude", "sonnet"),
                    estimated_input_tokens=8000,
                    estimated_output_tokens=4000,
                    estimated_cost_usd=0.084,
                ),
            ]
        )
        assert abs(estimate.total_cost_usd - 0.294) < 1e-9

    def test_empty_pipeline(self) -> None:
        estimate = PipelineCostEstimate()
        assert estimate.total_cost_usd == 0.0


class TestModelRates:
    def test_frozen(self) -> None:
        rates = ModelRates(input_per_1k=0.003, output_per_1k=0.015)
        with pytest.raises(AttributeError):
            rates.input_per_1k = 0.01  # type: ignore[misc]
