"""Tests for model routing by complexity and pipeline stage."""

import logging
from unittest.mock import MagicMock

from forge.core.model_router import (
    _ESCALATION_CHAINS,
    _PROVIDER_TIER_MAP,
    _ROUTING_TABLE,
    select_model,
    translate_to_provider,
)
from forge.providers.base import ModelSpec


class TestSelectModel:
    def test_auto_low_agent(self):
        assert select_model("auto", "agent", "low") == ModelSpec("claude", "sonnet")

    def test_auto_medium_agent(self):
        assert select_model("auto", "agent", "medium") == ModelSpec("claude", "opus")

    def test_auto_high_agent(self):
        assert select_model("auto", "agent", "high") == ModelSpec("claude", "opus")

    def test_auto_planner_always_opus(self):
        assert select_model("auto", "planner", "low") == ModelSpec("claude", "opus")
        assert select_model("auto", "planner", "high") == ModelSpec("claude", "opus")

    def test_auto_reviewer_low(self):
        assert select_model("auto", "reviewer", "low") == ModelSpec("claude", "sonnet")

    def test_auto_reviewer_high(self):
        assert select_model("auto", "reviewer", "high") == ModelSpec("claude", "sonnet")

    def test_fast_strategy(self):
        assert select_model("fast", "agent", "high") == ModelSpec("claude", "haiku")
        assert select_model("fast", "planner", "high") == ModelSpec("claude", "sonnet")
        assert select_model("fast", "reviewer", "high") == ModelSpec("claude", "sonnet")

    def test_quality_strategy(self):
        assert select_model("quality", "agent", "low") == ModelSpec("claude", "opus")
        assert select_model("quality", "planner", "low") == ModelSpec("claude", "opus")
        assert select_model("quality", "reviewer", "low") == ModelSpec("claude", "sonnet")

    def test_unknown_strategy_defaults_to_auto(self):
        assert select_model("unknown", "agent", "low") == ModelSpec("claude", "sonnet")

    def test_returns_model_spec(self):
        result = select_model("auto", "agent", "low")
        assert isinstance(result, ModelSpec)
        assert result.provider == "claude"
        assert result.model == "sonnet"

    def test_ci_fix_stage_auto(self):
        result = select_model("auto", "ci_fix", "medium")
        assert result == ModelSpec("claude", "sonnet")

    def test_ci_fix_stage_fast(self):
        result = select_model("fast", "ci_fix", "low")
        assert result == ModelSpec("claude", "haiku")

    def test_ci_fix_stage_quality(self):
        result = select_model("quality", "ci_fix", "high")
        assert result == ModelSpec("claude", "opus")

    def test_ci_fix_in_all_strategies(self):
        for strategy in ("auto", "fast", "quality"):
            result = select_model(strategy, "ci_fix", "medium")
            assert isinstance(result, ModelSpec)


class TestSelectModelOverrides:
    def test_override_planner_model(self):
        result = select_model("auto", "planner", "high", overrides={"planner_model": "haiku"})
        assert result == ModelSpec("claude", "haiku")

    def test_override_reviewer_model(self):
        result = select_model("auto", "reviewer", "low", overrides={"reviewer_model": "opus"})
        assert result == ModelSpec("claude", "opus")

    def test_override_agent_model_by_complexity(self):
        result = select_model("auto", "agent", "low", overrides={"agent_model_low": "opus"})
        assert result == ModelSpec("claude", "opus")

    def test_override_agent_model_medium(self):
        result = select_model("auto", "agent", "medium", overrides={"agent_model_medium": "haiku"})
        assert result == ModelSpec("claude", "haiku")

    def test_override_agent_model_high(self):
        result = select_model("auto", "agent", "high", overrides={"agent_model_high": "sonnet"})
        assert result == ModelSpec("claude", "sonnet")

    def test_override_contract_builder(self):
        result = select_model(
            "auto",
            "contract_builder",
            "medium",
            overrides={"contract_builder_model": "sonnet"},
        )
        assert result == ModelSpec("claude", "sonnet")

    def test_override_ci_fix(self):
        result = select_model("auto", "ci_fix", "medium", overrides={"ci_fix_model": "opus"})
        assert result == ModelSpec("claude", "opus")

    def test_override_with_provider_prefix(self):
        result = select_model(
            "auto", "planner", "high", overrides={"planner_model": "openai:gpt-5.4"}
        )
        assert result == ModelSpec("openai", "gpt-5.4")

    def test_no_matching_override_falls_through(self):
        result = select_model("auto", "agent", "low", overrides={"planner_model": "haiku"})
        assert result == ModelSpec("claude", "sonnet")

    def test_none_overrides_ignored(self):
        result = select_model("auto", "planner", "high", overrides=None)
        assert result == ModelSpec("claude", "opus")

    def test_empty_overrides_ignored(self):
        result = select_model("auto", "planner", "high", overrides={})
        assert result == ModelSpec("claude", "opus")


