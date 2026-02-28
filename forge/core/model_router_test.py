"""Tests for model routing by complexity and pipeline stage."""

from forge.core.model_router import select_model


class TestSelectModel:
    def test_auto_low_agent(self):
        assert select_model("auto", "agent", "low") == "sonnet"

    def test_auto_medium_agent(self):
        assert select_model("auto", "agent", "medium") == "opus"

    def test_auto_high_agent(self):
        assert select_model("auto", "agent", "high") == "opus"

    def test_auto_planner_always_opus(self):
        assert select_model("auto", "planner", "low") == "opus"
        assert select_model("auto", "planner", "high") == "opus"

    def test_auto_reviewer_low(self):
        assert select_model("auto", "reviewer", "low") == "sonnet"

    def test_auto_reviewer_high(self):
        assert select_model("auto", "reviewer", "high") == "opus"

    def test_fast_strategy(self):
        assert select_model("fast", "agent", "high") == "haiku"
        assert select_model("fast", "planner", "high") == "sonnet"
        assert select_model("fast", "reviewer", "high") == "sonnet"

    def test_quality_strategy(self):
        assert select_model("quality", "agent", "low") == "opus"
        assert select_model("quality", "planner", "low") == "opus"
        assert select_model("quality", "reviewer", "low") == "opus"

    def test_unknown_strategy_defaults_to_auto(self):
        assert select_model("unknown", "agent", "low") == "sonnet"
