"""Tests for pipeline cost estimation."""

from forge.config.settings import ForgeSettings
from forge.core.cost_estimator import (
    _model_family,
    _estimate_session_cost,
    estimate_pipeline_cost,
)


class TestModelFamily:
    def test_opus_from_full_name(self):
        assert _model_family("claude-3-opus-20240229") == "opus"

    def test_opus_from_short_name(self):
        assert _model_family("opus") == "opus"

    def test_haiku_from_full_name(self):
        assert _model_family("claude-3-haiku-20240307") == "haiku"

    def test_haiku_from_short_name(self):
        assert _model_family("haiku") == "haiku"

    def test_sonnet_from_full_name(self):
        assert _model_family("claude-3-sonnet-20240229") == "sonnet"

    def test_sonnet_from_short_name(self):
        assert _model_family("sonnet") == "sonnet"

    def test_unknown_defaults_to_sonnet(self):
        assert _model_family("some-future-model") == "sonnet"

    def test_case_insensitive(self):
        assert _model_family("OPUS") == "opus"
        assert _model_family("Haiku") == "haiku"


class TestEstimateSessionCost:
    def test_sonnet_cost(self):
        settings = ForgeSettings()
        cost = _estimate_session_cost("sonnet", settings)
        # 4000/1000 * 0.003 + 2000/1000 * 0.015 = 0.012 + 0.030 = 0.042
        assert abs(cost - 0.042) < 0.001

    def test_haiku_cost(self):
        settings = ForgeSettings()
        cost = _estimate_session_cost("haiku", settings)
        # 4000/1000 * 0.00025 + 2000/1000 * 0.00125 = 0.001 + 0.0025 = 0.0035
        assert abs(cost - 0.0035) < 0.001

    def test_opus_cost(self):
        settings = ForgeSettings()
        cost = _estimate_session_cost("opus", settings)
        # 4000/1000 * 0.015 + 2000/1000 * 0.075 = 0.060 + 0.150 = 0.210
        assert abs(cost - 0.210) < 0.001


class TestEstimatePipelineCost:
    async def test_returns_positive_value(self):
        settings = ForgeSettings()
        cost = await estimate_pipeline_cost(3, settings, "auto")
        assert cost > 0

    async def test_scales_with_task_count(self):
        settings = ForgeSettings()
        cost_3 = await estimate_pipeline_cost(3, settings, "auto")
        cost_6 = await estimate_pipeline_cost(6, settings, "auto")
        assert cost_6 > cost_3

    async def test_fast_strategy_cheaper_than_quality(self):
        settings = ForgeSettings()
        fast = await estimate_pipeline_cost(5, settings, "fast")
        quality = await estimate_pipeline_cost(5, settings, "quality")
        assert fast < quality

    async def test_single_task_pipeline(self):
        settings = ForgeSettings()
        cost = await estimate_pipeline_cost(1, settings, "auto")
        assert cost > 0

    async def test_zero_tasks(self):
        settings = ForgeSettings()
        cost = await estimate_pipeline_cost(0, settings, "auto")
        # Only planner cost with 0 tasks
        assert cost > 0
