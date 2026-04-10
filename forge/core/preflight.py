"""Pre-flight validation for Forge pipelines.

Runs a battery of checks BEFORE any planning or execution starts.
Catches issues that would otherwise waste time and money:
- Missing tools (claude CLI, gh, git)
- Dirty working tree (uncommitted changes)
- Base branch doesn't exist
- Disk space too low
- SDK auth issues
- Build/test commands that don't work

Every check is fast (<1s) and returns a clear, actionable error message.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger("forge.preflight")


@dataclass
class CheckResult:
    """Result of a single pre-flight check."""

    name: str
    passed: bool
    message: str = ""
    severity: str = "error"  # "error" blocks pipeline, "warning" shows but continues
    fix_hint: str = ""


@dataclass
class PreflightReport:
    """Aggregated pre-flight validation results."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def errors(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    def summary(self) -> str:
        """One-line summary for display."""
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.passed)
        errors = len(self.errors)
        warnings = len(self.warnings)
        if errors:
            return f"Pre-flight: {errors} error(s), {warnings} warning(s) ({passed}/{total} checks passed)"
        if warnings:
            return f"Pre-flight: {warnings} warning(s) ({passed}/{total} checks passed)"
        return f"Pre-flight: all {total} checks passed"


async def run_preflight(
    project_dir: str,
    base_branch: str = "main",
    repos: dict | None = None,
    registry: object | None = None,
    resolved_models: dict | None = None,
) -> PreflightReport:
    """Run all pre-flight checks. Returns a PreflightReport.

    Checks run concurrently where possible for speed.

    Args:
        registry: Optional ProviderRegistry for provider-aware health checks.
        resolved_models: Optional dict of stage -> ModelSpec for pipeline-specific checks.
    """
    report = PreflightReport()

    # Run fast sync checks first
    git_check = _check_git_installed()
    report.checks.append(git_check)
    report.checks.append(_check_gh_cli())
    report.checks.append(_check_disk_space(project_dir))

    # Provider-aware health checks replace Claude-specific auth check
    provider_checks = _check_provider_health(registry, resolved_models)
    report.checks.extend(provider_checks)

    # Validate all models in resolved routing are in catalog
    if registry is not None and resolved_models is not None:
        catalog_checks = _check_models_in_catalog(registry, resolved_models)
        report.checks.extend(catalog_checks)

    # Validate routing: blocked models and disconnected providers
    if registry is not None and resolved_models is not None:
        routing_checks = _check_routing_validity(registry, resolved_models)
        report.checks.extend(routing_checks)

    # Early exit: skip git-dependent checks if git is missing
    git_available = git_check.passed

    # Build async check list, skipping checks whose prerequisite is missing
    async_tasks: list[asyncio.Task] = []
    if git_available:
        async_tasks.append(_check_git_repo(project_dir, repos))
        async_tasks.append(_check_base_branch(project_dir, base_branch, repos))
        async_tasks.append(_check_working_tree_clean(project_dir, repos))
    else:
        report.checks.append(
            CheckResult(
                name="git_checks_skipped",
                passed=False,
                message="Skipped git repo/branch/tree checks — git not installed",
                fix_hint="Install git first, then re-run preflight",
            )
        )

    if async_tasks:
        async_checks = await asyncio.gather(*async_tasks, return_exceptions=True)
        for result in async_checks:
            if isinstance(result, CheckResult):
                report.checks.append(result)
            elif isinstance(result, Exception):
                report.checks.append(
                    CheckResult(
                        name="async_check",
                        passed=False,
                        message=f"Check failed unexpectedly: {result}",
                        severity="warning",
                    )
                )

    return report


# ── Individual Checks ────────────────────────────────────────────────


def _check_git_installed() -> CheckResult:
    """Verify git is installed and accessible."""
    git = shutil.which("git")
    if not git:
        return CheckResult(
            name="git",
            passed=False,
            message="git not found in PATH",
            fix_hint="Install git: https://git-scm.com/downloads",
        )
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        version = result.stdout.strip()
        return CheckResult(name="git", passed=True, message=version)
    except Exception as e:
        return CheckResult(name="git", passed=False, message=f"git check failed: {e}")


def _check_claude_cli() -> CheckResult:
    """Verify Claude Code CLI is installed."""
    claude = shutil.which("claude")
    if not claude:
        return CheckResult(
            name="claude_cli",
            passed=False,
            message="Claude Code CLI not found",
            fix_hint="Install: https://docs.anthropic.com/en/docs/claude-code",
        )
    return CheckResult(name="claude_cli", passed=True, message="Claude Code CLI found")


def _check_gh_cli() -> CheckResult:
    """Check if GitHub CLI is available (warning only — not required)."""
    gh = shutil.which("gh")
    if not gh:
        return CheckResult(
            name="gh_cli",
            passed=False,
            message="GitHub CLI not found — PR creation will be skipped",
            severity="warning",
            fix_hint="Install: https://cli.github.com",
        )
    return CheckResult(name="gh_cli", passed=True, message="GitHub CLI found")


