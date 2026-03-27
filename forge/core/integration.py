"""Post-merge integration health checks.

Validates the combined pipeline branch after task merges.
Runs user-provided commands (build, smoke tests, full suite) and
compares exit codes against a baseline captured at pipeline start.

This module has no LLM or SDK dependencies — it's pure subprocess execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from forge.config.project_config import IntegrationCheckConfig

logger = logging.getLogger(__name__)


# ── Result dataclass ─────────────────────────────────────────────────


@dataclass
class IntegrationCheckResult:
    """Result of a single integration health check run."""

    status: str  # "passed" | "failed" | "timeout" | "infra_error" | "skipped"
    exit_code: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float
    is_regression: bool  # True only if baseline was green (exit=0) and this is red


_MAX_OUTPUT = 10_000
_HALF_OUTPUT = _MAX_OUTPUT // 2


def _truncate_output(text: str) -> str:
    """Truncate long output preserving both beginning (error context) and end (final state)."""
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_HALF_OUTPUT] + "\n...truncated...\n" + text[-_HALF_OUTPUT:]


_SKIPPED = IntegrationCheckResult(
    status="skipped",
    exit_code=None,
    stdout="",
    stderr="",
    elapsed_seconds=0.0,
    is_regression=False,
)


# ── Public helpers ───────────────────────────────────────────────────


def effective_enabled(config: IntegrationCheckConfig) -> bool:
    """Return True only if the check is enabled AND has a non-empty command."""
    return config.enabled and bool(config.cmd and config.cmd.strip())


# ── Core runner ──────────────────────────────────────────────────────


async def run_health_check(
    cmd: str,
    cwd: str,
    timeout_seconds: int = 120,
) -> IntegrationCheckResult:
    """Run a health check command and return the result.

    Uses shell=True so the user can chain commands with ``&&``,
    activate virtualenvs via ``source``, etc.

    Returns:
        IntegrationCheckResult with one of:
        - status="passed" if exit code is 0
        - status="failed" if exit code is non-zero
        - status="timeout" if the command exceeds timeout_seconds
        - status="infra_error" if the command can't be started (not found, etc.)
    """
    start = time.monotonic()
    proc = None
    try:
        async with asyncio.timeout(10):
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
        elapsed = time.monotonic() - start
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        exit_code = proc.returncode or 0

        return IntegrationCheckResult(
            status="passed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            stdout=_truncate_output(stdout),
            stderr=_truncate_output(stderr),
            elapsed_seconds=elapsed,
            is_regression=False,  # caller sets this based on baseline
        )

    except TimeoutError:
        elapsed = time.monotonic() - start
        # Kill the process if it's still running
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return IntegrationCheckResult(
            status="timeout",
            exit_code=None,
            stdout="",
            stderr=f"Command timed out after {timeout_seconds}s",
            elapsed_seconds=elapsed,
            is_regression=False,
        )

    except (FileNotFoundError, OSError, PermissionError) as exc:
        elapsed = time.monotonic() - start
        logger.warning("Integration check infra error: %s", exc)
        return IntegrationCheckResult(
            status="infra_error",
            exit_code=None,
            stdout="",
            stderr=str(exc),
            elapsed_seconds=elapsed,
            is_regression=False,
        )


# ── Temp worktree for health checks ─────────────────────────────────


@asynccontextmanager
async def _temp_health_worktree(project_dir: str, ref: str):
    """Create a temporary detached worktree for running health checks.

    Uses ``git worktree add --detach`` to avoid creating extra branches.
    Cleans up on exit regardless of success or failure.

    Yields:
        str: The absolute path to the temporary worktree.
    """
    wt_id = f"_health-check-{uuid.uuid4().hex[:8]}"
    worktree_base = os.path.join(project_dir, ".forge", "worktrees")
    os.makedirs(worktree_base, exist_ok=True)
    wt_path = os.path.join(worktree_base, wt_id)

    # Create detached worktree
    create_proc = await asyncio.create_subprocess_exec(
        "git",
        "worktree",
        "add",
        "--detach",
        wt_path,
        ref,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, create_stderr = await asyncio.wait_for(create_proc.communicate(), timeout=30)
    if create_proc.returncode != 0:
        err_msg = (
            create_stderr.decode("utf-8", errors="replace") if create_stderr else "unknown error"
        )
        raise RuntimeError(f"Failed to create health check worktree: {err_msg}")

    try:
        yield wt_path
    finally:
        # Always clean up
        try:
            remove_proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "remove",
                "--force",
                wt_path,
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, rm_stderr = await asyncio.wait_for(remove_proc.communicate(), timeout=30)
            if remove_proc.returncode != 0:
                err_msg = rm_stderr.decode("utf-8", errors="replace") if rm_stderr else "unknown"
                logger.error(
                    "Failed to remove health check worktree at %s: %s", wt_path, err_msg
                )
        except TimeoutError:
            logger.error(
                "Timed out removing health check worktree at %s — may need manual cleanup",
                wt_path,
            )
        except Exception:
            logger.error(
                "Unexpected error removing health check worktree at %s", wt_path, exc_info=True
            )


# ── Stale worktree garbage collection ────────────────────────────────

_STALE_AGE_SECONDS = 24 * 60 * 60  # 24 hours


async def cleanup_stale_worktrees(project_dir: str) -> int:
    """Remove health-check worktrees older than 24 hours.

    Should be called at pipeline start to prevent accumulation from
    previous runs that crashed before cleanup.

    Returns:
        Number of stale worktrees removed.
    """
    worktree_base = os.path.join(project_dir, ".forge", "worktrees")
    if not os.path.isdir(worktree_base):
        return 0

    removed = 0
    now = time.time()
    for entry in os.listdir(worktree_base):
        if not entry.startswith("_health-check-"):
            continue
        wt_path = os.path.join(worktree_base, entry)
        if not os.path.isdir(wt_path):
            continue
        try:
            age = now - os.path.getmtime(wt_path)
        except OSError:
            continue
        if age < _STALE_AGE_SECONDS:
            continue

        logger.info("Removing stale health-check worktree: %s (age %.1fh)", entry, age / 3600)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", "--force", wt_path,
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            removed += 1
        except Exception:
            logger.warning("Could not remove stale worktree %s", wt_path, exc_info=True)

    if removed:
        logger.info("Cleaned up %d stale health-check worktree(s)", removed)
    return removed


# ── Pipeline-level functions ─────────────────────────────────────────


async def capture_baseline(
    config: IntegrationCheckConfig,
    project_dir: str,
    base_branch: str,
) -> int | None:
    """Capture baseline exit code at pipeline start.

    Runs the health check on the clean base branch (before any tasks execute).

    Returns:
        The exit code (0 = green baseline, non-zero = red baseline),
        or None if the check is disabled, has no command, or hit an infra error.
    """
    if not effective_enabled(config):
        return None

    assert config.cmd is not None  # guaranteed by effective_enabled
    try:
        async with _temp_health_worktree(project_dir, base_branch) as wt_path:
            result = await run_health_check(config.cmd, wt_path, config.timeout_seconds)
    except RuntimeError as exc:
        logger.warning("Baseline capture failed (worktree): %s — skipping", exc)
        return None

    if result.status == "infra_error":
        logger.warning(
            "Baseline capture infra error: %s — treating baseline as unknown",
            result.stderr[:200],
        )
        return None

    return result.exit_code


async def run_post_merge_check(
    config: IntegrationCheckConfig,
    project_dir: str,
    pipeline_branch: str,
    baseline_exit_code: int | None,
    task_id: str,
) -> IntegrationCheckResult:
    """Run the post-merge health check on pipeline_branch.

    Called after a task successfully merges. Creates a temp worktree
    on the current pipeline branch state and runs the check.

    Sets is_regression = True only if baseline was green (exit=0)
    and this check is red (exit != 0).
    """
    if not effective_enabled(config):
        return _SKIPPED

    assert config.cmd is not None  # guaranteed by effective_enabled
    try:
        async with _temp_health_worktree(project_dir, pipeline_branch) as wt_path:
            result = await run_health_check(config.cmd, wt_path, config.timeout_seconds)
    except RuntimeError as exc:
        logger.warning("Post-merge health check worktree error for %s: %s", task_id, exc)
        return IntegrationCheckResult(
            status="infra_error",
            exit_code=None,
            stdout="",
            stderr=str(exc),
            elapsed_seconds=0.0,
            is_regression=False,
        )

    # Determine regression: only if baseline was green and this is red
    if result.status in ("failed", "timeout"):
        result.is_regression = baseline_exit_code == 0

    return result


async def run_final_gate(
    config: IntegrationCheckConfig,
    project_dir: str,
    pipeline_branch: str,
) -> IntegrationCheckResult:
    """Run the final gate health check on pipeline_branch.

    Called once after all tasks complete, before PR creation.
    No baseline comparison — just pass/fail.
    """
    if not effective_enabled(config):
        return _SKIPPED

    assert config.cmd is not None  # guaranteed by effective_enabled
    try:
        async with _temp_health_worktree(project_dir, pipeline_branch) as wt_path:
            result = await run_health_check(config.cmd, wt_path, config.timeout_seconds)
    except RuntimeError as exc:
        logger.warning("Final gate worktree error: %s", exc)
        return IntegrationCheckResult(
            status="infra_error",
            exit_code=None,
            stdout="",
            stderr=str(exc),
            elapsed_seconds=0.0,
            is_regression=False,
        )

    return result
