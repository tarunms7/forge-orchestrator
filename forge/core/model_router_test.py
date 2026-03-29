"""Tests for model routing by complexity and pipeline stage."""

import logging

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


class TestModelEscalation:
    """Model escalation on retry 2+ for agent stage."""

    def test_no_escalation_retry_0(self):
        assert select_model("auto", "agent", "low", retry_count=0) == "sonnet"

    def test_no_escalation_retry_1(self):
        assert select_model("auto", "agent", "low", retry_count=1) == "sonnet"

    def test_escalation_retry_2_sonnet_to_opus(self):
        assert select_model("auto", "agent", "low", retry_count=2) == "opus"

    def test_escalation_retry_2_haiku_to_sonnet(self):
        assert select_model("fast", "agent", "high", retry_count=2) == "sonnet"

    def test_no_escalation_already_opus(self):
        assert select_model("quality", "agent", "low", retry_count=2) == "opus"

    def test_no_escalation_for_reviewer(self):
        assert select_model("auto", "reviewer", "low", retry_count=5) == "sonnet"

    def test_no_escalation_for_planner(self):
        assert select_model("auto", "planner", "low", retry_count=5) == "opus"

    def test_escalation_applies_to_overrides(self):
        """Override selects haiku, but retry 2+ should escalate it to sonnet."""
        result = select_model(
            "auto", "agent", "low", overrides={"agent_model_low": "haiku"}, retry_count=2
        )
        assert result == "sonnet"

    def test_escalation_retry_3_same_as_2(self):
        """Escalation is capped at one tier — retry 3 doesn't escalate further."""
        assert select_model("auto", "agent", "low", retry_count=3) == "opus"


class TestSelectModelFallbackLogging:
    """Verify warning logs on unknown strategy/stage/complexity."""

    def test_unknown_strategy_logs_warning(self, caplog):
        """Unknown strategy should log a warning and fall back to 'auto'."""
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("nonexistent", "agent", "medium")
        assert result == "opus"  # auto/agent/medium = opus
        assert "Unknown model_strategy 'nonexistent'" in caplog.text

    def test_unknown_stage_logs_warning(self, caplog):
        """Unknown stage should log a warning and fall back to 'agent'."""
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("auto", "nonexistent_stage", "low")
        assert result == "sonnet"  # auto/agent/low = sonnet
        assert "Unknown stage 'nonexistent_stage'" in caplog.text

    def test_unknown_complexity_logs_warning(self, caplog):
        """Unknown complexity should log a warning and fall back to 'sonnet'."""
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("auto", "agent", "extreme")
        assert result == "sonnet"
        assert "Unknown complexity 'extreme'" in caplog.text

    def test_known_values_no_warnings(self, caplog):
        """Known values should not produce any warnings."""
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("auto", "agent", "medium")
        assert result == "opus"
        assert caplog.text == ""