class TestModelEscalation:
    """Model escalation on retry 2+ for agent stage."""

    def test_no_escalation_retry_0(self):
        assert select_model("auto", "agent", "low", retry_count=0) == ModelSpec("claude", "sonnet")

    def test_no_escalation_retry_1(self):
        assert select_model("auto", "agent", "low", retry_count=1) == ModelSpec("claude", "sonnet")

    def test_escalation_retry_2_sonnet_to_opus(self):
        assert select_model("auto", "agent", "low", retry_count=2) == ModelSpec("claude", "opus")

    def test_escalation_retry_2_haiku_to_sonnet(self):
        assert select_model("fast", "agent", "high", retry_count=2) == ModelSpec("claude", "sonnet")

    def test_no_escalation_already_opus(self):
        assert select_model("quality", "agent", "low", retry_count=2) == ModelSpec("claude", "opus")

    def test_no_escalation_for_reviewer(self):
        assert select_model("auto", "reviewer", "low", retry_count=5) == ModelSpec(
            "claude", "sonnet"
        )

    def test_no_escalation_for_planner(self):
        assert select_model("auto", "planner", "low", retry_count=5) == ModelSpec("claude", "opus")

    def test_escalation_applies_to_overrides(self):
        result = select_model(
            "auto",
            "agent",
            "low",
            overrides={"agent_model_low": "haiku"},
            retry_count=2,
        )
        assert result == ModelSpec("claude", "sonnet")

    def test_escalation_retry_3_same_as_2(self):
        assert select_model("auto", "agent", "low", retry_count=3) == ModelSpec("claude", "opus")

    def test_escalation_stays_intra_provider_claude(self):
        """Escalation from claude:sonnet stays within claude provider."""
        result = select_model("auto", "agent", "low", retry_count=2)
        assert result.provider == "claude"
        assert result.model == "opus"

    def test_escalation_stays_intra_provider_openai(self):
        """Escalation from openai model stays within openai provider."""
        result = select_model(
            "auto",
            "agent",
            "low",
            overrides={"agent_model_low": "openai:gpt-5.4-mini"},
            retry_count=2,
        )
        assert result == ModelSpec("openai", "gpt-5.4")

    def test_escalation_openai_nano_to_mini(self):
        result = select_model(
            "auto",
            "agent",
            "low",
            overrides={"agent_model_low": "openai:gpt-5.4-nano"},
            retry_count=2,
        )
        assert result == ModelSpec("openai", "gpt-5.4-mini")

    def test_no_escalation_for_ci_fix(self):
        result = select_model("auto", "ci_fix", "medium", retry_count=5)
        assert result == ModelSpec("claude", "sonnet")


class TestEscalationChains:
    def test_claude_chains(self):
        assert _ESCALATION_CHAINS["claude"] == {"haiku": "sonnet", "sonnet": "opus"}

    def test_openai_chains(self):
        assert _ESCALATION_CHAINS["openai"] == {
            "gpt-5.4-nano": "gpt-5.4-mini",
            "gpt-5.4-mini": "gpt-5.4",
        }


class TestProviderTierMap:
    def test_claude_tiers(self):
        assert _PROVIDER_TIER_MAP["claude"]["high"] == "claude:opus"
        assert _PROVIDER_TIER_MAP["claude"]["medium"] == "claude:sonnet"
        assert _PROVIDER_TIER_MAP["claude"]["low"] == "claude:haiku"

    def test_openai_tiers(self):
        assert _PROVIDER_TIER_MAP["openai"]["high"] == "openai:gpt-5.4"
        assert _PROVIDER_TIER_MAP["openai"]["medium"] == "openai:gpt-5.4-mini"
        assert _PROVIDER_TIER_MAP["openai"]["low"] == "openai:gpt-5.4-nano"