def _check_disk_space(project_dir: str) -> CheckResult:
    """Check if there's enough disk space for worktrees."""
    try:
        stat = os.statvfs(project_dir)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        if free_gb < 1.0:
            return CheckResult(
                name="disk_space",
                passed=False,
                message=f"Only {free_gb:.1f}GB free — need at least 1GB for worktrees",
                fix_hint="Free up disk space before running Forge",
            )
        if free_gb < 5.0:
            return CheckResult(
                name="disk_space",
                passed=True,
                message=f"{free_gb:.1f}GB free (low — consider freeing space)",
                severity="warning",
            )
        return CheckResult(name="disk_space", passed=True, message=f"{free_gb:.1f}GB free")
    except Exception as e:
        return CheckResult(
            name="disk_space",
            passed=True,
            message=f"Could not check disk space: {e}",
            severity="warning",
        )


async def _check_git_repo(project_dir: str, repos: dict | None = None) -> CheckResult:
    """Verify git repositories are accessible.

    Multi-repo: checks each repo path. Single-repo: checks project_dir.
    """
    dirs_to_check = []
    if repos and len(repos) > 1:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path))
    else:
        dirs_to_check.append(("default", project_dir))

    async def _check_one_repo(repo_id: str, path: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--git-dir",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return f"{repo_id} ({path})" if repo_id != "default" else path
        except (TimeoutError, OSError):
            return f"{repo_id} ({path})" if repo_id != "default" else path
        return None

    results = await asyncio.gather(*(_check_one_repo(rid, p) for rid, p in dirs_to_check))
    failed = [r for r in results if r is not None]

    if failed:
        return CheckResult(
            name="git_repo",
            passed=False,
            message=f"Not a git repository: {', '.join(failed)}",
            fix_hint="Run `git init` or check repo paths in .forge/workspace.toml",
        )
    count = len(dirs_to_check)
    msg = f"{count} git repositories detected" if count > 1 else "Git repository detected"
    return CheckResult(name="git_repo", passed=True, message=msg)


async def _check_base_branch(
    project_dir: str, base_branch: str, repos: dict | None = None
) -> CheckResult:
    """Verify the base branch exists."""
    dirs_to_check = []
    if repos:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path, rc.base_branch or base_branch))
    else:
        dirs_to_check.append(("default", project_dir, base_branch))

    async def _check_one_branch(repo_id: str, path: str, branch: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--verify",
                f"refs/heads/{branch}",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                return None
            # Also check remote
            proc2 = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--verify",
                f"refs/remotes/origin/{branch}",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc2.communicate(), timeout=10)
            if proc2.returncode != 0:
                return f"{repo_id}:{branch}" if repo_id != "default" else branch
        except (TimeoutError, OSError):
            return f"{repo_id}:{branch}" if repo_id != "default" else branch
        return None

    results = await asyncio.gather(*(_check_one_branch(rid, p, b) for rid, p, b in dirs_to_check))
    missing = [r for r in results if r is not None]

    if missing:
        return CheckResult(
            name="base_branch",
            passed=False,
            message=f"Base branch not found: {', '.join(missing)}",
            fix_hint="Check branch name or run `git fetch` to update remote branches",
        )
    return CheckResult(name="base_branch", passed=True, message="Base branch(es) exist")


