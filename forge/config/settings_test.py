import pytest
from pydantic import ValidationError

from forge.config.settings import ForgeSettings


def test_default_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings()
    assert s.max_agents == 5
    assert s.cpu_threshold == 80.0
    assert s.memory_threshold_pct == 10.0
    assert s.agent_timeout_seconds == 600
    assert s.max_retries == 5
    assert s.db_url == f"sqlite+aiosqlite:///{tmp_path}/forge.db"
    assert s.data_dir == str(tmp_path)
    assert s.context_rotation_tokens == 80_000


def test_override_via_constructor():
    s = ForgeSettings(max_agents=2, cpu_threshold=90.0)
    assert s.max_agents == 2
    assert s.cpu_threshold == 90.0


def test_allowed_dirs_default_empty():
    s = ForgeSettings()
    assert s.allowed_dirs == []


def test_allowed_dirs_override():
    s = ForgeSettings(allowed_dirs=["/tmp/shared"])
    assert s.allowed_dirs == ["/tmp/shared"]


def test_postgres_url():
    s = ForgeSettings(db_url="postgresql+asyncpg://localhost/forge")
    assert "postgresql" in s.db_url


def test_negative_budget_raises():
    with pytest.raises(ValidationError, match="budget_limit_usd must be >= 0"):
        ForgeSettings(budget_limit_usd=-1.0)


def test_zero_cost_rate_raises():
    with pytest.raises(ValidationError, match="Cost rates must be > 0"):
        ForgeSettings(cost_rate_sonnet_input=0.0)


def test_cpu_threshold_negative_raises():
    with pytest.raises(ValidationError, match="cpu_threshold must be between 0 and 100"):
        ForgeSettings(cpu_threshold=-1.0)


def test_cpu_threshold_over_100_raises():
    with pytest.raises(ValidationError, match="cpu_threshold must be between 0 and 100"):
        ForgeSettings(cpu_threshold=200.0)


def test_max_agents_zero_raises():
    with pytest.raises(ValidationError, match="max_agents must be >= 1"):
        ForgeSettings(max_agents=0)


def test_agent_timeout_too_low_raises():
    with pytest.raises(ValidationError, match="agent_timeout_seconds must be >= 30"):
        ForgeSettings(agent_timeout_seconds=10)


def test_new_settings_defaults():
    s = ForgeSettings()
    assert s.pipeline_timeout_seconds == 3600
    assert s.contracts_required is False


def test_autonomy_default():
    s = ForgeSettings()
    assert s.autonomy == "balanced"


def test_question_limit_default():
    s = ForgeSettings()
    assert s.question_limit == 3


def test_question_timeout_default():
    s = ForgeSettings()
    assert s.question_timeout == 1800


def test_auto_pr_default():
    s = ForgeSettings()
    assert s.auto_pr is False


def test_autonomy_valid_values():
    for val in ("full", "balanced", "supervised"):
        s = ForgeSettings(autonomy=val)
        assert s.autonomy == val


def test_db_url_uses_centralized_path(tmp_path, monkeypatch):
    """db_url defaults to centralized forge_db_url() path."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings()
    expected = f"sqlite+aiosqlite:///{tmp_path}/forge.db"
    assert s.db_url == expected


def test_db_url_override(tmp_path, monkeypatch):
    """db_url can still be overridden via constructor or env var."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings(db_url="postgresql+asyncpg://localhost/forge")
    assert s.db_url == "postgresql+asyncpg://localhost/forge"


