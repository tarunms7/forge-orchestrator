"""ReviewMixin — extracted review pipeline methods for ForgeDaemon."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import logging
import time

from rich.console import Console

from forge.core.daemon_helpers import (
    _extract_text,
    _find_related_test_files,
    _get_changed_files_vs_main,
    _is_pytest_cmd,
    _run_git,
)
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

    # -- template review config --------------------------------------------

    def _get_review_config(self) -> dict:
        """Load review config from self._template_config.

        Returns a dict with keys: skip_l2, extra_review_pass,
        custom_review_focus.  All default to safe values when no
        template config is set.
        """
        template_config = getattr(self, "_template_config", None)
        if not template_config:
            return {"skip_l2": False, "extra_review_pass": False, "custom_review_focus": ""}
        review_raw = template_config.get("review_config", {})
        if not isinstance(review_raw, dict):
            return {"skip_l2": False, "extra_review_pass": False, "custom_review_focus": ""}
        return {
            "skip_l2": bool(review_raw.get("skip_l2", False)),
            "extra_review_pass": bool(review_raw.get("extra_review_pass", False)),
            "custom_review_focus": review_raw.get("custom_review_focus", "") or "",
        }

    # -- command resolution ------------------------------------------------

    def _resolve_build_cmd(self) -> str | None:
        """Return the build command: template override → pipeline override → settings fallback.

        If the template sets build_cmd to '' (empty string), the build gate
        is explicitly skipped.
        """
        template_config = getattr(self, "_template_config", None)
        if template_config and "build_cmd" in template_config:
            # Empty string means "skip this gate"
            val = template_config["build_cmd"]
            return val if val else None
        return getattr(self, '_pipeline_build_cmd', None) or getattr(self._settings, 'build_cmd', None)

    def _resolve_test_cmd(self) -> str | None:
        """Return the test command: template override → pipeline override → settings fallback.

        If the template sets test_cmd to '' (empty string), the test gate
        is explicitly skipped.
        """
        template_config = getattr(self, "_template_config", None)
        if template_config and "test_cmd" in template_config:
            val = template_config["test_cmd"]
            return val if val else None
        return getattr(self, '_pipeline_test_cmd', None) or getattr(self._settings, 'test_cmd', None)

    # -- shell gate helpers ------------------------------------------------

    async def _gate_build(self, worktree_path: str, build_cmd: str, timeout: int) -> GateResult:
        """Gate 0: Build gate — run the project build command."""
        return await self._run_shell_gate(worktree_path, build_cmd, timeout, gate_name='gate0_build')

    async def _gate_test(
        self, worktree_path: str, test_cmd: str, timeout: int,
        *, changed_files: list[str] | None = None,
    ) -> GateResult:
        """Gate 1.5: Test gate — run the project test command.

        When *changed_files* is provided and *test_cmd* is pytest-based, the
        gate automatically scopes to test files related to the changed source
        files.  This prevents pre-existing failures in unrelated tests from
        blocking every task in the pipeline.

        If no related test files are found, the gate passes with a "no
        relevant tests" message rather than running the full suite.
        """
        if changed_files and _is_pytest_cmd(test_cmd):
            test_files = _find_related_test_files(worktree_path, changed_files)
            if not test_files:
                return GateResult(
                    passed=True,
                    gate="gate1_5_test",
                    details="No test files found for changed files — skipped",
                )
            scoped_cmd = f"{test_cmd} {' '.join(test_files)}"
            logger.info(
                "Test gate scoped to %d test file(s): %s",
                len(test_files), ", ".join(test_files),
            )
            return await self._run_shell_gate(
                worktree_path, scoped_cmd, timeout, gate_name="gate1_5_test",
            )
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

    # -- streaming helpers --------------------------------------------------

    def _make_review_on_message(self, task_id: str, db, pipeline_id: str):
        """Build a batched on_message callback for LLM review streaming.

        Returns (callback, flush) where *flush* drains any remaining
        buffered lines.  The pattern mirrors ``_stream_agent`` in
        daemon_executor.
        """
        _last_flush = [time.monotonic()]
        _batch: list[str] = []

        async def _on_msg(msg):
            text = _extract_text(msg)
            if not text:
                return
            _batch.append(text)
            now = time.monotonic()
            if now - _last_flush[0] >= 0.1:
                for line in _batch:
                    await self._emit(
                        "review:llm_output",
                        {"task_id": task_id, "line": line},
                        db=db, pipeline_id=pipeline_id,
                    )
                _batch.clear()
                _last_flush[0] = now

        async def _flush():
            for line in _batch:
                await self._emit(
                    "review:llm_output",
                    {"task_id": task_id, "line": line},
                    db=db, pipeline_id=pipeline_id,
                )
            _batch.clear()

        return _on_msg, _flush

    # -- main review pipeline ----------------------------------------------

    async def _build_sibling_context(self, task, db, pipeline_id: str) -> str | None:
        """Build a context section describing sibling tasks in the same pipeline.

        Gives the reviewer awareness of the DAG so it doesn't fail reviews
        for cross-task concerns that another task handles (e.g. route
        registration in app.py when app.py belongs to a sibling task).
        """
        if not pipeline_id:
            return None

        all_tasks = await db.list_tasks_by_pipeline(pipeline_id)
        if len(all_tasks) <= 1:
            return None  # Solo task, no siblings

        lines = [
            "## Pipeline Task Context (DAG Awareness)",
            "",
            "This task is part of a multi-task pipeline. Other sibling tasks handle "
            "different parts of the implementation. Do NOT fail this review for "
            "missing functionality that belongs to another task's scope.",
            "",
        ]

        for sibling in all_tasks:
            if sibling.id == task.id:
                continue
            files_str = ", ".join((sibling.files or [])[:5])
            if sibling.files and len(sibling.files) > 5:
                files_str += f"... (+{len(sibling.files) - 5} more)"
            if not files_str:
                files_str = "(none)"
            lines.append(
                f"- **{sibling.id}** ({sibling.title}): "
                f"files=[{files_str}], state={sibling.state}"
            )

        lines.append("")
        lines.append(
            "IMPORTANT: If the task spec implies something needs to happen in a file "
            "NOT in this task's allowed scope (e.g., registering a route in app.py "
            "when app.py belongs to another task), do NOT fail the review. That "
            "work is handled by the sibling task that owns that file. Only review "
            "code changes within this task's allowed files."
        )

        return "\n".join(lines)

    async def _run_review(
        self, task, worktree_path: str, diff: str, *, db, pipeline_id: str,
        pipeline_branch: str | None = None,
        delta_diff: str | None = None,
    ) -> tuple[bool, str | None]:
        """Run the 3-gate review pipeline.

        Args:
            pipeline_branch: The pipeline branch ref used as the diff base
                for ``_get_changed_files_vs_main`` in the lint gate.
            delta_diff: On retry, the diff of only what the retry agent
                changed (pre_retry_ref..HEAD). Helps the reviewer focus
                on the fix rather than re-reading the full accumulated diff.

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
            await self._emit("review:gate_started", {
                "task_id": task.id, "gate": "gate0_build",
            }, db=db, pipeline_id=pipeline_id)
            build_result = await self._gate_build(worktree_path, build_cmd, gate_timeout)
            await self._emit(
                "review:gate_passed" if build_result.passed else "review:gate_failed",
                {"task_id": task.id, "gate": "gate0_build", "details": build_result.details},
                db=db, pipeline_id=pipeline_id,
            )
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "Gate0_Build", "passed": build_result.passed,
                "details": build_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not build_result.passed:
                console.print(f"[red]  Gate 0 (build) failed: {build_result.details}[/red]")
                feedback_parts.append(f"Gate 0 (build) FAILED:\n{build_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  Gate 0 (build) passed[/green]")
        else:
            console.print("[dim]  Gate 0 (build): skipped — no build command configured[/dim]")
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "gate0_build", "passed": True,
                "skipped": True, "details": "No build command configured",
            }, db=db, pipeline_id=pipeline_id)

        # Compute changed files once — used by both lint (L1) and test (Gate 1.5) gates
        changed_files = _get_changed_files_vs_main(worktree_path, base_ref=pipeline_branch)

        # L1: lint only the changed files (not full test suite)
        console.print(f"[blue]  L1 (general): Auto-checks for {task.id}...[/blue]")
        await self._emit("review:gate_started", {
            "task_id": task.id, "gate": "gate1_lint",
        }, db=db, pipeline_id=pipeline_id)
        gate1_result = await self._gate1(worktree_path, pipeline_branch=pipeline_branch)
        await self._emit(
            "review:gate_passed" if gate1_result.passed else "review:gate_failed",
            {"task_id": task.id, "gate": "gate1_lint", "details": gate1_result.details},
            db=db, pipeline_id=pipeline_id,
        )
        await self._emit("task:review_update", {
            "task_id": task.id, "gate": "L1", "passed": gate1_result.passed,
            "details": gate1_result.details,
        }, db=db, pipeline_id=pipeline_id)
        if not gate1_result.passed:
            console.print(f"[red]  L1 failed: {gate1_result.details}[/red]")
            feedback_parts.append(f"L1 (lint) FAILED:\n{gate1_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L1 passed[/green]")

        # L1 may have auto-fixed lint issues (unused imports, etc.) and
        # committed the result.  Recompute the diff so L2 reviews the
        # post-fix code rather than the stale pre-fix diff the caller
        # captured before the review pipeline ran.
        if pipeline_branch:
            from forge.core.daemon_helpers import _get_diff_vs_main
            diff = _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)

        # Gate 1.5: Test gate — scoped to changed files when possible
        test_cmd = self._resolve_test_cmd()
        if test_cmd:
            console.print(f"[blue]  Gate 1.5 (test): Running tests for {task.id}...[/blue]")
            await self._emit("review:gate_started", {
                "task_id": task.id, "gate": "gate1_5_test",
            }, db=db, pipeline_id=pipeline_id)
            test_result = await self._gate_test(
                worktree_path, test_cmd, gate_timeout, changed_files=changed_files,
            )
            await self._emit(
                "review:gate_passed" if test_result.passed else "review:gate_failed",
                {"task_id": task.id, "gate": "gate1_5_test", "details": test_result.details},
                db=db, pipeline_id=pipeline_id,
            )
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "Gate1_5_Test", "passed": test_result.passed,
                "details": test_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not test_result.passed:
                console.print(f"[red]  Gate 1.5 (test) failed: {test_result.details}[/red]")
                feedback_parts.append(f"Gate 1.5 (test) FAILED:\n{test_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  Gate 1.5 (test) passed[/green]")
        else:
            console.print("[dim]  Gate 1.5 (test): skipped — no test command configured[/dim]")
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "gate1_5_test", "passed": True,
                "skipped": True, "details": "No test command configured",
            }, db=db, pipeline_id=pipeline_id)

        # L2: LLM review
        # Load review config from template
        review_config = self._get_review_config()

        if review_config["skip_l2"]:
            console.print("[yellow]  L2 skipped by template[/yellow]")
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "L2", "passed": True,
                "details": "Skipped by template configuration",
            }, db=db, pipeline_id=pipeline_id)
        else:
            # Pass prior feedback + prior diff so the reviewer focuses on
            # verifying fixes instead of inventing new complaints on every retry.
            prior_feedback = getattr(task, "review_feedback", None) if task.retry_count > 0 else None
            prior_diff = getattr(task, "prior_diff", None) if task.retry_count > 0 else None
            console.print(
                f"[blue]  L2 (LLM): Code review for {task.id}"
                f"{'  (re-review)' if prior_feedback else ''}...[/blue]"
            )
            reviewer_model = select_model(self._strategy, "reviewer", task.complexity or "medium")
            # Build sibling context so the reviewer knows about the DAG
            sibling_context = await self._build_sibling_context(task, db, pipeline_id)
            custom_review_focus = review_config["custom_review_focus"]
            # Inject contract compliance into review focus
            if pipeline_id:
                contracts_json = await db.get_pipeline_contracts(pipeline_id)
                if contracts_json:
                    from forge.core.contracts import ContractSet as CS
                    try:
                        contract_set = CS.model_validate_json(contracts_json)
                        task_contracts = contract_set.contracts_for_task(task.id)
                        contract_review = task_contracts.format_for_reviewer()
                        if contract_review:
                            if custom_review_focus:
                                custom_review_focus = f"{custom_review_focus}\n\n{contract_review}"
                            else:
                                custom_review_focus = contract_review
                    except Exception:
                        logger.warning("Failed to parse contracts for review of task %s", task.id)
                        await self._emit("task:review_update", {
                            "task_id": task.id, "gate": "contract_loading",
                            "passed": True,
                            "details": "Contract loading failed — reviewing without contract compliance checks",
                        }, db=db, pipeline_id=pipeline_id)
            await self._emit("review:gate_started", {
                "task_id": task.id, "gate": "gate2_llm_review",
            }, db=db, pipeline_id=pipeline_id)
            on_message, flush_review = self._make_review_on_message(
                task.id, db, pipeline_id,
            )
            gate2_result, review_cost_info = await gate2_llm_review(
                task.title, task.description, diff, worktree_path,
                model=reviewer_model,
                prior_feedback=prior_feedback,
                prior_diff=prior_diff,
                project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
                allowed_files=task.files,
                delta_diff=delta_diff,
                sibling_context=sibling_context,
                custom_review_focus=custom_review_focus,
                on_message=on_message,
            )
            await flush_review()
            # Emit LLM feedback so the TUI can display reviewer comments
            await self._emit("review:llm_feedback", {
                "task_id": task.id, "feedback": gate2_result.details,
            }, db=db, pipeline_id=pipeline_id)
            # Track review cost
            if review_cost_info.cost_usd > 0:
                await db.add_task_review_cost(task.id, review_cost_info.cost_usd)
                await db.add_pipeline_cost(pipeline_id, review_cost_info.cost_usd)
                await self._emit("task:cost_update", {
                    "task_id": task.id,
                    "review_cost_usd": review_cost_info.cost_usd,
                    "input_tokens": review_cost_info.input_tokens,
                    "output_tokens": review_cost_info.output_tokens,
                }, db=db, pipeline_id=pipeline_id)
                total_cost = await db.get_pipeline_cost(pipeline_id)
                await self._emit("pipeline:cost_update", {
                    "total_cost_usd": total_cost,
                }, db=db, pipeline_id=pipeline_id)
            await self._emit(
                "review:gate_passed" if gate2_result.passed else "review:gate_failed",
                {"task_id": task.id, "gate": "gate2_llm_review", "details": gate2_result.details},
                db=db, pipeline_id=pipeline_id,
            )
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "L2", "passed": gate2_result.passed,
                "details": gate2_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not gate2_result.passed:
                console.print(f"[red]  L2 failed: {gate2_result.details}[/red]")
                prefix = "[RETRIABLE] " if gate2_result.retriable else ""
                feedback_parts.append(f"{prefix}L2 (LLM code review) FAILED:\n{gate2_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  L2 passed[/green]")

            # Extra review pass: run L2 a second time if configured
            if review_config["extra_review_pass"]:
                console.print(f"[blue]  L2 (extra pass): Second review for {task.id}...[/blue]")
                extra_focus = custom_review_focus
                if extra_focus:
                    extra_focus += "\n\n"
                extra_focus += (
                    "This is a SECOND REVIEW PASS. A previous reviewer already "
                    "approved. Catch anything they missed."
                )
                gate2_extra, extra_cost_info = await gate2_llm_review(
                    task.title, task.description, diff, worktree_path,
                    model=reviewer_model,
                    project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
                    allowed_files=task.files,
                    sibling_context=sibling_context,
                    custom_review_focus=extra_focus,
                )
                # Track extra review cost
                if extra_cost_info.cost_usd > 0:
                    await db.add_task_review_cost(task.id, extra_cost_info.cost_usd)
                    await db.add_pipeline_cost(pipeline_id, extra_cost_info.cost_usd)
                    await self._emit("task:cost_update", {
                        "task_id": task.id,
                        "review_cost_usd": extra_cost_info.cost_usd,
                        "input_tokens": extra_cost_info.input_tokens,
                        "output_tokens": extra_cost_info.output_tokens,
                    }, db=db, pipeline_id=pipeline_id)
                    total_cost = await db.get_pipeline_cost(pipeline_id)
                    await self._emit("pipeline:cost_update", {
                        "total_cost_usd": total_cost,
                    }, db=db, pipeline_id=pipeline_id)
                await self._emit("task:review_update", {
                    "task_id": task.id, "gate": "L2_extra", "passed": gate2_extra.passed,
                    "details": gate2_extra.details,
                }, db=db, pipeline_id=pipeline_id)
                if not gate2_extra.passed:
                    if gate2_extra.retriable:
                        # Transient failure (empty response, SDK timeout) on the
                        # extra pass — the primary L2 already approved.  Don't
                        # waste retries re-running the whole pipeline; treat as pass.
                        console.print("[yellow]  L2 (extra pass) transient failure — skipping (primary L2 passed)[/yellow]")
                        await self._emit("task:review_update", {
                            "task_id": task.id, "gate": "L2_extra", "passed": True,
                            "skipped": True,
                            "details": "Transient failure — skipped (primary L2 passed)",
                        }, db=db, pipeline_id=pipeline_id)
                    else:
                        console.print(f"[red]  L2 (extra pass) failed: {gate2_extra.details}[/red]")
                        feedback_parts.append(f"L2 extra pass FAILED:\n{gate2_extra.details}")
                        return False, "\n\n".join(feedback_parts)
                else:
                    console.print("[green]  L2 (extra pass) passed[/green]")

        # Gate 3: skip for now — merge check is handled by merge_worker
        console.print("[dim]  Gate 3 (merge readiness): deferred to merge step[/dim]")
        return True, None

    async def _gate1(self, worktree_path: str, *, pipeline_branch: str | None = None) -> GateResult:
        """Gate 1: Lint check on the worktree. Simple and fast."""

        # Only run ruff on changed files vs main.
        # Filter out deleted files — they appear in `git diff --name-only`
        # but no longer exist on disk, causing ruff E902 errors.
        changed = _get_changed_files_vs_main(worktree_path, base_ref=pipeline_branch)
        py_files = [
            f for f in changed
            if f.endswith(".py") and os.path.isfile(os.path.join(worktree_path, f))
        ]

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
        _run_git(["add", "-A"], cwd=worktree_path, check=False, description="stage lint fixes")
        # Only commit if there are staged changes
        staged = _run_git(
            ["diff", "--cached", "--name-only"],
            cwd=worktree_path, check=False, description="check staged lint fixes",
        )
        if staged.stdout.strip():
            _run_git(
                ["commit", "-m", "fix: auto-fix lint issues (ruff)"],
                cwd=worktree_path, check=False, description="commit lint fixes",
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
