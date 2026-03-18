"""Tests for .forge/forge.toml project configuration."""

from __future__ import annotations

import os

from forge.config.project_config import (
    DEFAULT_FORGE_TOML,
    CheckConfig,
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
        assert config.agents.max_parallel == 4
        assert config.agents.max_turns == 25
        assert config.agents.model == "sonnet"
        assert config.agents.autonomy == "balanced"
        assert config.instructions == ""

    def test_default_toml_is_parseable(self, tmp_path):
        """The DEFAULT_FORGE_TOML template must be valid TOML."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(DEFAULT_FORGE_TOML)
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.lint.enabled is True
        assert config.tests.enabled is True
        assert config.build.enabled is False
        assert config.review.max_retries == 3
        assert config.agents.max_turns == 25


class TestProjectConfigFromToml:
    def test_missing_file_returns_defaults(self):
        config = ProjectConfig.from_toml("/nonexistent/forge.toml")
        assert config.lint.enabled is True
        assert config.agents.max_turns == 25

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
        assert config.agents.max_turns == 25  # default


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
        assert settings.test_cmd == "__DISABLED__"

    def test_disabled_lint_sets_disabled_marker(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.lint.enabled = False
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.lint_cmd == "__DISABLED__"
        assert settings.lint_fix_cmd == "__DISABLED__"

    def test_custom_test_cmd(self):
        from forge.config.settings import ForgeSettings

        config = ProjectConfig()
        config.tests.cmd = "npm test"
        settings = ForgeSettings()
        apply_project_config(settings, config)
        assert settings.test_cmd == "npm test"
