"""Tests for .forge/forge.toml project configuration."""

from __future__ import annotations


import pytest

from forge.config.project_config import (
    CMD_DISABLED,
    DEFAULT_FORGE_TOML,
    AgentConfig,
    CheckConfig,
    IntegrationCheckConfig,
    ProjectConfig,
    apply_project_config,
)


class TestProjectConfigDefaults:
    def test_default_config_values(self):
        config = ProjectConfig()
        assert config.lint.enabled is True
        assert config.tests.enabled is True
        assert config.build.enabled is False
        assert config.review.enabled is True
        assert config.review.max_retries == 3
        assert config.agents.max_parallel == 5
        assert config.agents.max_turns == 75
        assert config.agents.model == "sonnet"
        assert config.agents.autonomy == "balanced"
        assert config.instructions == ""

    def test_default_toml_is_parseable(self, tmp_path):
        """The DEFAULT_FORGE_TOML template must be valid TOML."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(DEFAULT_FORGE_TOML)
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.lint.enabled is True
        assert config.tests.enabled is False  # Off by default — user must opt in
        assert config.build.enabled is False
        assert config.review.max_retries == 3
        assert config.agents.max_turns == 75


class TestProjectConfigFromToml:
    def test_missing_file_returns_defaults(self):
        config = ProjectConfig.from_toml("/nonexistent/forge.toml")
        assert config.lint.enabled is True
        assert config.agents.max_turns == 75

    def test_invalid_toml_returns_defaults(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text("this is not valid toml [[[")
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.lint.enabled is True

    def test_partial_config_fills_defaults(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text('[agents]\nmax_turns = 40\n')
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.agents.max_turns == 40
        assert config.agents.model == "sonnet"  # default
        assert config.lint.enabled is True  # default

    def test_disable_tests(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text('[checks.tests]\nenabled = false\n')
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.tests.enabled is False

    def test_disable_lint(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text('[checks.lint]\nenabled = false\n')
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.lint.enabled is False

    def test_custom_commands(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            '[checks.lint]\nfix_cmd = "eslint --fix ."\ncheck_cmd = "eslint ."\n'
            '[checks.tests]\ncmd = "npm test"\n'
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.lint.fix_cmd == "eslint --fix ."
        assert config.lint.check_cmd == "eslint ."
        assert config.tests.cmd == "npm test"

    def test_instructions_text(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            '[instructions]\ntext = "Always use type hints."\n'
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.instructions == "Always use type hints."

    def test_instructions_multiline(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            '[instructions]\ntext = """\nLine 1\nLine 2\n"""\n'
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert "Line 1" in config.instructions
        assert "Line 2" in config.instructions


class TestProjectConfigLoad:
    def test_load_from_project_dir(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "forge.toml").write_text('[agents]\nmax_turns = 50\n')
        config = ProjectConfig.load(str(tmp_path))
        assert config.agents.max_turns == 50

    def test_load_missing_forge_dir(self, tmp_path):
        config = ProjectConfig.load(str(tmp_path))
        assert config.agents.max_turns == 75  # default


class TestApplyProjectConfig:
    def test_applies_agent_settings(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.agents.max_turns = 40
        config.agents.max_parallel = 8
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.agent_max_turns == 40
        assert settings.max_agents == 8

    def test_env_var_wins_over_toml(self, monkeypatch):
        from forge.config.settings import ForgeSettings

        monkeypatch.setenv("FORGE_MAX_AGENTS", "16")
        config = ProjectConfig()
        config.agents.max_parallel = 2
        settings = ForgeSettings()
        apply_project_config(settings, config)
        # Env var should win
        assert settings.max_agents == 16

    def test_disabled_tests_sets_disabled_marker(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.tests.enabled = False
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.test_cmd == CMD_DISABLED

    def test_disabled_lint_sets_disabled_marker(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.lint.enabled = False
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.lint_cmd == CMD_DISABLED
        assert settings.lint_fix_cmd == CMD_DISABLED

    def test_custom_test_cmd(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.tests.cmd = "npm test"
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.test_cmd == "npm test"


class TestIntegrationConfig:
    """Tests for [integration] section parsing."""

    def test_integration_defaults(self):
        """No [integration] section → both checks disabled."""
        config = ProjectConfig()
        assert config.integration.post_merge.enabled is False
        assert config.integration.final_gate.enabled is False
        assert config.integration.post_merge.cmd is None
        assert config.integration.final_gate.cmd is None

    def test_integration_from_toml_defaults(self, tmp_path):
        """TOML without [integration] → both disabled."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text('[agents]\nmax_turns = 25\n')
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.integration.post_merge.enabled is False
        assert config.integration.final_gate.enabled is False

    def test_integration_post_merge_only(self, tmp_path):
        """Only [integration.post_merge] configured."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            '[integration.post_merge]\n'
            'enabled = true\n'
            'cmd = "make smoke"\n'
            'timeout_seconds = 60\n'
            'on_failure = "stop_pipeline"\n'
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.integration.post_merge.enabled is True
        assert config.integration.post_merge.cmd == "make smoke"
        assert config.integration.post_merge.timeout_seconds == 60
        assert config.integration.post_merge.on_failure == "stop_pipeline"
        # final_gate stays default
        assert config.integration.final_gate.enabled is False

    def test_integration_full(self, tmp_path):
        """Both sections with all fields."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            '[integration.post_merge]\n'
            'enabled = true\n'
            'cmd = "pytest tests/smoke/"\n'
            'timeout_seconds = 90\n'
            'on_failure = "ask"\n'
            '\n'
            '[integration.final_gate]\n'
            'enabled = true\n'
            'cmd = "pytest tests/ --tb=short"\n'
            'timeout_seconds = 300\n'
            'on_failure = "ignore_and_continue"\n'
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.integration.post_merge.enabled is True
        assert config.integration.post_merge.cmd == "pytest tests/smoke/"
        assert config.integration.post_merge.timeout_seconds == 90
        assert config.integration.final_gate.enabled is True
        assert config.integration.final_gate.cmd == "pytest tests/ --tb=short"
        assert config.integration.final_gate.timeout_seconds == 300
        assert config.integration.final_gate.on_failure == "ignore_and_continue"

    def test_integration_enabled_no_cmd(self, tmp_path):
        """enabled=true but no cmd → no error, defaults to None."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            '[integration.post_merge]\n'
            'enabled = true\n'
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.integration.post_merge.enabled is True
        assert config.integration.post_merge.cmd is None


class TestCmdDisabledConstant:
    def test_constant_value(self):
        assert CMD_DISABLED == "__DISABLED__"

    def test_disabled_tests_uses_constant(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.tests.enabled = False
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.test_cmd is CMD_DISABLED

    def test_disabled_lint_uses_constant(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.lint.enabled = False
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.lint_cmd is CMD_DISABLED
        assert settings.lint_fix_cmd is CMD_DISABLED


class TestCheckConfigValidation:
    def test_valid_scope_values(self):
        for scope in ("changed", "all", "none"):
            c = CheckConfig(scope=scope)
            assert c.scope == scope

    def test_invalid_scope_raises(self):
        with pytest.raises(ValueError, match="scope must be"):
            CheckConfig(scope="invalid")


class TestAgentConfigValidation:
    def test_valid_model_values(self):
        for model in ("sonnet", "opus", "haiku"):
            a = AgentConfig(model=model)
            assert a.model == model

    def test_invalid_model_raises(self):
        with pytest.raises(ValueError, match="model must be"):
            AgentConfig(model="gpt-4")

    def test_valid_autonomy_values(self):
        for autonomy in ("full", "balanced", "supervised"):
            a = AgentConfig(autonomy=autonomy)
            assert a.autonomy == autonomy

    def test_invalid_autonomy_raises(self):
        with pytest.raises(ValueError, match="autonomy must be"):
            AgentConfig(autonomy="yolo")


class TestIntegrationCheckConfigValidation:
    def test_valid_on_failure_values(self):
        for val in ("ask", "ignore_and_continue", "stop_pipeline"):
            c = IntegrationCheckConfig(on_failure=val)
            assert c.on_failure == val

    def test_invalid_on_failure_raises(self):
        with pytest.raises(ValueError, match="on_failure must be"):
            IntegrationCheckConfig(on_failure="crash")
