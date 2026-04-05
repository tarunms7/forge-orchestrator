"""Tests for pipeline cost estimation with CostRegistry."""

from forge.core.cost_estimator import (
    _STAGE_TOKEN_ESTIMATES,
    estimate_pipeline_cost,
)
from forge.core.cost_registry import CostRegistry, ModelRates, PipelineCostEstimate
from forge.providers.base import ModelSpec


class TestStageTokenEstimates:
    def test_all_stages_present(self):
        for stage in ("planner", "contract_builder", "agent", "reviewer", "ci_fix"):
            assert stage in _STAGE_TOKEN_ESTIMATES

    def test_estimates_are_positive(self):
        for stage, (inp, out) in _STAGE_TOKEN_ESTIMATES.items():
            assert inp > 0, f"{stage} input tokens should be > 0"
            assert out > 0, f"{stage} output tokens should be > 0"


class TestEstimatePipelineCost:
    async def test_returns_pipeline_cost_estimate(self):
        result = await estimate_pipeline_cost(3, strategy="auto")
        assert isinstance(result, PipelineCostEstimate)

    async def test_has_all_stages(self):
        result = await estimate_pipeline_cost(3, strategy="auto")
        stage_names = {s.stage for s in result.stages}
        assert stage_names == {"planner", "contract_builder", "agent", "reviewer", "ci_fix"}

    async def test_total_cost_positive(self):
        result = await estimate_pipeline_cost(3, strategy="auto")
        assert result.total_cost_usd > 0

    async def test_scales_with_task_count(self):
        result_3 = await estimate_pipeline_cost(3, strategy="auto")
        result_6 = await estimate_pipeline_cost(6, strategy="auto")
        assert result_6.total_cost_usd > result_3.total_cost_usd

    async def test_fast_cheaper_than_quality(self):
        fast = await estimate_pipeline_cost(5, strategy="fast")
        quality = await estimate_pipeline_cost(5, strategy="quality")
        assert fast.total_cost_usd < quality.total_cost_usd

    async def test_single_task_pipeline(self):
        result = await estimate_pipeline_cost(1, strategy="auto")
        assert result.total_cost_usd > 0

    async def test_zero_tasks(self):
        result = await estimate_pipeline_cost(0, strategy="auto")
        # Still has planner, contract_builder, ci_fix with multiplier=1
        # and agent/reviewer with max(0,1)=1
        assert result.total_cost_usd > 0

    async def test_per_stage_model_specs(self):
        result = await estimate_pipeline_cost(2, strategy="auto")
        for stage_est in result.stages:
            assert isinstance(stage_est.model_spec, ModelSpec)

    async def test_agent_multiplied_by_task_count(self):
        result = await estimate_pipeline_cost(5, strategy="auto")
        agent_stage = next(s for s in result.stages if s.stage == "agent")
        est_input, est_output = _STAGE_TOKEN_ESTIMATES["agent"]
        assert agent_stage.estimated_input_tokens == est_input * 5
        assert agent_stage.estimated_output_tokens == est_output * 5

    async def test_custom_cost_registry(self):
        """Custom CostRegistry with different rates should change total."""
        expensive = CostRegistry(
            overrides={
                "claude:opus": ModelRates(input_per_1k=1.0, output_per_1k=5.0),
                "claude:sonnet": ModelRates(input_per_1k=0.5, output_per_1k=2.5),
            }
        )
        default = CostRegistry()
        result_expensive = await estimate_pipeline_cost(3, cost_registry=expensive)
        result_default = await estimate_pipeline_cost(3, cost_registry=default)
        assert result_expensive.total_cost_usd > result_default.total_cost_usd

    async def test_overrides_affect_model_selection(self):
        result = await estimate_pipeline_cost(
            1,
            strategy="auto",
            overrides={"planner_model": "haiku"},
        )
        planner = next(s for s in result.stages if s.stage == "planner")
        assert planner.model_spec == ModelSpec("claude", "haiku")
