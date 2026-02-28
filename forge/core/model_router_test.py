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
        assert select_model("auto", "reviewer", "high") == "sonnet"

    def test_fast_strategy(self):
        assert select_model("fast", "agent", "high") == "haiku"
        assert select_model("fast", "planner", "high") == "sonnet"
        assert select_model("fast", "reviewer", "high") == "sonnet"

    def test_quality_strategy(self):
        assert select_model("quality", "agent", "low") == "opus"
        assert select_model("quality", "planner", "low") == "opus"
        assert select_model("quality", "reviewer", "low") == "sonnet"

    def test_unknown_strategy_defaults_to_auto(self):
        assert select_model("unknown", "agent", "low") == "sonnet"


class TestSelectModelOverrides:
    """Tests for the overrides parameter."""

    def test_override_planner_model(self):
        result = select_model("auto", "planner", "high", overrides={"planner_model": "haiku"})
        assert result == "haiku"

    def test_override_reviewer_model(self):
        result = select_model("auto", "reviewer", "low", overrides={"reviewer_model": "opus"})
        assert result == "opus"

    def test_override_agent_model_by_complexity(self):
        result = select_model("auto", "agent", "low", overrides={"agent_model_low": "opus"})
        assert result == "opus"

    def test_override_agent_model_medium(self):
        result = select_model("auto", "agent", "medium", overrides={"agent_model_medium": "haiku"})
        assert result == "haiku"

    def test_override_agent_model_high(self):
        result = select_model("auto", "agent", "high", overrides={"agent_model_high": "sonnet"})
        assert result == "sonnet"

    def test_no_matching_override_falls_through(self):
        """Override dict present but without a matching key should fall through to table."""
        result = select_model("auto", "agent", "low", overrides={"planner_model": "haiku"})
        assert result == "sonnet"  # default auto/agent/low

    def test_none_overrides_ignored(self):
        """overrides=None should behave like no overrides."""
        result = select_model("auto", "planner", "high", overrides=None)
        assert result == "opus"

    def test_empty_overrides_ignored(self):
        """Empty overrides dict should fall through to routing table."""
        result = select_model("auto", "planner", "high", overrides={})
        assert result == "opus"
