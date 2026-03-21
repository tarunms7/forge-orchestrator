"""Merge/conflict/retry mixin extracted from ForgeDaemon."""

from __future__ import annotations

import asyncio
import logging

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.core.logging_config import make_console
from forge.core.models import TaskState
from forge.merge.worktree import WorktreeManager
from forge.storage.db import Database

logger = logging.getLogger("forge")
console = make_console()


class MergeMixin:
    """Mixin supplying merge-related helpers for ForgeDaemon.

    Expects the host class to provide:
        self._settings   – ForgeSettings instance
        self._events     – EventEmitter instance
        self._emit()     – pipeline-aware event emitter (async)
        self._strategy   – merge strategy string
    """

    # ------------------------------------------------------------------
    # Tier 2: conflict resolution via Claude
    # ------------------------------------------------------------------

    async def _resolve_conflicts(
        self,
        task_id: str,
        worktree_path: str,
        conflicting_files: list[str],
        agent_model: str,
        db: Database,
    ) -> bool:
        """Tier 2: Use a targeted Claude call to resolve merge conflicts."""
        if not conflicting_files:
            return False

        console.print(
            f"[yellow]{task_id}: Tier 2 — asking Claude to resolve "
            f"{len(conflicting_files)} conflicts[/yellow]"
        )

        # Look up task context so the resolver understands what this task does
        task = await db.get_task(task_id)
        task_context = ""
        if task:
            task_context = (
                f"## Current Task Context\n"
                f"Task: {task.title}\n"
                f"Description: {task.description}\n\n"
            )

        conflict_prompt = (
            f"{task_context}"
            f"## Situation\n"
            f"This task was developed in parallel with other tasks. The other tasks have "
            f"already been merged to the pipeline branch. When rebasing this task's branch "
            f"onto the updated pipeline branch, merge conflicts arose.\n\n"
            f"A `git rebase` is currently IN PROGRESS and paused on the conflicting commit. "
            f"The following files have conflict markers (<<<<<<, =======, >>>>>>):\n"
            f"{', '.join(conflicting_files)}\n\n"
            f"## Instructions\n"
            f"1. Open each conflicting file listed above\n"
            f"2. You WILL see conflict markers (<<<<<<< HEAD, =======, >>>>>>> commit). "
            f"Resolve them by keeping the intent of BOTH sides.\n"
            f"3. Do not discard either side unless the changes are truly redundant.\n"
            f"4. Ensure the resolved code is syntactically correct and logically coherent.\n"
            f"5. Stage the resolved files and continue the rebase:\n"
            f"   git add -A && git rebase --continue\n"
            f"   IMPORTANT: Do NOT use `git commit`. The rebase is in progress — "
            f"use `git rebase --continue` to advance it.\n"
            f"6. If the rebase pauses again with more conflicts, resolve those too "
            f"and run `git add -A && git rebase --continue` again.\n"
            f"7. Repeat until the rebase completes successfully.\n"
        )

        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)
        result = await runtime.run_task(
            agent_id=f"resolver-{task_id}",
            task_prompt=conflict_prompt,
            worktree_path=worktree_path,
            allowed_files=conflicting_files,
            model=agent_model,
        )

        return result.success

    # ------------------------------------------------------------------
    # Dependency cascade
    # ------------------------------------------------------------------

    async def _cascade_blocked(
        self, db: Database, failed_task_id: str, pipeline_id: str,
    ) -> None:
        """Mark all transitive dependents of a failed task as BLOCKED."""
        from collections import deque
        all_tasks = await db.list_tasks_by_pipeline(pipeline_id)
        newly_blocked: set[str] = set()
        queue: deque[str] = deque([failed_task_id])

        while queue:
            current_id = queue.popleft()
            for task in all_tasks:
                if task.id in newly_blocked:
                    continue
                if task.state not in ("todo", "blocked"):
                    continue
                if current_id in (task.depends_on or []):
                    await db.update_task_state(task.id, "blocked")
                    await self._emit("task:state_changed", {
                        "task_id": task.id,
                        "state": "blocked",
                        "error": f"Blocked: dependency {current_id} failed",
                    }, db=db, pipeline_id=pipeline_id)
                    newly_blocked.add(task.id)
                    queue.append(task.id)

    # ------------------------------------------------------------------
    # Retry logic (general)
    # ------------------------------------------------------------------

    async def _handle_retry(
        self,
        db: Database,
        task_id: str,
        worktree_mgr: WorktreeManager,
        review_feedback: str | None = None,
        pipeline_id: str | None = None,
    ) -> None:
        """Handle task failure: retry up to max_retries, then mark as error.

        Args:
            review_feedback: If provided, stored on the task so the next
                agent run can see what the reviewer flagged and fix it.
        """
        task = await db.get_task(task_id)
        if not task:
            return

        if task.retry_count < self._settings.max_retries:
            console.print(
                f"[yellow]{task_id}: retry {task.retry_count + 1}/{self._settings.max_retries}"
                f" — {'with review feedback' if review_feedback else 'clean retry'}[/yellow]"
            )
            backoff = min(5 * (2 ** task.retry_count), 120)
            logger.info("Retry backoff: waiting %ds before retry %d for %s", backoff, task.retry_count + 1, task_id)
            await asyncio.sleep(backoff)
            await db.retry_task(task_id, review_feedback=review_feedback)
            if pipeline_id:
                await self._emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "retrying"},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                await self._events.emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "retrying"},
                )
            # KEEP the worktree — the retry agent needs the existing code
            # to fix specific review issues instead of rebuilding from scratch.
        else:
            console.print(
                f"[bold red]{task_id}: max retries exceeded, marking as error[/bold red]"
            )
            await db.update_task_state(task_id, TaskState.ERROR.value)
            if pipeline_id:
                await self._cascade_blocked(db, task_id, pipeline_id)
                await self._emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error"},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                await self._events.emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error"},
                )
            # Only clean up worktree when we're done with the task entirely
            try:
                worktree_mgr.remove(task_id)
            except Exception as e:
                logger.warning(
                    "Worktree cleanup failed for %s (_handle_retry): %s",
                    task_id,
                    e,
                )

    # ------------------------------------------------------------------
    # Retry logic (merge-only)
    # ------------------------------------------------------------------

    async def _handle_merge_retry(
        self,
        db: Database,
        task_id: str,
        worktree_mgr: WorktreeManager,
        pipeline_id: str | None = None,
    ) -> None:
        """Handle merge failure: retry merge only (skip agent + review).

        Unlike ``_handle_retry()``, this sets ``retry_reason='merge_failed'``
        so the next ``_execute_task()`` call skips the agent and review
        pipeline and goes directly to the merge step.
        """
        task = await db.get_task(task_id)
        if not task:
            return

        if task.retry_count < self._settings.max_retries:
            console.print(
                f"[yellow]{task_id}: merge-only retry "
                f"{task.retry_count + 1}/{self._settings.max_retries}"
                f" — skipping agent + review[/yellow]"
            )
            backoff = min(5 * (2 ** task.retry_count), 120)
            logger.info("Merge retry backoff: waiting %ds for %s", backoff, task_id)
            await asyncio.sleep(backoff)
            await db.retry_task_for_merge(task_id)
            if pipeline_id:
                await self._emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "retrying"},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                await self._events.emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "retrying"},
                )
            # KEEP the worktree — merge needs the existing code
        else:
            console.print(
                f"[bold red]{task_id}: max retries exceeded, marking as error[/bold red]"
            )
            await db.update_task_state(task_id, TaskState.ERROR.value)
            if pipeline_id:
                await self._cascade_blocked(db, task_id, pipeline_id)
                await self._emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error"},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                await self._events.emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error"},
                )
            try:
                worktree_mgr.remove(task_id)
            except Exception as e:
                logger.warning(
                    "Worktree cleanup failed for %s (_handle_merge_retry): %s",
                    task_id,
                    e,
                )
