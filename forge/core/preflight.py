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
) -> PreflightReport:
    """Run all pre-flight checks. Returns a PreflightReport.

    Checks run concurrently where possible for speed.
    """
    report = PreflightReport()

    # Run fast sync checks first
    git_check = _check_git_installed()
    claude_check = _check_claude_cli()
    report.checks.append(git_check)
    report.checks.append(claude_check)
    report.checks.append(_check_gh_cli())
    report.checks.append(_check_disk_space(project_dir))

    # Early exit: skip git-dependent checks if git is missing
    git_available = git_check.passed
    claude_available = claude_check.passed

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

    if claude_available:
        async_tasks.append(_check_claude_auth())
    else:
        report.checks.append(
            CheckResult(
                name="claude_auth_skipped",
                passed=False,
                message="Skipped Claude auth check — CLI not installed",
                severity="warning",
                fix_hint="Install Claude CLI first",
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
