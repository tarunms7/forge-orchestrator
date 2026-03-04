"""ReviewMixin — extracted review pipeline methods for ForgeDaemon."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import logging

from rich.console import Console

from forge.core.daemon_helpers import _get_changed_files_vs_main
from forge.core.model_router import select_model
from forge.review.llm_review import gate2_llm_review
from forge.review.pipeline import GateResult

logger = logging.getLogger("forge.daemon")
console = Console()


class ReviewMixin:
    """Review pipeline methods mixed into ForgeDaemon.

    Expects the host class to provide:
        self._strategy   — model routing strategy (str)
        self._snapshot   — ProjectSnapshot | None
        self._emit       — async event emitter
        self._settings   — ForgeSettings
    """

    # -- command resolution ------------------------------------------------

    def _resolve_build_cmd(self) -> str | None:
        """Return the build command: pipeline override → settings fallback."""
        return getattr(self, '_pipeline_build_cmd', None) or getattr(self._settings, 'build_cmd', None)

    def _resolve_test_cmd(self) -> str | None:
        """Return the test command: pipeline override → settings fallback."""
        return getattr(self, '_pipeline_test_cmd', None) or getattr(self._settings, 'test_cmd', None)

    # -- shell gate helpers ------------------------------------------------

    async def _gate_build(self, worktree_path: str, build_cmd: str, timeout: int) -> GateResult:
        """Gate 0: Build gate — run the project build command."""
        return await self._run_shell_gate(worktree_path, build_cmd, timeout, gate_name='gate0_build')

    async def _gate_test(self, worktree_path: str, test_cmd: str, timeout: int) -> GateResult:
        """Gate 1.5: Test gate — run the project test command."""
        return await self._run_shell_gate(worktree_path, test_cmd, timeout, gate_name='gate1_5_test')

    async def _run_shell_gate(
        self, worktree_path: str, cmd: str, timeout: int, *, gate_name: str,
    ) -> GateResult:
        """Execute a shell command as a review gate.

        Runs *cmd* inside *worktree_path* with a timeout.  Captures stdout+stderr,
        truncated to the last 5000 characters so logs stay manageable.
        """
        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd,
                shell=True,
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

        try:
            proc = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
        except asyncio.TimeoutError:
            return GateResult(
                passed=False,
                gate=gate_name,
                details=f"Command timed out after {timeout}s: {cmd}",
            )

        combined = (proc.stdout or "") + (proc.stderr or "")
        # Keep last 5000 chars so we see the tail of build/test output
        truncated = combined[-5000:] if len(combined) > 5000 else combined

        if proc.returncode == 0:
            return GateResult(passed=True, gate=gate_name, details="OK")

        return GateResult(
            passed=False,
            gate=gate_name,
            details=f"Exit code {proc.returncode}:\n{truncated}",
        )

    # -- main review pipeline ----------------------------------------------

    async def _run_review(
        self, task, worktree_path: str, diff: str, *, db, pipeline_id: str,
    ) -> tuple[bool, str | None]:
        """Run the 3-gate review pipeline.

        Returns:
            (passed, feedback) — feedback is a string with failure details
            if any gate failed, None if all passed.
        """
        feedback_parts: list[str] = []
        gate_timeout = self._settings.agent_timeout_seconds // 2

        # Gate 0: Build gate (skip silently if no command configured)
        build_cmd = self._resolve_build_cmd()
        if build_cmd:
            console.print(f"[blue]  Gate 0 (build): Running build for {task.id}...[/blue]")
            build_result = await self._gate_build(worktree_path, build_cmd, gate_timeout)
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "Gate0_Build", "passed": build_result.passed,
                "details": build_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not build_result.passed:
                console.print(f"[red]  Gate 0 (build) failed: {build_result.details}[/red]")
                feedback_parts.append(f"Gate 0 (build) FAILED:\n{build_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  Gate 0 (build) passed[/green]")

        # L1: lint only the changed files (not full test suite)
        console.print(f"[blue]  L1 (general): Auto-checks for {task.id}...[/blue]")
        gate1_result = await self._gate1(worktree_path)
        await self._emit("task:review_update", {
            "task_id": task.id, "gate": "L1", "passed": gate1_result.passed,
            "details": gate1_result.details,
        }, db=db, pipeline_id=pipeline_id)
        if not gate1_result.passed:
            console.print(f"[red]  L1 failed: {gate1_result.details}[/red]")
            feedback_parts.append(f"L1 (lint) FAILED:\n{gate1_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L1 passed[/green]")

        # Gate 1.5: Test gate (skip silently if no command configured)
        test_cmd = self._resolve_test_cmd()
        if test_cmd:
            console.print(f"[blue]  Gate 1.5 (test): Running tests for {task.id}...[/blue]")
            test_result = await self._gate_test(worktree_path, test_cmd, gate_timeout)
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "Gate1_5_Test", "passed": test_result.passed,
                "details": test_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not test_result.passed:
                console.print(f"[red]  Gate 1.5 (test) failed: {test_result.details}[/red]")
                feedback_parts.append(f"Gate 1.5 (test) FAILED:\n{test_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  Gate 1.5 (test) passed[/green]")

        # L2: LLM review
        # Pass prior feedback so the reviewer focuses on verifying fixes
        # instead of inventing new complaints on every retry.
        prior_feedback = getattr(task, "review_feedback", None) if task.retry_count > 0 else None
        console.print(
            f"[blue]  L2 (LLM): Code review for {task.id}"
            f"{'  (re-review)' if prior_feedback else ''}...[/blue]"
        )
        reviewer_model = select_model(self._strategy, "reviewer", task.complexity or "medium")
        gate2_result = await gate2_llm_review(
            task.title, task.description, diff, worktree_path,
            model=reviewer_model,
            prior_feedback=prior_feedback,
            project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
        )
        await self._emit("task:review_update", {
            "task_id": task.id, "gate": "L2", "passed": gate2_result.passed,
            "details": gate2_result.details,
        }, db=db, pipeline_id=pipeline_id)
        if not gate2_result.passed:
            console.print(f"[red]  L2 failed: {gate2_result.details}[/red]")
            feedback_parts.append(f"L2 (LLM code review) FAILED:\n{gate2_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L2 passed[/green]")

        # Gate 3: skip for now — merge check is handled by merge_worker
        console.print("[green]  Gate 3 (merge readiness): auto-pass[/green]")
        return True, None

    async def _gate1(self, worktree_path: str) -> GateResult:
        """Gate 1: Lint check on the worktree. Simple and fast."""

        # Only run ruff on changed files vs main
        changed = _get_changed_files_vs_main(worktree_path)
        py_files = [f for f in changed if f.endswith(".py")]

        if not py_files:
            return GateResult(passed=True, gate="gate1_auto_check", details="No Python files changed")

        # Auto-fix trivial lint issues (unused imports, etc.) before checking.
        # Agents commonly add `import pytest` or unused imports — ruff can fix
        # these automatically, avoiding wasted retries on mechanical issues.
        subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--fix"] + py_files,
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        # Commit any auto-fixes so they're included in the diff
        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_path,
            capture_output=True,
        )
        # Only commit if there are staged changes
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if staged.stdout.strip():
            subprocess.run(
                ["git", "commit", "-m", "fix: auto-fix lint issues (ruff)"],
                cwd=worktree_path,
                capture_output=True,
            )

        # Use sys.executable so we get the same Python (and venv) as forge itself
        lint_result = subprocess.run(
            [sys.executable, "-m", "ruff", "check"] + py_files,
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        lint_clean = lint_result.returncode == 0

        if lint_clean:
            return GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")

        # Include both stdout and stderr — ruff errors may go to either
        output = (lint_result.stdout or lint_result.stderr or "Unknown error")[:500]
        return GateResult(
            passed=False,
            gate="gate1_auto_check",
            details=f"Lint errors:\n{output}",
        )
