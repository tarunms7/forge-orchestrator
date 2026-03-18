"""Project-level configuration from .forge/forge.toml.

This is the user-facing config file that controls how Forge behaves
in a specific project. Created by `forge init`, edited by the user.

Priority chain: forge.toml → environment variables → ForgeSettings defaults.
forge.toml values override ForgeSettings defaults but env vars always win.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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
enabled = true
# cmd = "pytest"            # Leave commented to auto-detect
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
max_parallel = 4            # Max concurrent agents (each uses ~300-500 MB)
max_turns = 25              # Max turns per agent. Increase for complex tasks.
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


@dataclass
class ReviewConfig:
    """Configuration for LLM code review."""
    enabled: bool = True
    max_retries: int = 3


@dataclass
class AgentConfig:
    """Configuration for execution agents."""
    max_parallel: int = 4
    max_turns: int = 25
    model: str = "sonnet"
    autonomy: str = "balanced"
    timeout_seconds: int = 600


@dataclass
class ProjectConfig:
    """Parsed .forge/forge.toml configuration."""
    lint: CheckConfig = field(default_factory=CheckConfig)
    tests: CheckConfig = field(default_factory=lambda: CheckConfig(scope="changed"))
    build: CheckConfig = field(default_factory=lambda: CheckConfig(enabled=False))
    review: ReviewConfig = field(default_factory=ReviewConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)
    instructions: str = ""

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
            ),
            agents=AgentConfig(
                max_parallel=agents_raw.get("max_parallel", 4),
                max_turns=agents_raw.get("max_turns", 25),
                model=agents_raw.get("model", "sonnet"),
                autonomy=agents_raw.get("autonomy", "balanced"),
                timeout_seconds=agents_raw.get("timeout_seconds", 600),
            ),
            instructions=instructions_raw.get("text", "").strip(),
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

    # Disabled checks → set command to None (skip the gate)
    if not config.lint.enabled:
        settings.lint_cmd = "__DISABLED__"
        settings.lint_fix_cmd = "__DISABLED__"
    if not config.tests.enabled:
        settings.test_cmd = "__DISABLED__"
    if not config.build.enabled:
        settings.build_cmd = None
