"""ExecutorMixin — decomposed _execute_task extracted from ForgeDaemon."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from rich.console import Console

from forge.core.budget import check_budget
from forge.core.daemon_helpers import (
    _build_agent_prompt,
    _build_retry_prompt,
    _extract_implementation_summary,
    _extract_text,
    _get_diff_stats,
    _get_diff_vs_main,
    _load_conventions_md,
    _resolve_ref,
)
from forge.core.model_router import select_model
from forge.core.models import TaskState

logger = logging.getLogger("forge")
console = Console()


class ExecutorMixin:
    """Mixin providing the decomposed ``_execute_task`` pipeline.

    Host class must supply: ``_project_dir``, ``_strategy``, ``_snapshot``,
    ``_settings``, ``_emit``, ``_run_review``, ``_resolve_conflicts``,
    ``_handle_retry``, ``_handle_merge_retry``.
    """

    # -- orchestrator ----------------------------------------------------

    async def _execute_task(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, pipeline_id: str | None = None,
    ) -> None:
        """Execute a single task: worktree → agent → review → merge."""
        task = await db.get_task(task_id)
        if not task:
            await db.release_agent(agent_id)
            return
        pid = pipeline_id or ""
        console.print(f"\n[cyan]{'='*50}[/cyan]")
        console.print(f"[cyan]Starting {task_id}: {task.title}[/cyan]")
        console.print(f"[cyan]{'='*50}[/cyan]")
        await self._emit("task:state_changed", {"task_id": task_id, "state": "in_progress"}, db=db, pipeline_id=pid)
        if getattr(task, "retry_reason", None) == "merge_failed":
            await self._handle_merge_fast_path(db, merge_worker, worktree_mgr, task, task_id, agent_id, pipeline_id)
            return
        worktree_path = await self._prepare_worktree(worktree_mgr, task_id, pid, db, base_ref=merge_worker._main)
        if worktree_path is None:
            await db.release_agent(agent_id)
            return
        pipeline_branch = merge_worker._main
        ok = await self._run_agent(db, runtime, worktree_mgr, task, task_id, agent_id, worktree_path, pid, pipeline_branch=pipeline_branch)
        if not ok:
            await db.release_agent(agent_id)
            return
        # Strip out-of-scope changes before review
        has_in_scope_changes = self._enforce_file_scope(task, worktree_path, pipeline_branch)
        if not has_in_scope_changes:
            console.print(f"[red]{task_id}: all changes were outside file scope[/red]")
            await self._handle_retry(
                db, task_id, worktree_mgr,
                review_feedback=(
                    "ALL your changes were to files outside your allowed scope.\n"
                    f"You are ONLY allowed to modify: {', '.join(task.files)}\n"
                    "Do NOT touch any other files — changes outside scope are automatically reverted."
                ),
                pipeline_id=pid,
            )
            await db.release_agent(agent_id)
            return
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        await self._attempt_merge(db, merge_worker, worktree_mgr, task, task_id, worktree_path, agent_model, pid, pipeline_branch=pipeline_branch)
        await self._cleanup_and_release(db, worktree_mgr, task_id, agent_id)

    # -- merge-only fast path -------------------------------------------

    async def _handle_merge_fast_path(
        self, db, merge_worker, worktree_mgr, task,
        task_id: str, agent_id: str, pipeline_id: str | None,
    ) -> None:
        """Skip agent+review when only the merge previously failed."""
        pid = pipeline_id or ""
        console.print(f"[yellow]{task_id}: merge-only retry — skipping agent + review[/yellow]")
        worktree_path = os.path.join(self._project_dir, ".forge", "worktrees", task_id)
        if not os.path.isdir(worktree_path):
            console.print(f"[red]{task_id}: worktree missing — falling back to full retry[/red]")
            await self._handle_retry(db, task_id, worktree_mgr, pipeline_id=pipeline_id)
            await db.release_agent(agent_id)
            return
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        await db.update_task_state(task_id, TaskState.MERGING.value)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "merging"}, db=db, pipeline_id=pid)
        branch = f"forge/{task_id}"
        # Snapshot pipeline branch BEFORE merge so diff stats reflect only this task's changes
        pre_merge_ref = _resolve_ref(worktree_path, merge_worker._main)
        merge_result = merge_worker.merge(branch, worktree_path=worktree_path)
        if merge_result.success:
            await self._emit_merge_success(db, task_id, pid, worktree_path, pipeline_branch=pre_merge_ref)
        else:
            await self._emit_merge_failure(db, task_id, merge_result.error, pid)
            await self._attempt_merge_with_resolution(
                db, merge_worker, worktree_mgr, merge_result, task_id, worktree_path, branch, agent_model, pid,
                pre_merge_ref=pre_merge_ref,
            )
        await self._cleanup_and_release(db, worktree_mgr, task_id, agent_id)

    # -- worktree creation ----------------------------------------------

    async def _prepare_worktree(self, worktree_mgr, task_id: str, pid: str, db, base_ref: str | None = None) -> str | None:
        """Create or reuse a worktree. Returns path or ``None`` on failure."""
        try:
            return worktree_mgr.create(task_id, base_ref=base_ref)
        except ValueError:
            wt = os.path.join(self._project_dir, ".forge", "worktrees", task_id)
            if os.path.isdir(wt):
                # Reuse the worktree as-is.  The scope gate already stripped
                # out-of-scope changes on the previous run, so only the
                # agent's in-scope work remains.  The retry agent can patch
                # the review issues on top instead of rewriting everything.
                console.print(f"[yellow]{task_id}: reusing worktree for retry (in-scope changes preserved)[/yellow]")
                return wt
            console.print(f"[red]Worktree path doesn't exist for {task_id}[/red]")
        except Exception as exc:
            console.print(f"[red]Worktree creation failed for {task_id}: {exc}[/red]")
        await db.update_task_state(task_id, TaskState.ERROR.value)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "error"}, db=db, pipeline_id=pid)
        return None

    # -- agent execution + streaming + cost -----------------------------

    async def _run_agent(
        self, db, runtime, worktree_mgr, task, task_id: str, agent_id: str,
        worktree_path: str, pid: str, *, pipeline_branch: str | None = None,
    ) -> bool:
        """Run the agent, stream output, track cost. Returns ``True`` on success."""
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        console.print(f"[dim]{task_id}: using {agent_model}[/dim]")
        prompt = self._build_prompt(task)
        await check_budget(db, pid, self._settings)
        result = await self._stream_agent(runtime, agent_id, prompt, worktree_path, task, task_id, pid, db, agent_model)
        if hasattr(result, "cost_usd") and result.cost_usd > 0:
            await db.add_task_agent_cost(task_id, result.cost_usd, result.input_tokens, result.output_tokens)
            await db.add_pipeline_cost(pid, result.cost_usd)
            await self._emit("task:cost_update", {
                "task_id": task_id,
                "agent_cost_usd": result.cost_usd,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }, db=db, pipeline_id=pid)
            total_cost = await db.get_pipeline_cost(pid)
            await self._emit("pipeline:cost_update", {
                "total_cost_usd": total_cost,
            }, db=db, pipeline_id=pid)
        if not result.success:
            console.print(f"[red]{task_id} agent failed: {result.error}[/red]")
            await self._handle_retry(db, task_id, worktree_mgr, pipeline_id=pid)
            return False
        diff = _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)
        if not diff.strip():
            console.print(f"[red]{task_id} agent produced no changes[/red]")
            await self._handle_retry(db, task_id, worktree_mgr, pipeline_id=pid)
            return False
        console.print(f"[green]{task_id} agent completed ({len(diff.splitlines())} diff lines)[/green]")
        if result.files_changed:
            await self._emit("task:files_changed", {"task_id": task_id, "files": result.files_changed}, db=db, pipeline_id=pid)
        return True

    # -- file scope enforcement -------------------------------------------

    def _enforce_file_scope(
        self, task, worktree_path: str, pipeline_branch: str | None,
    ) -> bool:
        """Strip changes to files outside the task's allowed scope.

        Runs after the agent finishes, before review.  Reverts any modified
        files not in ``task.files`` back to the pipeline branch state.

        Returns ``True`` if in-scope changes remain, ``False`` if nothing
        is left (agent only made out-of-scope changes).
        """
        if not pipeline_branch:
            return True  # Can't enforce without a base ref

        allowed = set(task.files or [])
        if not allowed:
            return True  # No file list = no enforcement (safety valve)

        # Get all files changed by the agent vs pipeline branch
        result = subprocess.run(
            ["git", "diff", "--name-only", pipeline_branch, "HEAD"],
            cwd=worktree_path, capture_output=True, text=True,
        )
        changed = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        out_of_scope = [f for f in changed if f not in allowed]

        if not out_of_scope:
            return True  # All changes are in scope

        console.print(
            f"[yellow]  Scope enforcement: reverting {len(out_of_scope)} "
            f"out-of-scope file(s): {', '.join(out_of_scope[:5])}"
            f"{'...' if len(out_of_scope) > 5 else ''}[/yellow]"
        )

        for file in out_of_scope:
            # Restore file to pipeline branch state (works for modified/deleted)
            restore = subprocess.run(
                ["git", "checkout", pipeline_branch, "--", file],
                cwd=worktree_path, capture_output=True,
            )
            if restore.returncode != 0:
                # File was newly created (doesn't exist in base) — remove it
                subprocess.run(
                    ["git", "rm", "-f", file],
                    cwd=worktree_path, capture_output=True,
                )

        # Stage and commit the reverts
        subprocess.run(["git", "add", "-A"], cwd=worktree_path, capture_output=True)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=worktree_path, capture_output=True, text=True,
        )
        if staged.stdout.strip():
            subprocess.run(
                ["git", "commit", "-m", "chore: revert out-of-scope file changes"],
                cwd=worktree_path, capture_output=True,
            )

        # Check if any in-scope changes remain
        remaining = subprocess.run(
            ["git", "diff", "--name-only", pipeline_branch, "HEAD"],
            cwd=worktree_path, capture_output=True, text=True,
        )
        return bool(remaining.stdout.strip())

    # -- post-review merge with Tier 1/Tier 2 --------------------------

    async def _attempt_merge(
        self, db, merge_worker, worktree_mgr, task,
        task_id: str, worktree_path: str, agent_model: str, pid: str,
        *, pipeline_branch: str | None = None,
    ) -> None:
        """Review then merge; handles Tier 1 + Tier 2 conflict resolution."""
        diff = _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)
        await db.update_task_state(task_id, TaskState.IN_REVIEW.value)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "in_review"}, db=db, pipeline_id=pid)
        # Resolve per-pipeline build/test commands for review gates
        pipeline = await db.get_pipeline(pid) if pid else None
        self._pipeline_build_cmd = getattr(pipeline, 'build_cmd', None) if pipeline else None
        self._pipeline_test_cmd = getattr(pipeline, 'test_cmd', None) if pipeline else None
        # Review with automatic re-review on transient failures (empty response,
        # SDK errors) so they don't waste the task's limited retry budget.
        max_re_reviews = 2
        passed, feedback = False, None
        for re_review_attempt in range(max_re_reviews + 1):
            passed, feedback = await self._run_review(task, worktree_path, diff, db=db, pipeline_id=pid, pipeline_branch=pipeline_branch)
            if passed:
                break
            if feedback and "[RETRIABLE]" in feedback and re_review_attempt < max_re_reviews:
                console.print(f"[yellow]{task_id}: transient review failure, re-reviewing ({re_review_attempt + 1}/{max_re_reviews})...[/yellow]")
                continue
            break
        if not passed:
            # Include the rejected diff so the retry agent knows exactly what it wrote
            enriched_feedback = feedback or ""
            if diff:
                diff_snippet = diff[:8000]  # Cap at 8000 chars to save context
                enriched_feedback = (
                    f"=== YOUR REJECTED DIFF ===\n"
                    f"```diff\n{diff_snippet}\n```\n\n"
                    f"=== REVIEWER FEEDBACK ===\n{enriched_feedback}"
                )
            # Store current diff so re-reviewer can compare on next attempt
            await db.set_task_prior_diff(task_id, diff[:10000])
            await self._handle_retry(db, task_id, worktree_mgr, review_feedback=enriched_feedback, pipeline_id=pid)
            return

        # ── Approval gate ─────────────────────────────────────────────
        require_approval = (
            getattr(pipeline, "require_approval", False)
            or getattr(self._settings, "require_approval", False)
        )
        if require_approval:
            await db.update_task_state(task_id, TaskState.AWAITING_APPROVAL.value)
            await self._emit("task:state_changed", {
                "task_id": task_id, "state": "awaiting_approval",
            }, db=db, pipeline_id=pid)
            # Send diff preview (first 200 lines) via WebSocket
            diff_preview = "\n".join(diff.splitlines()[:200])
            await self._emit("task:awaiting_approval", {
                "task_id": task_id,
                "diff_preview": diff_preview,
            }, db=db, pipeline_id=pid)
            # Store approval context so the approve endpoint can resume the merge
            await db.set_task_approval_context(task_id, json.dumps({
                "worktree_path": worktree_path,
                "agent_model": agent_model,
                "pipeline_branch": pipeline_branch,
            }))
            # Do NOT proceed to merge — await human approval.
            # The /approve endpoint triggers the merge. Agent is released by
            # _cleanup_and_release in the caller.
            return

        await db.update_task_state(task_id, TaskState.MERGING.value)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "merging"}, db=db, pipeline_id=pid)
        branch = f"forge/{task_id}"
        # Snapshot pipeline branch BEFORE merge so diff stats reflect only this task's changes
        pre_merge_ref = _resolve_ref(worktree_path, merge_worker._main)
        merge_result = merge_worker.merge(branch, worktree_path=worktree_path)
        if merge_result.success:
            await self._emit_merge_success(db, task_id, pid, worktree_path, pipeline_branch=pre_merge_ref)
            return
        console.print(f"[yellow]{task_id}: trying Tier 1 merge retry (auto-rebase)...[/yellow]")
        await self._emit_merge_failure(db, task_id, merge_result.error, pid)
        retry_result = merge_worker.retry_merge(branch, worktree_path=worktree_path)
        if retry_result.success:
            await self._emit_merge_success(db, task_id, pid, worktree_path, label="on retry", pipeline_branch=pre_merge_ref)
            return
        console.print(f"[red]{task_id} merge retry also failed: {retry_result.error}[/red]")
        await self._attempt_tier2_resolution(
            db, merge_worker, worktree_mgr, retry_result, task_id, worktree_path, branch, agent_model, pid,
            pre_merge_ref=pre_merge_ref,
        )

    # -- Tier 2 conflict resolution -------------------------------------

    async def _attempt_tier2_resolution(
        self, db, merge_worker, worktree_mgr, retry_result,
        task_id: str, worktree_path: str, branch: str, agent_model: str, pid: str,
        pre_merge_ref: str | None = None,
    ) -> None:
        """Tier 2: agent-based conflict resolution."""
        if not retry_result.conflicting_files:
            await self._handle_merge_retry(db, task_id, worktree_mgr, pipeline_id=pid)
            return
        prep = merge_worker.prepare_for_resolution(branch, worktree_path=worktree_path)
        if prep.success:
            await self._try_race_resolved_merge(db, merge_worker, worktree_mgr, task_id, worktree_path, branch, pid, pre_merge_ref=pre_merge_ref)
            return
        resolved = await self._resolve_conflicts(task_id, worktree_path, prep.conflicting_files, agent_model, db=db)
        if resolved:
            final = merge_worker.merge(branch, worktree_path=worktree_path)
            if final.success:
                await self._emit_merge_success(db, task_id, pid, worktree_path, label="after conflict resolution", pipeline_branch=pre_merge_ref)
                return
            merge_worker._abort_rebase(worktree_path)
        else:
            merge_worker._abort_rebase(worktree_path)
        await self._handle_merge_retry(db, task_id, worktree_mgr, pipeline_id=pid)

    async def _attempt_merge_with_resolution(
        self, db, merge_worker, worktree_mgr, merge_result,
        task_id: str, worktree_path: str, branch: str, agent_model: str, pid: str,
        pre_merge_ref: str | None = None,
    ) -> None:
        """Tier 2 for the merge-only fast path."""
        if not merge_result.conflicting_files:
            await self._handle_merge_retry(db, task_id, worktree_mgr, pipeline_id=pid)
            return
        await self._attempt_tier2_resolution(
            db, merge_worker, worktree_mgr, merge_result, task_id, worktree_path, branch, agent_model, pid,
            pre_merge_ref=pre_merge_ref,
        )

    # -- cleanup --------------------------------------------------------

    async def _cleanup_and_release(self, db, worktree_mgr, task_id: str, agent_id: str) -> None:
        """Remove worktree for terminal states and release agent slot."""
        task_after = await db.get_task(task_id)
        if task_after and task_after.state in (TaskState.DONE.value, TaskState.ERROR.value):
            try:
                worktree_mgr.remove(task_id)
            except Exception as exc:
                logger.warning("Worktree cleanup failed for %s: %s", task_id, exc)
        await db.release_agent(agent_id)

    # -- small helpers ---------------------------------------------------

    def _build_prompt(self, task) -> str:
        """Select the correct prompt template for new or retry runs."""
        if task.retry_count > 0 and getattr(task, "review_feedback", None):
            console.print(f"[yellow]{getattr(task, 'id', '?')}: retry {task.retry_count} — including review feedback[/yellow]")
            return _build_retry_prompt(task.title, task.description, task.files, task.review_feedback, task.retry_count)
        return _build_agent_prompt(task.title, task.description, task.files)

    async def _stream_agent(self, runtime, agent_id: str, prompt: str, worktree_path: str, task, task_id: str, pid: str, db, agent_model: str):
        """Run agent with batched streaming callback."""
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
                    await self._emit("task:agent_output", {"task_id": task_id, "line": line}, db=db, pipeline_id=pid)
                _batch.clear()
                _last_flush[0] = now

        # Gather context-sharing data for the agent
        conventions_json = None
        conventions_md = None
        completed_deps: list[dict] = []

        if pid:
            pipeline = await db.get_pipeline(pid)
            if pipeline:
                conventions_json = getattr(pipeline, "conventions_json", None)

        conventions_md = _load_conventions_md(self._project_dir)

        # Collect completed dependency info
        if hasattr(task, "depends_on") and task.depends_on:
            for dep_id in task.depends_on:
                dep_task = await db.get_task(dep_id)
                if dep_task and dep_task.state == TaskState.DONE.value:
                    completed_deps.append({
                        "task_id": dep_task.id,
                        "title": dep_task.title,
                        "implementation_summary": getattr(dep_task, "implementation_summary", None),
                        "files_changed": dep_task.files or [],
                    })

        result = await runtime.run_task(
            agent_id, prompt, worktree_path, task.files,
            allowed_dirs=self._settings.allowed_dirs, model=agent_model, on_message=_on_msg,
            project_context=self._snapshot.format_for_agent() if self._snapshot else "",
            conventions_json=conventions_json,
            conventions_md=conventions_md,
            completed_deps=completed_deps if completed_deps else None,
        )
        for line in _batch:
            await self._emit("task:agent_output", {"task_id": task_id, "line": line}, db=db, pipeline_id=pid)
        _batch.clear()
        return result

    async def _try_race_resolved_merge(
        self, db, merge_worker, worktree_mgr, task_id: str, worktree_path: str,
        branch: str, pid: str, pre_merge_ref: str | None = None,
    ) -> None:
        """Rebase completed cleanly (race resolved) — attempt final merge."""
        ff_result = merge_worker.merge(branch, worktree_path=worktree_path)
        if ff_result.success:
            await self._emit_merge_success(db, task_id, pid, worktree_path, label="Tier 2 prep resolved race", pipeline_branch=pre_merge_ref)
        else:
            await self._handle_merge_retry(db, task_id, worktree_mgr, pipeline_id=pid)

    async def _emit_merge_success(
        self,
        db,
        task_id: str,
        pid: str,
        worktree_path: str,
        *,
        label: str = "",
        pipeline_branch: str | None = None,
    ) -> None:
        """Mark task done and emit merge-success events.

        Args:
            pipeline_branch: The pipeline branch ref (e.g. ``forge/pipeline-abc123``)
                used as the diff base so that stats reflect only *this* task's
                own changes rather than the cumulative total of all previously
                merged tasks.  When ``None``, falls back to the commit-count
                heuristic.
        """
        tag = f" ({label})" if label else ""
        console.print(f"[bold green]{task_id} merged{tag}![/bold green]")
        await db.update_task_state(task_id, TaskState.DONE.value)

        # Extract and store implementation summary for downstream tasks
        task = await db.get_task(task_id)
        agent_summary = getattr(task, "description", "") if task else ""
        # Use the agent result summary if available (stored during agent run)
        # Fall back to task description
        summary = _extract_implementation_summary(worktree_path, agent_summary, pipeline_branch)
        await db.update_task_implementation_summary(task_id, summary)

        stats = _get_diff_stats(worktree_path, pipeline_branch=pipeline_branch)
        await self._emit("task:merge_result", {"task_id": task_id, "success": True, "error": None, **stats}, db=db, pipeline_id=pid)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "done"}, db=db, pipeline_id=pid)

    async def _emit_merge_failure(self, db, task_id: str, error: str | None, pid: str) -> None:
        """Emit merge-failure event (does not change task state)."""
        console.print(f"[red]{task_id} merge failed: {error}[/red]")
        await self._emit("task:merge_result", {"task_id": task_id, "success": False, "error": error}, db=db, pipeline_id=pid)