def test_data_dir_default(tmp_path, monkeypatch):
    """data_dir defaults to forge_data_dir()."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings()
    assert s.data_dir == str(tmp_path)


def test_data_dir_override(tmp_path, monkeypatch):
    """data_dir can be overridden."""
    custom = str(tmp_path / "custom")
    monkeypatch.delenv("FORGE_DATA_DIR", raising=False)
    s = ForgeSettings(data_dir=custom)
    assert s.data_dir == custom


def test_planning_mode_default():
    s = ForgeSettings()
    assert s.planning_mode == "auto"


def test_planning_mode_valid_values():
    for val in ("auto", "simple", "deep"):
        s = ForgeSettings(planning_mode=val)
        assert s.planning_mode == val


def test_planning_mode_invalid_raises():
    with pytest.raises(ValidationError, match="planning_mode must be"):
        ForgeSettings(planning_mode="invalid")


# --- model_strategy validator ---


def test_model_strategy_valid_values():
    for val in ("auto", "fast", "quality"):
        s = ForgeSettings(model_strategy=val)
        assert s.model_strategy == val


def test_model_strategy_invalid_raises():
    with pytest.raises(ValidationError, match="model_strategy must be"):
        ForgeSettings(model_strategy="turbo")


# --- autonomy validator ---


def test_autonomy_valid_values_validator():
    for val in ("full", "balanced", "supervised"):
        s = ForgeSettings(autonomy=val)
        assert s.autonomy == val


def test_autonomy_invalid_raises():
    with pytest.raises(ValidationError, match="autonomy must be"):
        ForgeSettings(autonomy="manual")


# --- agent_max_turns validator ---


def test_agent_max_turns_default():
    s = ForgeSettings()
    assert s.agent_max_turns == 75


def test_agent_max_turns_valid():
    s = ForgeSettings(agent_max_turns=1)
    assert s.agent_max_turns == 1


def test_agent_max_turns_zero_raises():
    with pytest.raises(ValidationError, match="agent_max_turns must be >= 1"):
        ForgeSettings(agent_max_turns=0)


def test_agent_max_turns_negative_raises():
    with pytest.raises(ValidationError, match="agent_max_turns must be >= 1"):
        ForgeSettings(agent_max_turns=-5)


# --- question_limit validator ---


def test_question_limit_valid_range():
    for val in (1, 5, 10):
        s = ForgeSettings(question_limit=val)
        assert s.question_limit == val


def test_question_limit_zero_raises():
    with pytest.raises(ValidationError, match="question_limit must be between 1 and 10"):
        ForgeSettings(question_limit=0)


def test_question_limit_over_max_raises():
    with pytest.raises(ValidationError, match="question_limit must be between 1 and 10"):
        ForgeSettings(question_limit=11)


# --- question_timeout validator ---


def test_question_timeout_valid_range():
    for val in (60, 1800, 7200):
        s = ForgeSettings(question_timeout=val)
        assert s.question_timeout == val


def test_question_timeout_too_low_raises():
    with pytest.raises(ValidationError, match="question_timeout must be between 60 and 7200"):
        ForgeSettings(question_timeout=59)


def test_question_timeout_too_high_raises():
    with pytest.raises(ValidationError, match="question_timeout must be between 60 and 7200"):
        ForgeSettings(question_timeout=7201)


# --- Multi-provider fields ---


class TestMultiProviderFields:
    def test_openai_enabled_default(self):
        s = ForgeSettings()
        assert s.openai_enabled is False

    def test_openai_enabled_from_env(self, monkeypatch):
        monkeypatch.setenv("FORGE_OPENAI_ENABLED", "true")
        s = ForgeSettings()
        assert s.openai_enabled is True

    def test_per_stage_model_defaults_none(self):
        s = ForgeSettings()
        assert s.planner_model is None
        assert s.agent_model_low is None
        assert s.agent_model_medium is None
        assert s.agent_model_high is None
        assert s.reviewer_model is None
        assert s.contract_builder_model is None
        assert s.ci_fix_model is None
        assert s.planner_reasoning_effort is None
        assert s.reviewer_reasoning_effort is None

    def test_per_stage_model_from_env(self, monkeypatch):
        monkeypatch.setenv("FORGE_PLANNER_MODEL", "opus")
        monkeypatch.setenv("FORGE_REVIEWER_MODEL", "openai:gpt-5.4")
        monkeypatch.setenv("FORGE_REVIEWER_REASONING_EFFORT", "high")
        s = ForgeSettings()
        assert s.planner_model == "opus"
        assert s.reviewer_model == "openai:gpt-5.4"
        assert s.reviewer_reasoning_effort == "high"

    def test_cost_rates_default_none(self):
        s = ForgeSettings()
        assert s.cost_rates is None

    def test_mixed_provider_settings_roundtrip(self):
        """Create ForgeSettings with mixed providers and verify routing/reasoning methods work correctly."""
        s = ForgeSettings(
            planner_model="claude:opus",
            reviewer_model="openai:gpt-5.4",
            reviewer_reasoning_effort="high",
        )

        # Assert build_routing_overrides() returns correct dict
        routing = s.build_routing_overrides()
        assert routing["planner_model"] == "claude:opus"
        assert routing["reviewer_model"] == "openai:gpt-5.4"

        # Assert build_reasoning_effort_overrides() includes reviewer
        reasoning = s.build_reasoning_effort_overrides()
        assert reasoning["reviewer_reasoning_effort"] == "high"

        # Assert resolve_reasoning_effort('reviewer', 'medium') == 'high'
        assert s.resolve_reasoning_effort("reviewer", "medium") == "high"


class TestBuildRoutingOverrides:
    def test_empty_when_no_models_set(self):
        s = ForgeSettings()
        assert s.build_routing_overrides() == {}

    def test_includes_set_fields(self):
        s = ForgeSettings(
            planner_model="opus",
            agent_model_low="haiku",
            ci_fix_model="sonnet",
        )
        overrides = s.build_routing_overrides()
        assert overrides == {
            "planner_model": "opus",
            "agent_model_low": "haiku",
            "ci_fix_model": "sonnet",
        }

    def test_all_fields(self):
        s = ForgeSettings(
            planner_model="opus",
            agent_model_low="haiku",
            agent_model_medium="sonnet",
            agent_model_high="opus",
            reviewer_model="sonnet",
            contract_builder_model="opus",
            ci_fix_model="sonnet",
        )
        overrides = s.build_routing_overrides()
        assert len(overrides) == 7

    def test_accepts_provider_prefix(self):
        s = ForgeSettings(planner_model="openai:gpt-5.4")
        overrides = s.build_routing_overrides()
        assert overrides["planner_model"] == "openai:gpt-5.4"


class TestBuildReasoningEffortOverrides:
    def test_empty_when_no_effort_set(self):
        s = ForgeSettings()
        assert s.build_reasoning_effort_overrides() == {}

    def test_includes_set_fields(self):
        s = ForgeSettings(
            planner_reasoning_effort="high",
            reviewer_reasoning_effort="low",
        )
        assert s.build_reasoning_effort_overrides() == {
            "planner_reasoning_effort": "high",
            "reviewer_reasoning_effort": "low",
        }

    def test_resolve_reasoning_effort_by_stage(self):
        s = ForgeSettings(
            planner_reasoning_effort="high",
            agent_model_medium_reasoning_effort="medium",
            reviewer_reasoning_effort="low",
        )
        assert s.resolve_reasoning_effort("planner", "high") == "high"
        assert s.resolve_reasoning_effort("agent", "medium") == "medium"
        assert s.resolve_reasoning_effort("reviewer", "low") == "low"
        assert s.resolve_reasoning_effort("ci_fix", "medium") is None


def test_invalid_reasoning_effort_raises():
    with pytest.raises(ValidationError):
        ForgeSettings(reviewer_reasoning_effort="turbo")


class TestBuildCostRegistryOverrides:
    def test_legacy_fields_migrated(self):
        s = ForgeSettings()
        overrides = s.build_cost_registry_overrides()
        assert "claude:sonnet" in overrides
        assert "claude:opus" in overrides
        assert "claude:haiku" in overrides
        assert overrides["claude:sonnet"].input_per_1k == 0.003

    def test_new_cost_rates_overlay(self):
        s = ForgeSettings(
            cost_rates={
                "openai:gpt-5.4": {"input_per_1k": 0.01, "output_per_1k": 0.03},
            }
        )
        overrides = s.build_cost_registry_overrides()
        assert "openai:gpt-5.4" in overrides
        assert overrides["openai:gpt-5.4"].input_per_1k == 0.01
        # Legacy still present
        assert "claude:sonnet" in overrides

    def test_new_rates_override_legacy(self):
        """New cost_rates should override legacy rates for the same key."""
        s = ForgeSettings(
            cost_rates={
                "claude:sonnet": {"input_per_1k": 0.005, "output_per_1k": 0.025},
            }
        )
        overrides = s.build_cost_registry_overrides()
        assert overrides["claude:sonnet"].input_per_1k == 0.005
        assert overrides["claude:sonnet"].output_per_1k == 0.025
