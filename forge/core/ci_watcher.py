"""CI Auto-Fix: watch PR checks, diagnose failures, dispatch fix agents.

After Forge creates a PR, this module polls GitHub CI checks via the `gh` CLI.
When checks fail, it fetches failure logs, dispatches a Claude agent to fix the
issue, and pushes the fix to the same PR branch. Repeats until CI passes or
max retries are exhausted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from forge.core.daemon_helpers import async_subprocess

if TYPE_CHECKING:
    from forge.core.sdk_helpers import SdkResult
    from forge.storage.db import Database

logger = logging.getLogger("forge.ci_watcher")


def _decode_output(raw: str | bytes) -> str:
    """Decode subprocess output to str, handling both bytes and str inputs."""
    return raw.decode() if isinstance(raw, bytes) else str(raw)


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class CICheck:
    """One GitHub Actions check/status."""
    name: str
    status: str       # "queued", "in_progress", "completed", "waiting", etc.
    conclusion: str   # "success", "failure", "cancelled", "skipped", "neutral", ""
    run_id: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status == "completed"

    @property
    def is_failure(self) -> bool:
        return self.is_terminal and self.conclusion in ("failure", "cancelled", "timed_out")

    @property
    def is_success(self) -> bool:
        return self.is_terminal and self.conclusion in ("success", "neutral", "skipped")


@dataclass
class CIFixAttempt:
    """Record of one fix attempt."""
    attempt: int
    failed_checks: list[str]
    fix_summary: str = ""
    cost_usd: float = 0.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


@dataclass
class CIFixResult:
    """Overall result of the CI fix loop."""
    final_status: str  # "passed", "exhausted", "cancelled", "error", "timeout"
    attempts: list[CIFixAttempt] = field(default_factory=list)
    total_cost_usd: float = 0.0


@dataclass
class CIFixConfig:
    """Runtime config for the CI fix loop."""
    max_retries: int = 3
    poll_timeout_seconds: int = 1800
    poll_interval_seconds: int = 30
    budget_usd: float = 0.0
    model: str = "sonnet"
    max_turns: int = 50


# ── URL parsing ──────────────────────────────────────────────────────

_PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner_repo>[^/]+/[^/]+)/pull/(?P<number>\d+)"
)


def parse_pr_info(pr_url: str) -> tuple[str, str]:
    """Extract (owner/repo, pr_number) from a GitHub PR URL.

    Returns ("", "") if the URL doesn't match.
    """
    m = _PR_URL_RE.search(pr_url)
    if not m:
        return ("", "")
    return (m.group("owner_repo"), m.group("number"))


# ── CI polling ───────────────────────────────────────────────────────


async def poll_ci_checks(
    owner_repo: str,
    pr_number: str,
    project_dir: str,
    *,
    timeout: int = 1800,
    interval: int = 30,
    cancel_event: asyncio.Event | None = None,
    on_update: Callable[[list[CICheck]], None] | None = None,
) -> list[CICheck]:
    """Poll `gh pr checks` until all checks are terminal or timeout.

    Uses exponential backoff: interval, interval*1.5, interval*2, capped at 90s.

    Returns the final list of checks. Raises asyncio.TimeoutError on timeout.
    """
    start = time.monotonic()
    current_interval = interval
    grace_empty_polls = 3  # Allow a few empty polls for CI to register

    while True:
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("CI watch cancelled by user")

        elapsed = time.monotonic() - start
        if elapsed > timeout:
            raise TimeoutError(f"CI checks did not complete within {timeout}s")

        checks = await _fetch_checks(owner_repo, pr_number, project_dir)

        if on_update and checks:
            on_update(checks)

        if not checks:
            grace_empty_polls -= 1
            if grace_empty_polls <= 0:
                # No CI configured — treat as passed
                logger.info("No CI checks found for PR #%s — treating as passed", pr_number)
                return []
        else:
            all_terminal = all(c.is_terminal for c in checks)
            if all_terminal:
                return checks

        await asyncio.sleep(min(current_interval, 90))
        current_interval = min(current_interval * 1.5, 90)


async def _fetch_checks(
    owner_repo: str, pr_number: str, project_dir: str
) -> list[CICheck]:
    """Run `gh pr checks` and parse the JSON output."""
    result = await async_subprocess(
        [
            "gh", "pr", "checks", pr_number,
            "--repo", owner_repo,
            "--json", "name,state,conclusion,detailsUrl",
        ],
        cwd=project_dir,
    )
    if result.returncode != 0:
        stderr = _decode_output(result.stderr)
        logger.warning("gh pr checks failed (exit %d): %s", result.returncode, stderr[:500])
        return []

    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else str(result.stdout)
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse gh pr checks JSON: %s", stdout[:500])
        return []

    checks = []
    for item in data:
        # Extract run ID from detailsUrl if available
        run_id = ""
        details_url = item.get("detailsUrl", "")
        run_match = re.search(r"/actions/runs/(\d+)", details_url)
        if run_match:
            run_id = run_match.group(1)

        checks.append(CICheck(
            name=item.get("name", "unknown"),
            status=item.get("state", "").lower(),
            conclusion=item.get("conclusion", "").lower(),
            run_id=run_id,
        ))
    return checks


# ── Failure log fetching ─────────────────────────────────────────────

_MAX_LOG_CHARS = 3000


async def fetch_failure_logs(
    owner_repo: str,
    failed_checks: list[CICheck],
    project_dir: str,
) -> dict[str, str]:
    """Fetch failure logs for failed CI checks via `gh run view --log-failed`.

    Returns {check_name: log_output} mapping, each truncated to _MAX_LOG_CHARS.
    """
    logs: dict[str, str] = {}
    seen_run_ids: set[str] = set()

    for check in failed_checks:
        if not check.run_id or check.run_id in seen_run_ids:
            logs[check.name] = f"Check '{check.name}' failed with conclusion: {check.conclusion}"
            continue
        seen_run_ids.add(check.run_id)

        result = await async_subprocess(
            [
                "gh", "run", "view", check.run_id,
                "--repo", owner_repo,
                "--log-failed",
            ],
            cwd=project_dir,
        )
        stdout = _decode_output(result.stdout)
        stderr = _decode_output(result.stderr)

        if result.returncode != 0:
            logs[check.name] = f"Failed to fetch logs (exit {result.returncode}): {stderr[:500]}"
        else:
            # Truncate to last N chars for prompt efficiency
            log_text = stdout.strip()
            if len(log_text) > _MAX_LOG_CHARS:
                log_text = "... (truncated)\n" + log_text[-_MAX_LOG_CHARS:]
            logs[check.name] = log_text

    return logs


# ── PR status check ──────────────────────────────────────────────────


async def check_pr_open(owner_repo: str, pr_number: str, project_dir: str) -> bool:
    """Check if the PR is still open (not merged or closed)."""
    result = await async_subprocess(
        [
            "gh", "pr", "view", pr_number,
            "--repo", owner_repo,
            "--json", "state",
        ],
        cwd=project_dir,
    )
    if result.returncode != 0:
        return False
    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else str(result.stdout)
    try:
        data = json.loads(stdout)
        return data.get("state", "").upper() == "OPEN"
    except json.JSONDecodeError:
        return False


# ── Fix agent dispatch ───────────────────────────────────────────────

_FIX_AGENT_PROMPT = """\
You are a CI fix agent. A pull request's CI checks have failed. Your job is to
diagnose and fix the failures so CI passes on the next run.