class TestTranslateToProvider:
    def test_translate_to_openai(self):
        translated = translate_to_provider("openai")
        # auto/planner/medium was claude:opus (high) -> openai:gpt-5.4
        assert translated["auto"]["planner"]["medium"] == "openai:gpt-5.4"
        # auto/agent/low was claude:sonnet (medium) -> openai:gpt-5.4-mini
        assert translated["auto"]["agent"]["low"] == "openai:gpt-5.4-mini"
        # fast/agent/low was claude:haiku (low) -> openai:gpt-5.4-nano
        assert translated["fast"]["agent"]["low"] == "openai:gpt-5.4-nano"

    def test_translate_unknown_provider_returns_original(self):
        translated = translate_to_provider("unknown_provider")
        assert translated == _ROUTING_TABLE


class TestOverridePrecedence:
    def test_overrides_beat_routing_table(self):
        """CLI per-stage overrides take priority over the routing table."""
        result = select_model(
            "quality",
            "planner",
            "high",
            overrides={"planner_model": "haiku"},
        )
        assert result == ModelSpec("claude", "haiku")

    def test_custom_routing_table(self):
        """Custom routing_table parameter overrides default table."""
        custom_table = {
            "auto": {
                "planner": {
                    "low": "claude:haiku",
                    "medium": "claude:haiku",
                    "high": "claude:haiku",
                },
                "agent": {"low": "claude:haiku", "medium": "claude:haiku", "high": "claude:haiku"},
            }
        }
        result = select_model("auto", "planner", "medium", routing_table=custom_table)
        assert result == ModelSpec("claude", "haiku")

    def test_overrides_beat_custom_routing_table(self):
        """Overrides still win even with a custom routing table."""
        custom_table = {
            "auto": {
                "planner": {
                    "low": "claude:haiku",
                    "medium": "claude:haiku",
                    "high": "claude:haiku",
                },
                "agent": {"low": "claude:haiku", "medium": "claude:haiku", "high": "claude:haiku"},
            }
        }
        result = select_model(
            "auto",
            "planner",
            "medium",
            overrides={"planner_model": "opus"},
            routing_table=custom_table,
        )
        assert result == ModelSpec("claude", "opus")


class TestRoutingTableFormat:
    def test_all_entries_are_provider_colon_model(self):
        """All routing table entries use 'provider:model' format."""
        for strategy, stages in _ROUTING_TABLE.items():
            for stage, complexities in stages.items():
                for complexity, raw in complexities.items():
                    assert ":" in raw, (
                        f"Entry {strategy}/{stage}/{complexity}={raw!r} missing 'provider:' prefix"
                    )
                    spec = ModelSpec.parse(raw)
                    assert spec.provider in ("claude", "openai")

    def test_ci_fix_in_all_strategies(self):
        for strategy in _ROUTING_TABLE:
            assert "ci_fix" in _ROUTING_TABLE[strategy], (
                f"ci_fix stage missing from {strategy} strategy"
            )


class TestRegistryValidation:
    def test_validation_called_when_registry_provided(self):
        registry = MagicMock()
        registry.validate_model_for_stage.return_value = []
        select_model("auto", "agent", "low", registry=registry)
        registry.validate_model_for_stage.assert_called_once_with(
            ModelSpec("claude", "sonnet"), "agent"
        )

    def test_validation_warnings_logged(self, caplog):
        registry = MagicMock()
        registry.validate_model_for_stage.return_value = [
            "WARNING: sonnet is not validated for test_stage"
        ]
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            select_model("auto", "agent", "low", registry=registry)
        assert "WARNING:" in caplog.text

    def test_no_validation_when_registry_none(self):
        # Should not raise
        select_model("auto", "agent", "low", registry=None)


class TestSelectModelFallbackLogging:
    def test_unknown_strategy_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("nonexistent", "agent", "medium")
        assert result == ModelSpec("claude", "opus")
        assert "Unknown model_strategy 'nonexistent'" in caplog.text

    def test_unknown_stage_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("auto", "nonexistent_stage", "low")
        assert result == ModelSpec("claude", "sonnet")
        assert "Unknown stage 'nonexistent_stage'" in caplog.text

    def test_unknown_complexity_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("auto", "agent", "extreme")
        assert result == ModelSpec("claude", "opus")  # falls back to medium
        assert "Unknown complexity 'extreme'" in caplog.text

    def test_known_values_no_warnings(self, caplog):
        with caplog.at_level(logging.WARNING, logger="forge.model_router"):
            result = select_model("auto", "agent", "medium")
        assert result == ModelSpec("claude", "opus")
        assert caplog.text == ""
