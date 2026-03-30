"""Tests for .forge/forge.toml project configuration."""

from __future__ import annotations

import os
import subprocess

import click
import pytest

from forge.config.project_config import (
    CMD_DISABLED,
    DEFAULT_FORGE_TOML,
    AgentConfig,
    CheckConfig,
    IntegrationCheckConfig,
    ProjectConfig,
    ReviewConfig,
    apply_project_config,
    auto_detect_base_branch,
    load_repo_configs,
    load_workspace_toml,
    parse_repo_flags,
    resolve_repos,
    validate_repos_startup,
)
from forge.core.models import RepoConfig


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
        toml_path.write_text("[agents]\nmax_turns = 40\n")
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.agents.max_turns == 40
        assert config.agents.model == "sonnet"  # default
        assert config.lint.enabled is True  # default

    def test_disable_tests(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text("[checks.tests]\nenabled = false\n")
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.tests.enabled is False

    def test_disable_lint(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text("[checks.lint]\nenabled = false\n")
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
        toml_path.write_text('[instructions]\ntext = "Always use type hints."\n')
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.instructions == "Always use type hints."

    def test_instructions_multiline(self, tmp_path):
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text('[instructions]\ntext = """\nLine 1\nLine 2\n"""\n')
        config = ProjectConfig.from_toml(str(toml_path))
        assert "Line 1" in config.instructions
        assert "Line 2" in config.instructions


class TestProjectConfigLoad:
    def test_load_from_project_dir(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "forge.toml").write_text("[agents]\nmax_turns = 50\n")
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
        toml_path.write_text("[agents]\nmax_turns = 25\n")
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.integration.post_merge.enabled is False
        assert config.integration.final_gate.enabled is False

    def test_integration_post_merge_only(self, tmp_path):
        """Only [integration.post_merge] configured."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            "[integration.post_merge]\n"
            "enabled = true\n"
            'cmd = "make smoke"\n'
            "timeout_seconds = 60\n"
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
            "[integration.post_merge]\n"
            "enabled = true\n"
            'cmd = "pytest tests/smoke/"\n'
            "timeout_seconds = 90\n"
            'on_failure = "ask"\n'
            "\n"
            "[integration.final_gate]\n"
            "enabled = true\n"
            'cmd = "pytest tests/ --tb=short"\n'
            "timeout_seconds = 300\n"
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
        toml_path.write_text("[integration.post_merge]\nenabled = true\n")
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

    def test_invalid_autonomy_defaults_to_full(self):
        """Invalid autonomy value is silently corrected to 'full' instead of raising."""
        a = AgentConfig(autonomy="yolo")
        assert a.autonomy == "full"

    def test_invalid_autonomy_ask_defaults_to_full(self):
        a = AgentConfig(autonomy="ask")
        assert a.autonomy == "full"

    # ── Numeric clamping ──────────────────────────────────────────────

    def test_max_parallel_clamped_to_one(self):
        """max_parallel < 1 is clamped to 1."""
        assert AgentConfig(max_parallel=0).max_parallel == 1
        assert AgentConfig(max_parallel=-5).max_parallel == 1

    def test_max_parallel_valid_unchanged(self):
        assert AgentConfig(max_parallel=4).max_parallel == 4

    def test_max_turns_clamped_to_one(self):
        """max_turns < 1 is clamped to 1."""
        assert AgentConfig(max_turns=0).max_turns == 1
        assert AgentConfig(max_turns=-10).max_turns == 1

    def test_max_turns_valid_unchanged(self):
        assert AgentConfig(max_turns=50).max_turns == 50

    def test_timeout_seconds_clamped_to_thirty(self):
        """timeout_seconds < 30 is clamped to 30."""
        assert AgentConfig(timeout_seconds=0).timeout_seconds == 30
        assert AgentConfig(timeout_seconds=10).timeout_seconds == 30
        assert AgentConfig(timeout_seconds=29).timeout_seconds == 30

    def test_timeout_seconds_at_boundary(self):
        """timeout_seconds == 30 is accepted unchanged."""
        assert AgentConfig(timeout_seconds=30).timeout_seconds == 30

    def test_timeout_seconds_valid_unchanged(self):
        assert AgentConfig(timeout_seconds=600).timeout_seconds == 600


class TestReviewConfigValidation:
    def test_valid_max_retries_unchanged(self):
        assert ReviewConfig(max_retries=3).max_retries == 3
        assert ReviewConfig(max_retries=0).max_retries == 0

    def test_negative_max_retries_clamped_to_zero(self):
        """max_retries < 0 is clamped to 0."""
        assert ReviewConfig(max_retries=-1).max_retries == 0
        assert ReviewConfig(max_retries=-99).max_retries == 0

    def test_review_config_new_defaults(self):
        """New adaptive review fields have correct defaults."""
        cfg = ReviewConfig()
        assert cfg.adaptive_review is True
        assert cfg.medium_diff_threshold == 400
        assert cfg.large_diff_threshold == 2000
        assert cfg.max_chunk_lines == 600

    def test_review_config_clamps_large_threshold(self):
        """large_diff_threshold is always > medium_diff_threshold after __post_init__."""
        cfg = ReviewConfig(medium_diff_threshold=1000, large_diff_threshold=500)
        assert cfg.large_diff_threshold > cfg.medium_diff_threshold

    def test_review_config_clamps_chunk_lines(self):
        """max_chunk_lines is at least 50."""
        cfg = ReviewConfig(max_chunk_lines=5)
        assert cfg.max_chunk_lines == 50

    def test_review_config_parses_new_fields_from_toml(self, tmp_path):
        """New adaptive fields are parsed from forge.toml."""
        toml_path = tmp_path / "forge.toml"
        toml_path.write_text(
            "[review]\n"
            "adaptive_review = false\n"
            "medium_diff_threshold = 300\n"
            "large_diff_threshold = 1500\n"
            "max_chunk_lines = 800\n"
        )
        config = ProjectConfig.from_toml(str(toml_path))
        assert config.review.adaptive_review is False
        assert config.review.medium_diff_threshold == 300
        assert config.review.large_diff_threshold == 1500
        assert config.review.max_chunk_lines == 800


class TestIntegrationCheckConfigValidation:
    def test_valid_on_failure_values(self):
        for val in ("ask", "ignore_and_continue", "stop_pipeline"):
            c = IntegrationCheckConfig(on_failure=val)
            assert c.on_failure == val

    def test_invalid_on_failure_raises(self):
        with pytest.raises(ValueError, match="on_failure must be"):
            IntegrationCheckConfig(on_failure="crash")

    def test_timeout_seconds_clamped_to_one(self):
        """timeout_seconds < 1 is clamped to 1."""
        assert IntegrationCheckConfig(timeout_seconds=0).timeout_seconds == 1
        assert IntegrationCheckConfig(timeout_seconds=-5).timeout_seconds == 1

    def test_timeout_seconds_valid_unchanged(self):
        assert IntegrationCheckConfig(timeout_seconds=120).timeout_seconds == 120


# ── Helpers for git-based tests ──────────────────────────────────────


def _make_git_repo(path: str, branch: str = "main") -> None:
    """Create a minimal git repo at *path* with one commit on *branch*."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-b", branch], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    # Create an initial commit so HEAD exists
    dummy = os.path.join(path, "README.md")
    with open(dummy, "w") as f:
        f.write("# test\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


# ── Chunk 1: parse_repo_flags & auto_detect_base_branch ─────────────


class TestAutoDetectBaseBranch:
    def test_detects_main(self, tmp_path):
        repo = str(tmp_path / "repo")
        _make_git_repo(repo, branch="main")
        assert auto_detect_base_branch(repo) == "main"


class TestParseRepoFlags:
    def test_valid_single_repo(self, tmp_path):
        repo = str(tmp_path / "backend")
        _make_git_repo(repo)
        result = parse_repo_flags(("backend=" + repo,), str(tmp_path))
        assert len(result) == 1
        assert result[0].id == "backend"
        assert result[0].path == repo
        assert result[0].base_branch == "main"

    def test_valid_multiple_repos(self, tmp_path):
        be = str(tmp_path / "backend")
        fe = str(tmp_path / "frontend")
        _make_git_repo(be)
        _make_git_repo(fe)
        result = parse_repo_flags(("backend=" + be, "frontend=" + fe), str(tmp_path))
        assert len(result) == 2
        ids = {r.id for r in result}
        assert ids == {"backend", "frontend"}

    def test_invalid_id_raises(self, tmp_path):
        repo = str(tmp_path / "repo")
        _make_git_repo(repo)
        with pytest.raises(click.ClickException, match="Invalid repo id"):
            parse_repo_flags(("UPPER=" + repo,), str(tmp_path))

    def test_duplicate_id_raises(self, tmp_path):
        r1 = str(tmp_path / "a")
        r2 = str(tmp_path / "b")
        _make_git_repo(r1)
        _make_git_repo(r2)
        with pytest.raises(click.ClickException, match="Duplicate repo id"):
            parse_repo_flags(("dup=" + r1, "dup=" + r2), str(tmp_path))

    def test_duplicate_path_raises(self, tmp_path):
        repo = str(tmp_path / "repo")
        _make_git_repo(repo)
        with pytest.raises(click.ClickException, match="Duplicate repo path"):
            parse_repo_flags(("a=" + repo, "b=" + repo), str(tmp_path))

    def test_nested_paths_raises(self, tmp_path):
        parent = str(tmp_path / "parent")
        child = str(tmp_path / "parent" / "child")
        _make_git_repo(parent)
        _make_git_repo(child)
        with pytest.raises(click.ClickException, match="[Nn]ested"):
            parse_repo_flags(("parent=" + parent, "child=" + child), str(tmp_path))

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(click.ClickException, match="does not exist"):
            parse_repo_flags(("bad=/no/such/path",), str(tmp_path))

    def test_not_git_repo_raises(self, tmp_path):
        plain = str(tmp_path / "plain")
        os.makedirs(plain)
        with pytest.raises(click.ClickException, match="not a git repo"):
            parse_repo_flags(("plain=" + plain,), str(tmp_path))


# ── Chunk 2: load_workspace_toml ─────────────────────────────────────


class TestLoadWorkspaceToml:
    def test_valid_workspace(self, tmp_path):
        repo = str(tmp_path / "backend")
        _make_git_repo(repo)
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text(f'[[repos]]\nid = "backend"\npath = "{repo}"\n')
        result = load_workspace_toml(str(tmp_path))
        assert result is not None
        assert len(result) == 1
        assert result[0].id == "backend"
        assert result[0].path == repo

    def test_missing_file_returns_none(self, tmp_path):
        assert load_workspace_toml(str(tmp_path)) is None

    def test_invalid_toml_returns_none(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text("not valid toml [[[")
        assert load_workspace_toml(str(tmp_path)) is None


# ── Chunk 3: resolve_repos ───────────────────────────────────────────


class TestResolveRepos:
    def test_cli_overrides_toml(self, tmp_path):
        """CLI --repo flags take priority over workspace.toml."""
        cli_repo = str(tmp_path / "cli-repo")
        toml_repo = str(tmp_path / "toml-repo")
        _make_git_repo(cli_repo)
        _make_git_repo(toml_repo)
        # Write a workspace.toml that would normally be picked up
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text(f'[[repos]]\nid = "toml"\npath = "{toml_repo}"\n')
        result = resolve_repos(("cli=" + cli_repo,), str(tmp_path))
        assert len(result) == 1
        assert result[0].id == "cli"

    def test_toml_fallback(self, tmp_path):
        """No CLI flags → falls back to workspace.toml."""
        repo = str(tmp_path / "backend")
        _make_git_repo(repo)
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text(f'[[repos]]\nid = "backend"\npath = "{repo}"\n')
        result = resolve_repos((), str(tmp_path))
        assert len(result) == 1
        assert result[0].id == "backend"

    def test_single_repo_default(self, tmp_path):
        """No CLI flags, no workspace.toml → single-repo CWD default."""
        _make_git_repo(str(tmp_path))
        result = resolve_repos((), str(tmp_path))
        assert len(result) == 1
        assert result[0].id == "default"
        assert result[0].path == str(tmp_path)


# ── Chunk 4: validate_repos_startup ──────────────────────────────────


class TestValidateReposStartup:
    def test_gh_cli_missing_multi_repo(self, tmp_path, monkeypatch):
        """Multi-repo requires gh CLI."""
        r1 = str(tmp_path / "a")
        r2 = str(tmp_path / "b")
        _make_git_repo(r1)
        _make_git_repo(r2)
        repos = [
            RepoConfig(id="a", path=r1, base_branch="main"),
            RepoConfig(id="b", path=r2, base_branch="main"),
        ]
        monkeypatch.setattr("shutil.which", lambda _name: None)
        with pytest.raises(click.ClickException, match="gh .* not found"):
            validate_repos_startup(repos)

    def test_base_branch_missing(self, tmp_path):
        """Raises when base branch doesn't exist."""
        repo = str(tmp_path / "repo")
        _make_git_repo(repo, branch="main")
        repos = [RepoConfig(id="default", path=repo, base_branch="nonexistent")]
        with pytest.raises(click.ClickException, match="nonexistent"):
            validate_repos_startup(repos)

    def test_staged_changes_raises(self, tmp_path, monkeypatch):
        """Staged changes are rejected for non-default repos."""
        monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/gh")
        repo = str(tmp_path / "repo")
        _make_git_repo(repo)
        # Create and stage a file (but don't commit)
        with open(os.path.join(repo, "dirty.txt"), "w") as f:
            f.write("uncommitted")
        subprocess.run(["git", "add", "dirty.txt"], cwd=repo, check=True, capture_output=True)
        repos = [RepoConfig(id="myrepo", path=repo, base_branch="main")]
        with pytest.raises(click.ClickException, match="[Ss]taged"):
            validate_repos_startup(repos)


# ── Chunk 5: load_repo_configs ───────────────────────────────────────


class TestLoadRepoConfigs:
    def test_load_repo_configs_multiple(self, tmp_path):
        """Creates 2 repo dirs with distinct configs, asserts each loads correctly."""
        be = tmp_path / "backend"
        fe = tmp_path / "frontend"
        be.mkdir()
        fe.mkdir()

        # backend: pytest test command
        be_forge = be / ".forge"
        be_forge.mkdir()
        (be_forge / "forge.toml").write_text('[checks.tests]\nenabled = true\ncmd = "pytest"\n')

        # frontend: npm test command
        fe_forge = fe / ".forge"
        fe_forge.mkdir()
        (fe_forge / "forge.toml").write_text('[checks.tests]\nenabled = true\ncmd = "npm test"\n')

        repos = {
            "backend": RepoConfig(id="backend", path=str(be), base_branch="main"),
            "frontend": RepoConfig(id="frontend", path=str(fe), base_branch="main"),
        }
        result = load_repo_configs(repos)

        assert set(result.keys()) == {"backend", "frontend"}
        assert result["backend"].tests.cmd == "pytest"
        assert result["frontend"].tests.cmd == "npm test"

    def test_load_repo_configs_missing_toml(self, tmp_path):
        """Repo dir without .forge/forge.toml returns defaults (cmd=None)."""
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()

        repos = {
            "default": RepoConfig(id="default", path=str(repo_dir), base_branch="main"),
        }
        result = load_repo_configs(repos)

        assert "default" in result
        config = result["default"]
        assert config.tests.cmd is None
        assert config.lint.check_cmd is None
        assert config.build.cmd is None

    def test_load_repo_configs_invalid_toml(self, tmp_path, caplog):
        """Repo dir with invalid TOML returns defaults and logs a warning."""
        import logging

        repo_dir = tmp_path / "broken"
        repo_dir.mkdir()
        forge_dir = repo_dir / ".forge"
        forge_dir.mkdir()
        (forge_dir / "forge.toml").write_text("this is not valid toml [[[")

        repos = {
            "broken": RepoConfig(id="broken", path=str(repo_dir), base_branch="main"),
        }
        with caplog.at_level(logging.WARNING):
            result = load_repo_configs(repos)

        assert "broken" in result
        config = result["broken"]
        # Defaults returned despite invalid TOML
        assert config.tests.cmd is None
        assert config.agents.max_turns == 75
        # Warning was logged
        assert any("broken" in r.message or "forge.toml" in r.message for r in caplog.records)
