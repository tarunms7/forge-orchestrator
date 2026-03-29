"""Merge/conflict/retry mixin extracted from ForgeDaemon."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.core.logging_config import make_console
from forge.core.models import TaskState
from forge.learning.extractor import extract_from_review_feedback
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
                f"## Current Task Context\nTask: {task.title}\nDescription: {task.description}\n\n"
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
            f"   git add -A -- ':(exclude).venv' ':(exclude)venv' ':(exclude)node_modules' ':(exclude)__pycache__' && git rebase --continue\n"
            f"   IMPORTANT: Do NOT use `git commit`. The rebase is in progress — "
            f"use `git rebase --continue` to advance it.\n"
            f"6. If the rebase pauses again with more conflicts, resolve those too "
            f"and run `git add -A -- ':(exclude).venv' ':(exclude)venv' ':(exclude)node_modules' ':(exclude)__pycache__' && git rebase --continue` again.\n"
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
        self,
        db: Database,
        failed_task_id: str,
        pipeline_id: str,
    ) -> None:
        """Mark all transitive dependents of a failed task as BLOCKED."""
        from collections import deque

        all_tasks = await db.list_tasks_by_pipeline(pipeline_id)

        # Build inverse dependency map: parent_id -> list of dependent task IDs
        # This turns the O(N²) inner scan into O(1) lookups per BFS step
        dependents_map: dict[str, list] = {}
        blockable_tasks: dict[str, object] = {}
        for task in all_tasks:
            if task.state in ("todo", "blocked"):
                blockable_tasks[task.id] = task
                for dep_id in task.depends_on or []:
                    dependents_map.setdefault(dep_id, []).append(task.id)

        newly_blocked: set[str] = set()
        queue: deque[str] = deque([failed_task_id])

        while queue:
            current_id = queue.popleft()
            for dependent_id in dependents_map.get(current_id, []):
                if dependent_id in newly_blocked:
                    continue
                if dependent_id not in blockable_tasks:
                    continue
                try:
                    await db.update_task_state(dependent_id, "blocked")
                    await self._emit(
                        "task:state_changed",
                        {
                            "task_id": dependent_id,
                            "state": "blocked",
                            "error": f"Blocked: dependency {current_id} failed",
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to mark task %s as blocked (dep %s failed)",
                        dependent_id,
                        current_id,
                    )
                # Always add to newly_blocked to cascade further, even if DB failed
                newly_blocked.add(dependent_id)
                queue.append(dependent_id)

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
            backoff = min(5 * (2**task.retry_count), 120)
            logger.info(
                "Retry backoff: waiting %ds before retry %d for %s",
                backoff,
                task.retry_count + 1,
                task_id,
            )
            await asyncio.sleep(backoff)
            await db.retry_task(task_id, review_feedback=review_feedback)
            # Extract lesson from review feedback if this is a repeated failure
            if (
                review_feedback
                and task.retry_count >= 2
                and not review_feedback.startswith("[INFRASTRUCTURE CRASH]")
            ):  # 3rd+ attempt
                try:
                    lesson = extract_from_review_feedback(
                        feedback=review_feedback,
                        task_title=getattr(task, "title", ""),
                        project_dir=getattr(self, "_project_dir", None),
                    )
                    existing = await db.find_matching_lesson(
                        lesson.trigger, project_dir=getattr(self, "_project_dir", None)
                    )
                    if existing:
                        await db.bump_lesson_hit(existing.id)
                    else:
                        await db.add_lesson(
                            scope=lesson.scope,
                            category=lesson.category,
                            title=lesson.title,
                            content=lesson.content,
                            trigger=lesson.trigger,
                            resolution=lesson.resolution,
                            project_dir=getattr(self, "_project_dir", None)
                            if lesson.scope == "project"
                            else None,
                            confidence=0.3,
                        )
                    logger.info("Review lesson captured: %s", lesson.title)
                except Exception as exc:
                    logger.warning("Failed to capture review lesson: %s", exc)
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
            console.print(f"[bold red]{task_id}: max retries exceeded, marking as error[/bold red]")
            await db.update_task_state(task_id, TaskState.ERROR.value)
            # Record error and completion time
            try:
                error_msg = "Max retries exceeded"
                if review_feedback:
                    error_msg = f"Max retries exceeded. Last feedback: {review_feedback[:500]}"
                await db.set_task_error(task_id, error_msg)
                await db.set_task_timing(task_id, completed_at=datetime.now(UTC).isoformat())
            except Exception:
                logger.debug("Failed to record error/timing for %s", task_id, exc_info=True)
            if pipeline_id:
                await self._cascade_blocked(db, task_id, pipeline_id)
                await self._emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error", "error": error_msg},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                await self._events.emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error", "error": error_msg},
                )
            # Only clean up worktree when we're done with the task entirely
            try:
                await worktree_mgr.async_remove(task_id)
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
            backoff = min(5 * (2**task.retry_count), 120)
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
            console.print(f"[bold red]{task_id}: max retries exceeded, marking as error[/bold red]")
            await db.update_task_state(task_id, TaskState.ERROR.value)
            # Record error and completion time
            try:
                await db.set_task_error(task_id, "Max merge retries exceeded")
                await db.set_task_timing(task_id, completed_at=datetime.now(UTC).isoformat())
            except Exception:
                logger.debug("Failed to record error/timing for %s", task_id, exc_info=True)
            merge_error_msg = "Max merge retries exceeded"
            if pipeline_id:
                await self._cascade_blocked(db, task_id, pipeline_id)
                await self._emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error", "error": merge_error_msg},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                await self._events.emit(
                    "task:state_changed",
                    {"task_id": task_id, "state": "error", "error": merge_error_msg},
                )
            try:
                await worktree_mgr.async_remove(task_id)
            except Exception as e:
                logger.warning(
                    "Worktree cleanup failed for %s (_handle_merge_retry): %s",
                    task_id,
                    e,
                )