## Failed CI Checks

{failure_details}

## Current Branch

You are working on branch `{branch}`. The code has already been pushed to GitHub.

## Instructions

1. First, run `git pull --rebase origin {branch}` to ensure you have the latest code
2. Read the failure logs above carefully to understand what went wrong
3. Fix the issue — this may involve fixing code, tests, config, dependencies, etc.
4. Stage your changes with `git add`
5. Commit with a descriptive message: `fix: <what you fixed for CI>`
6. Push: `git push origin {branch}`

IMPORTANT:
- Only fix what's needed to pass CI. Do NOT refactor or add features.
- If the failure is a flaky test, add a retry or skip annotation.
- If a dependency is missing, add it properly.
- If a build config is wrong, fix it.
- Be surgical and minimal.
"""


async def dispatch_fix_agent(
    project_dir: str,
    branch: str,
    failure_logs: dict[str, str],
    *,
    model: str = "sonnet",
    max_turns: int = 50,
    base_branch: str = "main",
) -> SdkResult | None:
    """Dispatch a Claude agent to fix CI failures.

    The agent works in project_dir on the given branch, reads failure logs,
    and commits + pushes a fix.
    """
    from claude_code_sdk import ClaudeCodeOptions

    from forge.core.sdk_helpers import sdk_query

    # Build failure details section
    failure_parts = []
    for name, log in failure_logs.items():
        failure_parts.append(f"### {name}\n```\n{log}\n```")
    failure_details = "\n\n".join(failure_parts)

    prompt = _FIX_AGENT_PROMPT.format(
        failure_details=failure_details,
        branch=branch,
    )

    # Checkout the branch in the project dir before running agent
    await async_subprocess(["git", "checkout", branch], cwd=project_dir)
    await async_subprocess(["git", "pull", "--rebase", "origin", branch], cwd=project_dir)

    options = ClaudeCodeOptions(
        max_turns=max_turns,
        model=model,
        cwd=project_dir,
    )

    result = await sdk_query(prompt, options)

    # Checkout back to main to avoid leaving the repo on the feature branch
    await async_subprocess(["git", "checkout", base_branch], cwd=project_dir)

    return result


# ── Main fix loop ────────────────────────────────────────────────────


async def run_ci_fix_loop(
    *,
    config: CIFixConfig,
    pr_url: str,
    project_dir: str,
    branch: str,
    base_branch: str = "main",
    db: Database | None = None,
    pipeline_id: str = "",
    emit_fn: Callable | None = None,
    cancel_event: asyncio.Event | None = None,
) -> CIFixResult:
    """Main CI fix orchestrator: poll -> diagnose -> fix -> push -> repeat.

    Args:
        config: CI fix settings (retries, timeout, budget, model)
        pr_url: Full GitHub PR URL
        project_dir: Path to the git repository
        branch: The PR branch name
        base_branch: The base branch (default "main")
        db: Optional database for persisting state
        pipeline_id: Pipeline ID for DB/event tracking
        emit_fn: Optional async callback for broadcasting events
        cancel_event: Optional asyncio.Event for cancellation

    Returns:
        CIFixResult with final status and attempt history
    """
    owner_repo, pr_number = parse_pr_info(pr_url)
    if not owner_repo or not pr_number:
        logger.error("Invalid PR URL: %s", pr_url)
        return CIFixResult(final_status="error")

    if cancel_event is None:
        cancel_event = asyncio.Event()

    attempts: list[CIFixAttempt] = []
    total_cost = 0.0

    async def _emit(event_type: str, payload: dict | None = None):
        if emit_fn:
            try:
                await emit_fn(event_type, payload or {})
            except Exception:
                logger.warning("Failed to emit %s", event_type, exc_info=True)

    async def _update_db(**kwargs):
        if db and pipeline_id:
            try:
                await db.update_pipeline_ci_fix(pipeline_id, **kwargs)
            except Exception:
                logger.warning("Failed to update CI fix DB state", exc_info=True)

    # Persist initial state
    await _update_db(
        ci_fix_status="watching",
        ci_fix_attempt=0,
        ci_fix_max_retries=config.max_retries,
    )
    await _emit("pipeline:ci_watching", {})

    for attempt_num in range(1, config.max_retries + 1):
        if cancel_event.is_set():
            await _update_db(ci_fix_status="cancelled")
            await _emit("pipeline:ci_fix_cancelled", {"reason": "User cancelled"})
            return CIFixResult(final_status="cancelled", attempts=attempts, total_cost_usd=total_cost)

        # Check if PR is still open
        if not await check_pr_open(owner_repo, pr_number, project_dir):
            logger.info("PR #%s is no longer open — stopping CI fix", pr_number)
            await _update_db(ci_fix_status="cancelled")
            await _emit("pipeline:ci_fix_cancelled", {"reason": "PR closed or merged"})
            return CIFixResult(final_status="cancelled", attempts=attempts, total_cost_usd=total_cost)

        # Poll CI checks
        try:
            checks = await poll_ci_checks(
                owner_repo, pr_number, project_dir,
                timeout=config.poll_timeout_seconds,
                interval=config.poll_interval_seconds,
                cancel_event=cancel_event,
                on_update=lambda cs: asyncio.get_event_loop().create_task(
                    _emit("pipeline:ci_check_update", {
                        "checks": [asdict(c) for c in cs],
                    })
                ) if emit_fn else None,
            )
        except TimeoutError:
            logger.warning("CI poll timed out for PR #%s", pr_number)
            await _update_db(ci_fix_status="error")
            await _emit("pipeline:ci_fix_error", {"error": "CI poll timed out"})
            return CIFixResult(final_status="timeout", attempts=attempts, total_cost_usd=total_cost)
        except asyncio.CancelledError:
            await _update_db(ci_fix_status="cancelled")
            await _emit("pipeline:ci_fix_cancelled", {"reason": "Cancelled during poll"})
            return CIFixResult(final_status="cancelled", attempts=attempts, total_cost_usd=total_cost)

        # Check results
        if not checks:
            # No CI checks — treat as passed
            await _update_db(ci_fix_status="passed")
            await _emit("pipeline:ci_passed", {"elapsed_s": 0})
            return CIFixResult(final_status="passed", attempts=attempts, total_cost_usd=total_cost)

        failed = [c for c in checks if c.is_failure]
        if not failed:
            # All passed!
            logger.info("All CI checks passed for PR #%s", pr_number)
            await _update_db(ci_fix_status="passed")
            await _emit("pipeline:ci_passed", {"elapsed_s": 0})
            return CIFixResult(final_status="passed", attempts=attempts, total_cost_usd=total_cost)

        # CI failed — diagnose
        failed_names = [c.name for c in failed]
        logger.info(
            "CI failed for PR #%s (attempt %d/%d): %s",
            pr_number, attempt_num, config.max_retries, ", ".join(failed_names),
        )

        await _emit("pipeline:ci_failed", {
            "attempt": attempt_num,
            "max_retries": config.max_retries,
            "failed_checks": [{"name": c.name, "conclusion": c.conclusion} for c in failed],
        })

        # Budget check
        if config.budget_usd > 0 and total_cost >= config.budget_usd:
            logger.warning("CI fix budget exhausted ($%.2f >= $%.2f)", total_cost, config.budget_usd)
            await _update_db(ci_fix_status="cancelled")
            await _emit("pipeline:ci_fix_cancelled", {"reason": "Budget exhausted"})
            return CIFixResult(final_status="cancelled", attempts=attempts, total_cost_usd=total_cost)

        # Fetch failure logs
        failure_logs = await fetch_failure_logs(owner_repo, failed, project_dir)

        # Dispatch fix agent
        await _update_db(ci_fix_status="fixing", ci_fix_attempt=attempt_num)
        await _emit("pipeline:ci_fixing", {"attempt": attempt_num, "max_retries": config.max_retries})

        try:
            sdk_result = await dispatch_fix_agent(
                project_dir, branch, failure_logs,
                model=config.model,
                max_turns=config.max_turns,
                base_branch=base_branch,
            )
        except Exception as exc:
            logger.error("Fix agent failed: %s", exc, exc_info=True)
            attempt = CIFixAttempt(
                attempt=attempt_num,
                failed_checks=failed_names,
                fix_summary=f"Agent error: {exc}",
            )
            attempts.append(attempt)
            await _update_db(
                ci_fix_log=json.dumps([asdict(a) for a in attempts]),
            )
            continue

        # Record attempt
        cost = sdk_result.cost_usd if sdk_result else 0.0
        total_cost += cost
        summary = ""
        if sdk_result and sdk_result.result_text:
            # Take first 500 chars of result as summary
            summary = sdk_result.result_text[:500]

        attempt = CIFixAttempt(
            attempt=attempt_num,
            failed_checks=failed_names,
            fix_summary=summary,
            cost_usd=cost,
        )
        attempts.append(attempt)

        await _update_db(
            ci_fix_cost_usd=total_cost,
            ci_fix_log=json.dumps([asdict(a) for a in attempts]),
            ci_fix_status="watching",  # Back to watching
        )
        await _emit("pipeline:ci_fix_pushed", {
            "attempt": attempt_num,
            "summary": summary[:200],
            "cost_usd": cost,
        })

        # Small delay before re-polling to let GitHub register the new push
        await asyncio.sleep(10)

    # Exhausted all retries
    logger.warning("CI fix exhausted all %d retries for PR #%s", config.max_retries, pr_number)
    await _update_db(ci_fix_status="exhausted")
    await _emit("pipeline:ci_fix_exhausted", {"attempts": len(attempts)})
    return CIFixResult(final_status="exhausted", attempts=attempts, total_cost_usd=total_cost)