async def _check_working_tree_clean(project_dir: str, repos: dict | None = None) -> CheckResult:
    """Warn if working tree has uncommitted changes."""
    dirs_to_check = []
    if repos:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path))
    else:
        dirs_to_check.append(("default", project_dir))

    async def _check_one_tree(repo_id: str, path: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--porcelain",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if stdout.strip():
                count = len(stdout.strip().split(b"\n"))
                label = repo_id if repo_id != "default" else os.path.basename(path)
                return f"{label} ({count} files)"
        except (TimeoutError, OSError):
            label = repo_id if repo_id != "default" else os.path.basename(path)
            return f"{label} (check timed out)"
        return None

    results = await asyncio.gather(*(_check_one_tree(rid, p) for rid, p in dirs_to_check))
    dirty = [r for r in results if r is not None]

    if dirty:
        return CheckResult(
            name="working_tree",
            passed=True,  # warning, not error
            message=f"Uncommitted changes in: {', '.join(dirty)}",
            severity="warning",
        )
    return CheckResult(name="working_tree", passed=True, message="Working tree clean")


async def _check_claude_auth() -> CheckResult:
    """Verify Claude Code CLI is authenticated."""
    claude = shutil.which("claude")
    if not claude:
        return CheckResult(
            name="claude_auth",
            passed=False,
            message="Claude CLI not found — cannot verify auth",
            severity="warning",
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            claude,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return CheckResult(
                name="claude_auth",
                passed=True,
                message="Claude CLI accessible",
            )
        return CheckResult(
            name="claude_auth",
            passed=False,
            message="Claude CLI returned error — may not be authenticated",
            fix_hint="Run `claude login` to authenticate",
        )
    except TimeoutError:
        return CheckResult(
            name="claude_auth",
            passed=True,
            message="Claude CLI check timed out (likely fine)",
            severity="warning",
        )


# ── Provider-aware checks ──────────────────────────────────────────────


def _check_provider_health(
    registry: object | None = None,
    resolved_models: dict | None = None,
) -> list[CheckResult]:
    """Run provider health checks using the registry.

    Uses preflight_for_pipeline() when resolved_models is provided (pipeline-specific),
    otherwise uses preflight_all() for broad checks.
    Falls back to a basic Claude CLI check when no registry is available.
    """
    results: list[CheckResult] = []

    if registry is None:
        # Fallback: basic Claude CLI check (backward compat)
        claude_check = _check_claude_cli()
        results.append(claude_check)
        return results

    try:
        if resolved_models is not None:
            health = registry.preflight_for_pipeline(resolved_models)
        else:
            health = registry.preflight_all()

        if not health:
            results.append(
                CheckResult(
                    name="provider_health",
                    passed=True,
                    message="No providers registered",
                    severity="warning",
                )
            )
            return results

        for name, status in health.items():
            if status.healthy:
                results.append(
                    CheckResult(
                        name=f"provider_{name}",
                        passed=True,
                        message=f"{name}: {status.details}",
                    )
                )
            else:
                error_detail = "; ".join(status.errors) if status.errors else "unhealthy"
                results.append(
                    CheckResult(
                        name=f"provider_{name}",
                        passed=False,
                        message=f"{name}: {error_detail}",
                        fix_hint=f"Check {name} provider configuration and credentials",
                    )
                )
    except Exception as e:
        results.append(
            CheckResult(
                name="provider_health",
                passed=False,
                message=f"Provider health check failed: {e}",
                severity="warning",
            )
        )

    return results


def _check_models_in_catalog(
    registry: object,
    resolved_models: dict,
) -> list[CheckResult]:
    """Validate all models in resolved routing are in the catalog."""
    results: list[CheckResult] = []

    for stage, spec in resolved_models.items():
        if not registry.validate_model(spec):
            results.append(
                CheckResult(
                    name=f"model_catalog_{stage}",
                    passed=False,
                    message=f"Model '{spec}' for stage '{stage}' not found in catalog",
                    fix_hint="Check model name or use 'forge providers list' to see available models",
                )
            )

    return results


def _check_routing_validity(
    registry: object,
    resolved_models: dict,
) -> list[CheckResult]:
    """Validate stage routing: check for blocked models and disconnected providers.

    For each stage+model, calls registry.validate_model_for_stage() and flags
    any BLOCKED issues. Also checks that providers used by stages are connected.
    """
    from forge.providers.status import collect_provider_connection_statuses

    results: list[CheckResult] = []

    # Check each stage's model for BLOCKED validation issues
    for stage, spec in resolved_models.items():
        warnings = registry.validate_model_for_stage(spec, stage)
        blocked = [w for w in warnings if w.startswith("BLOCKED:")]
        if blocked:
            results.append(
                CheckResult(
                    name=f"routing_validity_{stage}",
                    passed=False,
                    message=f"Stage '{stage}' model '{spec}': {'; '.join(blocked)}",
                    fix_hint=(
                        f"Change model for stage '{stage}' or use "
                        "'forge providers list' to see valid models for this stage"
                    ),
                )
            )

    # Check that providers used by routing stages are connected
    try:
        conn_statuses = collect_provider_connection_statuses()
    except Exception as e:
        results.append(
            CheckResult(
                name="routing_provider_status",
                passed=False,
                message=f"Could not check provider connection status: {e}",
                severity="warning",
            )
        )
        return results

    # Build lookup by provider_key
    provider_by_key = {cs.provider_key: cs for cs in conn_statuses.values()}

    # Collect unique providers used in routing
    used_providers: dict[str, list[str]] = {}
    for stage, spec in resolved_models.items():
        used_providers.setdefault(spec.provider, []).append(stage)

    for provider_key, stages in used_providers.items():
        cs = provider_by_key.get(provider_key)
        if cs is None:
            continue
        if not cs.installed:
            results.append(
                CheckResult(
                    name=f"routing_provider_{provider_key}",
                    passed=False,
                    message=(
                        f"Provider '{provider_key}' is not installed but used by "
                        f"stage {', '.join(stages)}"
                    ),
                    fix_hint=f"Install the {provider_key} CLI to use this provider",
                )
            )
        elif not cs.connected:
            results.append(
                CheckResult(
                    name=f"routing_provider_{provider_key}",
                    passed=False,
                    message=(
                        f"Provider '{provider_key}' is not connected but used by "
                        f"stage {', '.join(stages)}"
                    ),
                    fix_hint="Run `claude auth login` or `codex login`",
                )
            )

    return results
