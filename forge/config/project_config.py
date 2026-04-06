"""Project-level configuration from .forge/forge.toml.

This is the user-facing config file that controls how Forge behaves
in a specific project. Created by `forge init`, edited by the user.

Priority chain: forge.toml → environment variables → ForgeSettings defaults.
forge.toml values override ForgeSettings defaults but env vars always win.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.core.models import RepoConfig

logger = logging.getLogger(__name__)

# Sentinel value used to mark a check command as explicitly disabled via forge.toml.
CMD_DISABLED = "__DISABLED__"


# ── Default forge.toml content (written by `forge init`) ──────────────

DEFAULT_FORGE_TOML = '''\
# ═══════════════════════════════════════════════════════════════════════
# Forge Project Configuration
# ═══════════════════════════════════════════════════════════════════════
#
# This file controls how Forge behaves in this project.
# Edit it to match your project's needs. Forge reads it at pipeline start.
#
# Tip: commit this file to your repo so your team shares the same config.

# ── Checks ─────────────────────────────────────────────────────────────
# Agents run these BEFORE committing. Failures are fixed by the agent,
# not by the review system — so they don't waste review retries.

[checks.lint]
enabled = true
# auto_fix = true           # Auto-fix lint errors before verifying (default: true)
# fix_cmd = "ruff check --fix ."    # Leave commented to auto-detect
# check_cmd = "ruff check ."       # Leave commented to auto-detect

[checks.tests]
enabled = false             # Off by default — set your test command first
# cmd = "pytest"            # Set your test command (use absolute venv path if needed)
# scope = "changed"         # "changed" = only tests for modified files (default)
                            # "all" = run full test suite
                            # "none" = skip tests entirely

[checks.build]
enabled = false
# cmd = "npm run build"     # Set your build command if needed


# ── Review ─────────────────────────────────────────────────────────────
# LLM code review after the agent commits. This is the actual review.

[review]
enabled = true
max_retries = 3             # How many times to retry on review rejection


# ── Agents ─────────────────────────────────────────────────────────────

[agents]
max_parallel = 5            # Max concurrent agents (each uses ~300-500 MB)
max_turns = 75              # Max turns per agent. Increase for complex tasks.
model = "sonnet"            # "sonnet", "opus", "haiku"
autonomy = "balanced"       # "full" = never ask questions
                            # "balanced" = ask when <80% confident (default)
                            # "supervised" = ask about everything
timeout_seconds = 600       # Per-agent timeout in seconds


# ── Instructions ───────────────────────────────────────────────────────
# Free-text instructions injected into every agent\'s context.
# Use this for project-specific rules, patterns, and preferences.
#
# Examples:
#   - "Always use `from __future__ import annotations`"
#   - "Never add print statements — use the logger"
#   - "This is a monorepo: backend is Python, frontend is Next.js"

[instructions]
text = """\
"""


# ── Integration Health Checks ────────────────────────────────────────
# Pipeline-level validation that runs AFTER tasks merge onto the pipeline branch.
#
# These are NOT the same as [checks.*] above:
#   [checks.*]       → per-task, pre-commit gates. The AGENT runs these and fixes failures.
#   [integration.*]  → pipeline-level. Validates the COMBINED result of all merged tasks.
#
# Both sections are OFF by default. Existing pipelines are completely unaffected.
#
# Provide the FULL command including virtual environment activation if needed:
#   cmd = "source .venv/bin/activate && pytest tests/integration/ -x"
#
# If enabled = true but no cmd is set, the check is a no-op (effectively disabled).
#
# At pipeline start, Forge runs the health check on the clean base branch to capture
# a "baseline". If the baseline already fails, you choose: ignore pre-existing
# failures and continue, or cancel the pipeline.
# Comparison is exit-code only: baseline exit 0 means post-merge must also exit 0.
#
# on_failure controls what happens when the check fails:
#   "ask"                 → pause pipeline, show failure in TUI, let you choose (default)
#   "ignore_and_continue" → log warning, mark pipeline as degraded, keep going
#   "stop_pipeline"       → halt the pipeline immediately

# Runs after EACH task merges into the pipeline branch.
# Use a fast/smoke command here — it runs once per task.
[integration.post_merge]
enabled = false
# cmd = "make smoke"               # Full command (include venv if needed)
# timeout_seconds = 120            # Max seconds before the check is killed
# on_failure = "ask"               # "ask" | "ignore_and_continue" | "stop_pipeline"

# Runs ONCE after ALL tasks complete, before PR creation.
# Use your full test suite here — it only runs once.
[integration.final_gate]
enabled = false
# cmd = "pytest tests/ --tb=short" # Full command (include venv if needed)
# timeout_seconds = 300            # Max seconds before the check is killed
# on_failure = "ask"               # "ask" | "ignore_and_continue" | "stop_pipeline"

# ── CI Auto-Fix ──────────────────────────────────────────────────────
# After PR creation, watch CI checks and auto-fix failures.
# Requires: gh CLI authenticated, CI configured on the repo.
[ci_fix]
enabled = true               # Watch CI after PR creation and auto-fix failures
# max_retries = 3            # Max fix attempts before giving up (1-10)
# poll_timeout_seconds = 1800 # Max time to wait for CI checks (30 min)
# poll_interval_seconds = 30  # Base polling interval (exponential backoff)
# budget_usd = 0.0           # Budget for fix agents (0 = use pipeline budget)
# model = "sonnet"           # Model for the fix agent
'''


# ── Parsed config dataclass ───────────────────────────────────────────


@dataclass
class CheckConfig:
    """Configuration for a single pre-commit check (lint/test/build)."""

    enabled: bool = True
    cmd: str | None = None
    fix_cmd: str | None = None
    check_cmd: str | None = None
    auto_fix: bool = True
    scope: str = "changed"  # "changed", "all", "none"

    def __post_init__(self):
        if self.scope not in ("changed", "all", "none"):
            raise ValueError("scope must be 'changed', 'all', or 'none'")


@dataclass
class ReviewConfig:
    """Configuration for LLM code review."""

    enabled: bool = True
    max_retries: int = 3
    # Adaptive review scaling
    adaptive_review: bool = True  # False = always use single-pass (Tier 1)
    medium_diff_threshold: int = 400  # Lines; >= this → Tier 2 (risk-enhanced single pass)
    large_diff_threshold: int = 2000  # Lines; >= this → Tier 3 (multi-chunk map-reduce)
    max_chunk_lines: int = 600  # Max lines per chunk in Tier 3

    def __post_init__(self):
        if self.max_retries < 0:
            self.max_retries = 0
        if self.medium_diff_threshold < 1:
            self.medium_diff_threshold = 1
        if self.large_diff_threshold <= self.medium_diff_threshold:
            self.large_diff_threshold = self.medium_diff_threshold + 1
        if self.max_chunk_lines < 50:
            self.max_chunk_lines = 50


@dataclass
class AgentConfig:
    """Configuration for execution agents."""

    max_parallel: int = 5
    max_turns: int = 75
    model: str = "sonnet"
    autonomy: str = "balanced"
    timeout_seconds: int = 600

    def __post_init__(self):
        # Backward compat: accept bare aliases and 'provider:model' format.
        # Validation is deferred to ProjectConfig.validate() with a registry.
        from forge.providers.base import ModelSpec

        try:
            spec = ModelSpec.parse(self.model)
            self.model = str(spec)
        except ValueError:
            raise ValueError(f"model must be a valid model spec, got {self.model!r}")
        if self.autonomy not in ("full", "balanced", "supervised"):
            logger.warning("Invalid autonomy value %r — defaulting to 'full'", self.autonomy)
            self.autonomy = "full"
        if self.max_parallel < 1:
            self.max_parallel = 1
        if self.max_turns < 1:
            self.max_turns = 1
        if self.timeout_seconds < 30:
            self.timeout_seconds = 30


@dataclass
class IntegrationCheckConfig:
    """Config for a single integration health check (post_merge or final_gate).

    Unlike [checks.*] which are per-task agent pre-commit gates,
    integration checks validate the combined pipeline branch after merges.
    """

    enabled: bool = False
    cmd: str | None = None  # Full shell command including venv activation
    timeout_seconds: int = 120
    on_failure: str = "ask"  # "ask" | "ignore_and_continue" | "stop_pipeline"

    def __post_init__(self):
        if self.on_failure not in ("ask", "ignore_and_continue", "stop_pipeline"):
            raise ValueError("on_failure must be 'ask', 'ignore_and_continue', or 'stop_pipeline'")
        if self.timeout_seconds < 1:
            self.timeout_seconds = 1


@dataclass
class IntegrationConfig:
    """Parsed [integration] config from forge.toml."""

    post_merge: IntegrationCheckConfig = field(default_factory=IntegrationCheckConfig)
    final_gate: IntegrationCheckConfig = field(default_factory=IntegrationCheckConfig)


@dataclass
class RoutingConfig:
    """Configuration for per-stage model routing from [routing] section."""

    planner: str | None = None
    agent_low: str | None = None
    agent_medium: str | None = None
    agent_high: str | None = None
    reviewer: str | None = None
    contract_builder: str | None = None
    ci_fix: str | None = None

    def to_overrides(self) -> dict[str, str]:
        """Convert to the overrides dict format expected by select_model()."""
        result: dict[str, str] = {}
        if self.planner is not None:
            result["planner_model"] = self.planner
        if self.agent_low is not None:
            result["agent_model_low"] = self.agent_low
        if self.agent_medium is not None:
            result["agent_model_medium"] = self.agent_medium
        if self.agent_high is not None:
            result["agent_model_high"] = self.agent_high
        if self.reviewer is not None:
            result["reviewer_model"] = self.reviewer
        if self.contract_builder is not None:
            result["contract_builder_model"] = self.contract_builder
        if self.ci_fix is not None:
            result["ci_fix_model"] = self.ci_fix
        return result


@dataclass
class CustomModelConfig:
    """Configuration for a user-provided experimental model from [[custom_models]]."""

    alias: str = ""
    provider: str = ""
    canonical_id: str = ""
    backend: str = ""

    def __post_init__(self):
        if not self.alias:
            raise ValueError("custom_models entry must have an 'alias'")
        if not self.provider:
            raise ValueError("custom_models entry must have a 'provider'")


@dataclass
class CIFixConfig:
    """Configuration for CI auto-fix after PR creation."""

    enabled: bool = True
    max_retries: int = 3
    poll_timeout_seconds: int = 1800
    poll_interval_seconds: int = 30
    budget_usd: float = 0.0
    model: str = "sonnet"

    def __post_init__(self):
        self.max_retries = max(1, min(10, self.max_retries))
        self.poll_timeout_seconds = max(60, self.poll_timeout_seconds)
        self.poll_interval_seconds = max(10, min(120, self.poll_interval_seconds))
        if self.model not in ("sonnet", "opus", "haiku"):
            self.model = "sonnet"


@dataclass
class ProjectConfig:
    """Parsed .forge/forge.toml configuration."""

    lint: CheckConfig = field(default_factory=CheckConfig)
    tests: CheckConfig = field(default_factory=lambda: CheckConfig(scope="changed"))
    build: CheckConfig = field(default_factory=lambda: CheckConfig(enabled=False))
    review: ReviewConfig = field(default_factory=ReviewConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)
    instructions: str = ""
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)
    ci_fix: CIFixConfig = field(default_factory=CIFixConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    custom_models: list[CustomModelConfig] = field(default_factory=list)

    def validate(self, registry: object) -> list[str]:
        """Validate all model references against a ProviderRegistry.

        Args:
            registry: A ProviderRegistry instance with validate_model() method.

        Returns:
            List of validation error/warning strings. Empty = all valid.
        """
        from forge.providers.base import ModelSpec

        issues: list[str] = []
        provider_names = {
            provider.name for provider in getattr(registry, "all_providers", lambda: [])()
        }

        seen_custom_specs: set[str] = set()
        for custom_model in self.custom_models:
            spec = f"{custom_model.provider}:{custom_model.alias}"
            if spec in seen_custom_specs:
                issues.append(f"custom_models duplicate alias '{spec}'")
            seen_custom_specs.add(spec)
            if provider_names and custom_model.provider not in provider_names:
                issues.append(f"custom_models entry '{spec}' uses unregistered provider")
            if not custom_model.canonical_id:
                issues.append(f"custom_models entry '{spec}' missing canonical_id")
            if not custom_model.backend:
                issues.append(f"custom_models entry '{spec}' missing backend")

        # Validate agents.model
        try:
            spec = ModelSpec.parse(self.agents.model)
            if not registry.validate_model(spec):
                issues.append(f"agents.model '{self.agents.model}' not found in catalog")
        except ValueError as e:
            issues.append(f"agents.model invalid: {e}")

        # Validate routing section
        routing_fields = {
            "routing.planner": self.routing.planner,
            "routing.agent_low": self.routing.agent_low,
            "routing.agent_medium": self.routing.agent_medium,
            "routing.agent_high": self.routing.agent_high,
            "routing.reviewer": self.routing.reviewer,
            "routing.contract_builder": self.routing.contract_builder,
            "routing.ci_fix": self.routing.ci_fix,
        }
        for field_name, value in routing_fields.items():
            if value is None:
                continue
            try:
                spec = ModelSpec.parse(value)
                if not registry.validate_model(spec):
                    issues.append(f"{field_name} '{value}' not found in catalog")
            except ValueError as e:
                issues.append(f"{field_name} invalid: {e}")

        return issues

    @classmethod
    def from_toml(cls, path: str) -> ProjectConfig:
        """Load config from a TOML file. Returns defaults if file missing/invalid."""
        if not os.path.isfile(path):
            logger.debug("No forge.toml at %s, using defaults", path)
            return cls()

        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            logger.warning("Failed to parse %s: %s — using defaults", path, e)
            return cls()

        checks = data.get("checks", {})
        lint_raw = checks.get("lint", {})
        test_raw = checks.get("tests", {})
        build_raw = checks.get("build", {})
        review_raw = data.get("review", {})
        agents_raw = data.get("agents", {})
        instructions_raw = data.get("instructions", {})
        integration_raw = data.get("integration", {})
        post_merge_raw = integration_raw.get("post_merge", {})
        final_gate_raw = integration_raw.get("final_gate", {})
        ci_fix_raw = data.get("ci_fix", {})
        routing_raw = data.get("routing", {})
        custom_models_raw = data.get("custom_models", [])

        return cls(
            lint=CheckConfig(
                enabled=lint_raw.get("enabled", True),
                fix_cmd=lint_raw.get("fix_cmd"),
                check_cmd=lint_raw.get("check_cmd"),
                auto_fix=lint_raw.get("auto_fix", True),
            ),
            tests=CheckConfig(
                enabled=test_raw.get("enabled", True),
                cmd=test_raw.get("cmd"),
                scope=test_raw.get("scope", "changed"),
            ),
            build=CheckConfig(
                enabled=build_raw.get("enabled", False),
                cmd=build_raw.get("cmd"),
            ),
            review=ReviewConfig(
                enabled=review_raw.get("enabled", True),
                max_retries=review_raw.get("max_retries", 3),
                adaptive_review=review_raw.get("adaptive_review", True),
                medium_diff_threshold=review_raw.get("medium_diff_threshold", 400),
                large_diff_threshold=review_raw.get("large_diff_threshold", 2000),
                max_chunk_lines=review_raw.get("max_chunk_lines", 600),
            ),
            agents=AgentConfig(
                max_parallel=agents_raw.get("max_parallel", 5),
                max_turns=agents_raw.get("max_turns", 75),
                model=agents_raw.get("model", "sonnet"),
                autonomy=agents_raw.get("autonomy", "balanced"),
                timeout_seconds=agents_raw.get("timeout_seconds", 600),
            ),
            instructions=instructions_raw.get("text", "").strip(),
            integration=IntegrationConfig(
                post_merge=IntegrationCheckConfig(
                    enabled=post_merge_raw.get("enabled", False),
                    cmd=post_merge_raw.get("cmd"),
                    timeout_seconds=post_merge_raw.get("timeout_seconds", 120),
                    on_failure=post_merge_raw.get("on_failure", "ask"),
                ),
                final_gate=IntegrationCheckConfig(
                    enabled=final_gate_raw.get("enabled", False),
                    cmd=final_gate_raw.get("cmd"),
                    timeout_seconds=final_gate_raw.get("timeout_seconds", 120),
                    on_failure=final_gate_raw.get("on_failure", "ask"),
                ),
            ),
            ci_fix=CIFixConfig(
                enabled=ci_fix_raw.get("enabled", True),
                max_retries=ci_fix_raw.get("max_retries", 3),
                poll_timeout_seconds=ci_fix_raw.get("poll_timeout_seconds", 1800),
                poll_interval_seconds=ci_fix_raw.get("poll_interval_seconds", 30),
                budget_usd=ci_fix_raw.get("budget_usd", 0.0),
                model=ci_fix_raw.get("model", "sonnet"),
            ),
            routing=RoutingConfig(
                planner=routing_raw.get("planner"),
                agent_low=routing_raw.get("agent_low"),
                agent_medium=routing_raw.get("agent_medium"),
                agent_high=routing_raw.get("agent_high"),
                reviewer=routing_raw.get("reviewer"),
                contract_builder=routing_raw.get("contract_builder"),
                ci_fix=routing_raw.get("ci_fix"),
            ),
            custom_models=[
                CustomModelConfig(
                    alias=cm.get("alias", ""),
                    provider=cm.get("provider", ""),
                    canonical_id=cm.get("canonical_id", ""),
                    backend=cm.get("backend", ""),
                )
                for cm in custom_models_raw
            ],
        )

    @classmethod
    def load(cls, project_dir: str) -> ProjectConfig:
        """Load forge.toml from a project directory."""
        path = os.path.join(project_dir, ".forge", "forge.toml")
        return cls.from_toml(path)


def apply_project_config(settings: object, config: ProjectConfig) -> None:
    """Apply forge.toml values to ForgeSettings where not overridden by env vars.

    forge.toml fills in settings that the user hasn't explicitly set via
    environment variables. Env vars always win.
    """
    # Only override settings that still have their default values.
    # If the user set FORGE_MAX_AGENTS=8, we don't want forge.toml to override it.
    # We detect "user set via env" by checking if the env var exists.

    env = os.environ

    if "FORGE_MAX_AGENTS" not in env:
        settings.max_agents = config.agents.max_parallel
    if "FORGE_AGENT_MAX_TURNS" not in env:
        settings.agent_max_turns = config.agents.max_turns
    if "FORGE_AGENT_TIMEOUT_SECONDS" not in env:
        settings.agent_timeout_seconds = config.agents.timeout_seconds
    if "FORGE_AUTONOMY" not in env:
        settings.autonomy = config.agents.autonomy
    if "FORGE_MAX_RETRIES" not in env:
        settings.max_retries = config.review.max_retries

    # Check commands: forge.toml values override settings defaults
    if config.lint.check_cmd and "FORGE_LINT_CMD" not in env:
        settings.lint_cmd = config.lint.check_cmd
    if config.lint.fix_cmd and "FORGE_LINT_FIX_CMD" not in env:
        settings.lint_fix_cmd = config.lint.fix_cmd
    if config.tests.cmd and "FORGE_TEST_CMD" not in env:
        settings.test_cmd = config.tests.cmd
    if config.build.cmd and "FORGE_BUILD_CMD" not in env:
        settings.build_cmd = config.build.cmd

    # CI fix settings
    if "FORGE_CI_FIX_ENABLED" not in env:
        settings.ci_fix_enabled = config.ci_fix.enabled
    if "FORGE_CI_FIX_MAX_RETRIES" not in env:
        settings.ci_fix_max_retries = config.ci_fix.max_retries
    if "FORGE_CI_FIX_BUDGET_USD" not in env:
        settings.ci_fix_budget_usd = config.ci_fix.budget_usd

    # Disabled checks → set command to None (skip the gate)
    if not config.lint.enabled:
        settings.lint_cmd = CMD_DISABLED
        settings.lint_fix_cmd = CMD_DISABLED
    if not config.tests.enabled:
        settings.test_cmd = CMD_DISABLED
    if not config.build.enabled:
        settings.build_cmd = None


def load_repo_configs(repos: dict[str, RepoConfig]) -> dict[str, ProjectConfig]:
    """Load per-repo ProjectConfig from each repo's .forge/forge.toml.

    Args:
        repos: Mapping of repo_id → RepoConfig. Each RepoConfig has .id, .path,
               and .base_branch. Keys are repo IDs like 'backend', 'frontend',
               or 'default' for single-repo.

    Returns:
        Mapping of repo_id → ProjectConfig loaded via ProjectConfig.load(rc.path).
        Missing .forge/forge.toml returns defaults. Invalid TOML logs warning
        and returns defaults.
    """
    configs: dict[str, ProjectConfig] = {}
    for repo_id, rc in repos.items():
        configs[repo_id] = ProjectConfig.load(rc.path)
    return configs


# ── Multi-repo workspace support ─────────────────────────────────────

_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@functools.lru_cache(maxsize=32)
def auto_detect_base_branch(repo_path: str) -> str:
    """Detect the base branch for a git repo.

    Always uses the CURRENT checked-out branch. If the user is on 'develop',
    Forge branches off 'develop'. If on 'main', Forge branches off 'main'.

    Falls back to 'main'/'master' only for detached HEAD or empty repos.
    """
    # 1. Current branch — this is what the user is working on
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    branch = result.stdout.strip() if result.returncode == 0 else ""
    if branch and branch != "HEAD":
        return branch

    # 2. Symbolic ref (works before first commit)
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    # 3. Last resort for truly empty/detached repos
    for candidate in ("main", "master"):
        check = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{candidate}"],
            cwd=repo_path,
            capture_output=True,
        )
        if check.returncode == 0:
            return candidate

    return "main"


def _validate_repo_list(repos: list) -> None:
    """Validate a list of RepoConfig entries.

    Checks: valid IDs, existing paths, git repos, no duplicates, no nesting.
    Raises click.ClickException on any failure.
    """
    import click

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    abs_paths: list[str] = []

    for repo in repos:
        # ID validation
        if not _REPO_ID_RE.match(repo.id):
            raise click.ClickException(
                f"Invalid repo id '{repo.id}' — must match ^[a-z0-9][a-z0-9-]*$"
            )

        # Duplicate ID
        if repo.id in seen_ids:
            raise click.ClickException(f"Duplicate repo id: '{repo.id}'")
        seen_ids.add(repo.id)

        # Path existence
        if not os.path.isdir(repo.path):
            raise click.ClickException(f"Repo '{repo.id}': path '{repo.path}' does not exist")

        # Git repo check
        git_dir = os.path.join(repo.path, ".git")
        if not os.path.exists(git_dir):
            raise click.ClickException(f"Repo '{repo.id}': '{repo.path}' is not a git repo")

        # Duplicate path
        real_path = os.path.realpath(repo.path)
        if real_path in seen_paths:
            raise click.ClickException(f"Duplicate repo path: '{repo.path}'")
        seen_paths.add(real_path)
        abs_paths.append(real_path)

    # Nesting check — no repo path should be a prefix of another
    sorted_paths = sorted(abs_paths)
    for i in range(len(sorted_paths) - 1):
        if sorted_paths[i + 1].startswith(sorted_paths[i] + os.sep):
            raise click.ClickException(
                f"Nested repo paths detected: '{sorted_paths[i]}' and '{sorted_paths[i + 1]}'"
            )


def parse_repo_flags(repo_flags: tuple[str, ...], project_dir: str) -> list:
    """Parse --repo name=path CLI flags into RepoConfig list.

    Resolves relative paths against *project_dir*, auto-detects base branch.
    Raises click.ClickException on validation errors.
    """
    import click

    from forge.core.models import RepoConfig

    repos = []
    for flag in repo_flags:
        if "=" not in flag:
            raise click.ClickException(f"Invalid --repo flag '{flag}' — expected name=path")
        repo_id, raw_path = flag.split("=", 1)

        # Resolve relative paths
        if not os.path.isabs(raw_path):
            raw_path = os.path.join(project_dir, raw_path)
        raw_path = os.path.realpath(raw_path)

        repos.append(
            RepoConfig(
                id=repo_id,
                path=raw_path,
                base_branch=auto_detect_base_branch(raw_path)
                if os.path.isdir(raw_path)
                else "main",
            )
        )

    _validate_repo_list(repos)
    return repos


def load_workspace_toml(workspace_dir: str) -> list | None:
    """Load repo definitions from .forge/workspace.toml.

    Returns a list of RepoConfig or None if the file is missing or invalid.
    """
    from forge.core.models import RepoConfig

    toml_path = os.path.join(workspace_dir, ".forge", "workspace.toml")
    if not os.path.isfile(toml_path):
        return None

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to parse %s: %s", toml_path, e)
        return None

    raw_repos = data.get("repos", [])
    if not raw_repos:
        logger.warning("No [[repos]] entries in %s", toml_path)
        return None

    repos = []
    for entry in raw_repos:
        repo_id = entry.get("id", "")
        raw_path = entry.get("path", "")

        # Resolve relative paths against workspace dir
        if not os.path.isabs(raw_path):
            raw_path = os.path.join(workspace_dir, raw_path)
        raw_path = os.path.realpath(raw_path)

        # Always auto-detect base branch from current checkout.
        # workspace.toml base_branch field is ignored — the user controls
        # the base by checking out the branch they want to work from.
        base_branch = auto_detect_base_branch(raw_path) if os.path.isdir(raw_path) else "main"

        repos.append(RepoConfig(id=repo_id, path=raw_path, base_branch=base_branch))

    return repos


def _auto_detect_repos(project_dir: str) -> list | None:
    """Auto-detect git repos in subdirectories.

    If the CWD is NOT itself a git repo but contains 2+ subdirectories
    that ARE git repos, treat it as a multi-repo workspace. Each subdirectory
    name becomes the repo id.

    Returns a list of RepoConfig or None if auto-detection doesn't apply.
    """
    from forge.core.models import RepoConfig

    # If CWD is a git repo with actual content, it's a normal single-repo — skip.
    # But if it's an empty container repo (no commits, no tracked files), it might
    # be a super-repo wrapper around real repos in subdirectories.
    if os.path.isdir(os.path.join(project_dir, ".git")):
        has_commits = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir,
            capture_output=True,
        )
        if has_commits.returncode == 0:
            # Has commits — check if it has tracked files (not just a bare wrapper)
            tracked = subprocess.run(
                ["git", "ls-files"],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            if tracked.stdout.strip():
                return None  # Real repo with tracked files — not a super-repo

    repos = []
    try:
        entries = sorted(os.listdir(project_dir))
    except OSError:
        return None

    for name in entries:
        if name.startswith("."):
            continue
        subdir = os.path.join(project_dir, name)
        if not os.path.isdir(subdir):
            continue
        if not os.path.isdir(os.path.join(subdir, ".git")):
            continue
        # This subdirectory has a .git folder — verify it's a valid repo
        repo_id = name.lower().replace(" ", "-")
        if not _REPO_ID_RE.match(repo_id):
            continue
        # Verify git can actually read this repo (catches corrupt/broken repos)
        check = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=subdir,
            capture_output=True,
        )
        if check.returncode != 0:
            logger.warning("Skipping %s: has .git but git cannot read it", name)
            continue
        base_branch = auto_detect_base_branch(subdir)
        repos.append(RepoConfig(id=repo_id, path=os.path.realpath(subdir), base_branch=base_branch))

    if len(repos) < 2:
        return None  # Need at least 2 repos for multi-repo workspace

    logger.info(
        "Auto-detected %d repos in workspace: %s",
        len(repos),
        ", ".join(r.id for r in repos),
    )

    # Write workspace.toml so the user can edit base branches later
    _write_workspace_toml(project_dir, repos)

    return repos


def _write_workspace_toml(project_dir: str, repos: list) -> None:
    """Write auto-detected repos to .forge/workspace.toml.

    Only writes if the file doesn't already exist (never overwrites user config).
    Uses relative paths so the config is portable.
    """
    forge_dir = os.path.join(project_dir, ".forge")
    toml_path = os.path.join(forge_dir, "workspace.toml")
    if os.path.exists(toml_path):
        return  # Never overwrite existing config

    os.makedirs(forge_dir, exist_ok=True)

    lines = [
        "# Auto-generated by Forge.",
        "#",
        "# Forge uses whatever branch each repo is checked out to as the base.",
        "# To exclude a repo, remove or comment out its [[repos]] block.",
        "",
    ]
    for repo in repos:
        # Use relative path if possible
        try:
            rel_path = os.path.relpath(repo.path, project_dir)
        except ValueError:
            rel_path = repo.path  # Different drive on Windows
        lines.append("[[repos]]")
        lines.append(f'id = "{repo.id}"')
        lines.append(f'path = "{rel_path}"')
        lines.append("")

    with open(toml_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def resolve_repos(repo_flags: tuple[str, ...], project_dir: str) -> list:
    """Resolve repository configurations.

    Priority: CLI flags → workspace.toml → single-repo CWD → error.
    Returns a list of RepoConfig.
    """
    from forge.core.models import RepoConfig

    # 1. CLI flags take highest priority
    if repo_flags:
        return parse_repo_flags(repo_flags, project_dir)

    # 2. workspace.toml fallback
    toml_repos = load_workspace_toml(project_dir)
    if toml_repos:
        _validate_repo_list(toml_repos)
        return toml_repos

    # 3. Auto-detect: scan subdirectories for git repos
    auto_repos = _auto_detect_repos(project_dir)
    if auto_repos:
        _validate_repo_list(auto_repos)
        return auto_repos

    # 4. Single-repo CWD default
    base_branch = (
        auto_detect_base_branch(project_dir)
        if os.path.isdir(os.path.join(project_dir, ".git"))
        else "main"
    )

    return [RepoConfig(id="default", path=project_dir, base_branch=base_branch)]


async def validate_repos_startup_async(repos: list) -> None:
    """Validate repos at startup (async version — runs subprocess checks concurrently).

    Checks:
    - gh CLI availability (multi-repo only)
    - Dirty working trees (skipped for single default repo)
    - Base branch existence

    Raises click.ClickException on any failure.
    """
    import click

    is_single_default = len(repos) == 1 and repos[0].id == "default"

    # gh CLI check for multi-repo
    if not is_single_default:
        if shutil.which("gh") is None:
            raise click.ClickException(
                "gh (GitHub CLI) not found — required for multi-repo workspaces. "
                "Install: https://cli.github.com/"
            )

    async def _validate_one(repo) -> str | None:
        """Validate a single repo. Returns error message or None."""
        if not is_single_default:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--cached",
                "--quiet",
                cwd=repo.path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode != 0:
                return (
                    f"Staged changes in repo '{repo.id}' ({repo.path}). "
                    "Commit or stash changes before running a pipeline."
                )

        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=repo.path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0:
            proc2 = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--verify",
                f"refs/heads/{repo.base_branch}",
                cwd=repo.path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc2.wait()
            if proc2.returncode != 0:
                return (
                    f"Base branch '{repo.base_branch}' not found in repo '{repo.id}' ({repo.path})"
                )
        return None

    results = await asyncio.gather(*[_validate_one(r) for r in repos])
    for err in results:
        if err is not None:
            raise click.ClickException(err)


def validate_repos_startup(repos: list) -> None:
    """Validate repos at startup (sync wrapper).

    Checks:
    - gh CLI availability (multi-repo only)
    - Dirty working trees (skipped for single default repo)
    - Base branch existence

    Raises click.ClickException on any failure.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already in an async context — fall back to sequential validation
        import click

        is_single_default = len(repos) == 1 and repos[0].id == "default"
        if not is_single_default:
            if shutil.which("gh") is None:
                raise click.ClickException(
                    "gh (GitHub CLI) not found — required for multi-repo workspaces. "
                    "Install: https://cli.github.com/"
                )
        for repo in repos:
            if not is_single_default:
                result = subprocess.run(
                    ["git", "diff", "--cached", "--quiet"],
                    cwd=repo.path,
                    capture_output=True,
                )
                if result.returncode != 0:
                    raise click.ClickException(
                        f"Staged changes in repo '{repo.id}' ({repo.path}). "
                        "Commit or stash changes before running a pipeline."
                    )
            has_commits = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo.path,
                capture_output=True,
            )
            if has_commits.returncode == 0:
                result = subprocess.run(
                    ["git", "rev-parse", "--verify", f"refs/heads/{repo.base_branch}"],
                    cwd=repo.path,
                    capture_output=True,
                )
                if result.returncode != 0:
                    raise click.ClickException(
                        f"Base branch '{repo.base_branch}' not found in repo '{repo.id}' ({repo.path})"
                    )
    else:
        asyncio.run(validate_repos_startup_async(repos))
