"""ExecutorMixin — decomposed _execute_task extracted from ForgeDaemon."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from forge.core.budget import check_budget
from forge.core.sanitize import validate_task_id
from forge.core.daemon_helpers import (
    _build_agent_prompt,
    _build_retry_prompt,
    _extract_activity,
    _extract_implementation_summary,
    _find_related_test_files,
    _get_changed_files_vs_main,
    _get_diff_stats,
    _get_diff_vs_main,
    _load_conventions_md,
    _parse_forge_question,
    _resolve_ref,
    _run_git,
)
from forge.core.logging_config import make_console
from forge.core.model_router import select_model
from forge.core.models import TaskState
from forge.learning.guard import RuntimeGuard, GuardTriggered
from forge.learning.store import format_lessons_block, row_to_lesson
from forge.learning.extractor import extract_from_command_failures

logger = logging.getLogger("forge")
console = make_console()

_COMPLEXITY_MULTIPLIERS: dict[str, float] = {
    "low": 1.0,
    "medium": 1.5,
    "high": 2.0,
}


def _complexity_timeout(base_seconds: int, complexity: str | None) -> int:
    """Scale agent timeout by task complexity."""
    multiplier = _COMPLEXITY_MULTIPLIERS.get(complexity or "medium", 1.5)
    return int(base_seconds * multiplier)


class ExecutorMixin:
    """Mixin providing the decomposed ``_execute_task`` pipeline.

    Host class must supply: ``_project_dir``, ``_strategy``, ``_snapshot``,
    ``_settings``, ``_emit``, ``_run_review``, ``_resolve_conflicts``,
    ``_handle_retry``, ``_handle_merge_retry``.
    """

    def _build_project_context(self) -> str:
        """Build project context string from snapshot + forge.toml instructions."""
        parts = []
        if self._snapshot:
            parts.append(self._snapshot.format_for_agent())
        # Inject user instructions from .forge/forge.toml
        instructions = getattr(getattr(self, "_project_config", None), "instructions", "")
        if instructions:
            parts.append(f"## User Instructions (from forge.toml)\n\n{instructions}")
        return "\n\n".join(parts)

    # -- orchestrator ----------------------------------------------------

    async def _execute_task(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, pipeline_id: str | None = None,
        repo_id: str = "default",
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
        # Use repo_id from DB task row if available, else use parameter default.
        repo_id = getattr(task, "repo_id", repo_id) or repo_id
        if getattr(task, "retry_reason", None) == "merge_failed":
            await self._handle_merge_fast_path(db, merge_worker, worktree_mgr, task, task_id, agent_id, pipeline_id, repo_id=repo_id)
            return
        worktree_path = await self._prepare_worktree(worktree_mgr, task_id, pid, db, base_ref=merge_worker._main, repo_id=repo_id)
        if worktree_path is None:
            await db.release_agent(agent_id)
            return
        pipeline_branch = merge_worker._main
        # Snapshot HEAD before retry agent runs so we can compute the
        # delta diff (what the retry agent actually changed) for review.
        pre_retry_ref = None
        if task.retry_count > 0:
            snap = await _run_git(
                ["rev-parse", "HEAD"], cwd=worktree_path,
                check=False, description="snapshot pre-retry ref",
            )
            if snap.returncode == 0:
                pre_retry_ref = snap.stdout.strip()
        agent_result = await self._run_agent(db, runtime, worktree_mgr, task, task_id, agent_id, worktree_path, pid, pipeline_branch=pipeline_branch)
        if agent_result is None:
            await db.release_agent(agent_id)
            return

        # Check if the agent paused to ask a question
        question_data = _parse_forge_question(agent_result.summary)
        if question_data:
            await self._handle_agent_question(
                db, task_id, agent_id, pipeline_id=pid,
                question_data=question_data,
                session_id=agent_result.session_id,
            )
            return

        # Check for pending human interjections before review
        interjection_delivered, final_session = await self._deliver_interjections(
            db=db, runtime=runtime, worktree_mgr=worktree_mgr,
            task_id=task_id, task=task, agent_id=agent_id,
            worktree_path=worktree_path, pipeline_id=pid,
            session_id=agent_result.session_id,
            pipeline_branch=pipeline_branch,
        )
        # If _deliver_interjections handled a question, the task is paused — stop here
        if interjection_delivered and final_session != agent_result.session_id:
            # Check if a question was asked (agent released inside _deliver_interjections)
            task_after = await db.get_task(task_id)
            if task_after and task_after.state == "awaiting_input":
                return

        # Strip out-of-scope changes before review
        has_in_scope_changes, reverted_files = await self._enforce_file_scope(
            task, worktree_path, pipeline_branch,
        )
        if not has_in_scope_changes:
            if not reverted_files:
                # Agent made zero changes and nothing was reverted —
                # the task legitimately required no modifications.
                # Mark done directly (skip review and merge).
                console.print(f"[bold green]{task_id}: no changes needed — marking done[/bold green]")
                await db.update_task_state(task_id, TaskState.DONE.value)
                await self._emit("task:state_changed", {"task_id": task_id, "state": "done"}, db=db, pipeline_id=pid)
                await db.release_agent(agent_id)
                return
            console.print(f"[red]{task_id}: all changes were outside file scope[/red]")
            reverted_list = "\n".join(f"  - {f}" for f in reverted_files)
            await self._handle_retry(
                db, task_id, worktree_mgr,
                review_feedback=(
                    "ALL your changes were to files outside your allowed scope "
                    "and have been REVERTED by the system.\n\n"
                    f"Files you modified that were REVERTED:\n{reverted_list}\n\n"
                    f"You are ONLY allowed to modify these files:\n"
                    + "\n".join(f"  - {f}" for f in task.files)
                    + "\n\nDo NOT touch any other files. Focus ONLY on the files listed above."
                ),
                pipeline_id=pid,
            )
            await db.release_agent(agent_id)
            return
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        await self._attempt_merge(
            db, merge_worker, worktree_mgr, task, task_id, worktree_path,
            agent_model, pid, pipeline_branch=pipeline_branch,
            pre_retry_ref=pre_retry_ref,
            agent_summary=agent_result.summary if agent_result else "",
        )
        await self._cleanup_and_release(db, worktree_mgr, task_id, agent_id)

    # -- merge-only fast path -------------------------------------------

    async def _handle_merge_fast_path(
        self, db, merge_worker, worktree_mgr, task,
        task_id: str, agent_id: str, pipeline_id: str | None,
        repo_id: str = "default",
    ) -> None:
        """Skip agent+review when only the merge previously failed."""
        pid = pipeline_id or ""
        console.print(f"[yellow]{task_id}: merge-only retry — skipping agent + review[/yellow]")
        worktree_path = self._worktree_path(repo_id, task_id)
        if not os.path.isdir(worktree_path):
            console.print(f"[red]{task_id}: worktree missing — falling back to full retry[/red]")
            await self._handle_retry(db, task_id, worktree_mgr, pipeline_id=pipeline_id)
            await db.release_agent(agent_id)
            return
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        await db.update_task_state(task_id, TaskState.MERGING.value)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "merging"}, db=db, pipeline_id=pid)
        branch = f"forge/{validate_task_id(task_id)}"
        # Ensure worktree is clean before merge — commits staged changes,
        # stashes untracked files, so rebase never hits "uncommitted changes"
        await self._ensure_clean_for_rebase(worktree_path, task_id)
        # Snapshot pipeline branch BEFORE merge so diff stats reflect only this task's changes
        pre_merge_ref = await _resolve_ref(worktree_path, merge_worker._main)
        async with self._merge_lock:
            merge_result = await merge_worker.merge(branch, worktree_path=worktree_path)
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

    async def _prepare_worktree(self, worktree_mgr, task_id: str, pid: str, db, base_ref: str | None = None, repo_id: str = "default") -> str | None:
        """Create or reuse a worktree. Returns path or ``None`` on failure."""
        try:
            return worktree_mgr.create(task_id, base_ref=base_ref)
        except ValueError:
            wt = self._worktree_path(repo_id, task_id)
            if os.path.isdir(wt):
                # Reuse the worktree as-is.  The scope gate already stripped
                # out-of-scope changes on the previous run, so only the
                # agent's in-scope work remains.  The retry agent can patch
                # the review issues on top instead of rewriting everything.
                console.print(f"[yellow]{task_id}: reusing worktree for retry (in-scope changes preserved)[/yellow]")
                # Rebase onto latest pipeline branch to pick up changes
                # merged by sibling tasks since this worktree was created.
                # This eliminates "ghost diffs" where the diff shows
                # deletions of lines added by other tasks.
                if base_ref:
                    await self._rebase_worktree(wt, base_ref, task_id)
                return wt
            console.print(f"[red]Worktree path doesn't exist for {task_id}[/red]")
        except Exception as exc:
            console.print(f"[red]Worktree creation failed for {task_id}: {exc}[/red]")
        await db.update_task_state(task_id, TaskState.ERROR.value)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "error"}, db=db, pipeline_id=pid)
        return None

    async def _rebase_worktree(self, worktree_path: str, base_ref: str, task_id: str) -> None:
        """Rebase the worktree branch onto the latest pipeline branch.

        Best-effort: if the rebase conflicts, abort and continue with
        the un-rebased worktree.  The merge step will handle conflicts
        later.  This is preferable to failing the retry entirely.
        """
        # Ensure worktree is clean — rebase refuses if index or working tree is dirty
        await self._ensure_clean_for_rebase(worktree_path, task_id)

        result = await _run_git(
            ["rebase", base_ref], cwd=worktree_path,
            check=False, description="rebase worktree",
        )
        if result.returncode == 0:
            console.print(f"[green]  {task_id}: worktree rebased onto {base_ref}[/green]")
        else:
            # Abort the failed rebase so the worktree is usable
            await _run_git(
                ["rebase", "--abort"], cwd=worktree_path,
                check=False, description="abort rebase",
            )
            console.print(
                f"[yellow]  {task_id}: rebase onto {base_ref} had conflicts — "
                f"continuing with un-rebased worktree[/yellow]"
            )

    # -- agent execution + streaming + cost -----------------------------

    async def _run_agent(
        self, db, runtime, worktree_mgr, task, task_id: str, agent_id: str,
        worktree_path: str, pid: str, *, pipeline_branch: str | None = None,
        resume: str | None = None, prompt_override: str | None = None,
    ):
        """Run the agent, stream output, track cost.

        Returns the ``AgentResult`` on success, or ``None`` on failure (after
        scheduling a retry).  Callers must check the result for ``None`` before
        proceeding.

        Args:
            prompt_override: When provided, overrides the task-derived prompt.
                Used by ``_resume_task`` to send the human's answer as the next
                user message when resuming a paused conversation.
            resume: SDK session ID for conversation continuation (``ClaudeCodeOptions.resume``).
        """
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        console.print(f"[dim]{task_id}: using {agent_model}[/dim]")
        prompt = prompt_override if prompt_override is not None else self._build_prompt(task)
        await check_budget(db, pid, self._settings)
        result = await self._stream_agent(runtime, agent_id, prompt, worktree_path, task, task_id, pid, db, agent_model, resume=resume)
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
            return None

        # Safety net: commit any uncommitted changes left by the agent.
        # The agent is instructed to commit, but may fail to do so if the
        # SDK session was sandboxed or the agent simply forgot. Without
        # this, uncommitted changes are invisible to the merge pipeline.
        await self._auto_commit_if_needed(worktree_path, task_id, task_title=getattr(task, 'title', ''))

        diff = await _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)
        if not diff.strip():
            # A FORGE_QUESTION with no diff is valid — the agent stopped to ask.
            # Detect it before declaring failure; the caller checks summary after return.
            question_data = _parse_forge_question(result.summary)
            if not question_data:
                console.print(f"[red]{task_id} agent produced no changes[/red]")
                await self._handle_retry(db, task_id, worktree_mgr, pipeline_id=pid)
                return None
        console.print(f"[green]{task_id} agent completed ({len(diff.splitlines())} diff lines)[/green]")
        if result.files_changed:
            await self._emit("task:files_changed", {"task_id": task_id, "files": result.files_changed}, db=db, pipeline_id=pid)
        return result

    # -- infrastructure file cleanup --------------------------------------

    # Files written by Forge into the worktree that must NEVER be staged,
    # committed, or included in diffs.  They are invisible to the agent's
    # work product.

    @staticmethod
    async def _ensure_clean_for_rebase(worktree_path: str, task_id: str) -> None:
        """Ensure the worktree has no uncommitted changes before rebase.

        git rebase refuses to run if the index or working tree is dirty.
        This can happen when:
        - The agent's git commit failed (pre-commit hook, sandbox, etc.)
          leaving staged files in the index
        - Forge infrastructure files (.claude/settings.json) are present
          as untracked or modified files
        - Scope enforcement left residual changes

        Strategy: unstage infra files, then commit any remaining staged
        changes with --no-verify, then stash any remaining working tree
        dirt (untracked files, modifications).
        """
        # 1. If anything is staged, commit it (agent work that wasn't committed)
        staged = await _run_git(
            ["diff", "--cached", "--quiet"],
            cwd=worktree_path, check=False,
            description="check staged changes",
        )
        if staged.returncode != 0:
            # There are staged changes — commit them
            logger.info("%s: committing leftover staged changes before rebase", task_id)
            await _run_git(
                ["commit", "--no-verify", "-m", f"chore({task_id}): commit staged changes before merge"],
                cwd=worktree_path, check=False,
                description="commit staged before rebase",
            )

        # 2. Stash any remaining working tree dirt (untracked files, modifications)
        status = await _run_git(
            ["status", "--porcelain"],
            cwd=worktree_path, check=False,
            description="check working tree",
        )
        if status.stdout.strip():
            await _run_git(
                ["stash", "push", "--include-untracked", "-m", "forge-pre-rebase-cleanup"],
                cwd=worktree_path, check=False,
                description="stash working tree before rebase",
            )

    # -- auto-commit safety net -------------------------------------------

    @staticmethod
    async def _auto_commit_if_needed(worktree_path: str, task_id: str, task_title: str = "") -> bool:
        """Commit any uncommitted agent changes as a safety net.

        The agent is instructed to commit its own work, but may fail to if:
        - The SDK session was sandboxed and Bash commands were blocked
        - The agent ran out of turns before committing
        - The agent simply forgot

        Without this, uncommitted changes are invisible to the entire merge
        pipeline (diff, review, rebase, fast-forward) and the task silently
        produces nothing.

        Returns True if a commit was made, False otherwise.
        """
        # Check for uncommitted changes (staged + unstaged + untracked new files)
        status = await _run_git(
            ["status", "--porcelain"],
            cwd=worktree_path, check=False,
            description="check uncommitted changes",
        )
        if status.returncode != 0 or not status.stdout.strip():
            return False

        # There are uncommitted changes — commit them
        logger.info(
            "%s: agent left uncommitted changes, auto-committing", task_id,
        )
        console.print(
            f"[yellow]{task_id}: auto-committing uncommitted agent changes[/yellow]",
        )

        # Stage everything (including new files the agent created)
        add_result = await _run_git(
            ["add", "-A"],
            cwd=worktree_path, check=False,
            description="auto-stage agent changes",
        )
        if add_result.returncode != 0:
            logger.warning(
                "%s: git add failed during auto-commit: %s",
                task_id, add_result.stderr.strip(),
            )
            return False

        # Build a descriptive commit message from the task title
        if task_title:
            # Truncate to 72 chars for conventional commit format
            short_title = task_title[:65].rstrip()
            commit_msg = f"feat({task_id}): {short_title}"
        else:
            commit_msg = f"feat({task_id}): implement task changes"

        commit_result = await _run_git(
            ["commit", "--no-verify", "-m", commit_msg],
            cwd=worktree_path, check=False,
            description="auto-commit agent changes",
        )
        if commit_result.returncode != 0:
            logger.warning(
                "%s: git commit failed during auto-commit: %s",
                task_id, commit_result.stderr.strip(),
            )
            return False

        logger.info("%s: auto-commit succeeded", task_id)
        return True

    # -- question handling (pause / resume) --------------------------------

    async def _handle_agent_question(
        self, db, task_id: str, agent_id: str,
        question_data: dict, session_id: str | None,
        pipeline_id: str | None = None,
    ) -> None:
        """Persist a FORGE_QUESTION and transition the task to awaiting_input.

        The agent slot is released so the scheduler can pick up other tasks
        while waiting for the human's answer.  The *session_id* is stored on
        the task row so :meth:`_resume_task` can continue the conversation.
        """
        pid = pipeline_id or ""

        # Persist the question
        q = await db.create_task_question(
            task_id=task_id,
            pipeline_id=pid,
            question=question_data["question"],
            suggestions=question_data.get("suggestions"),
            context=question_data.get("context"),
        )

        # Store session_id and increment questions_asked counter
        if session_id:
            async with db._session_factory() as session:
                from forge.storage.db import TaskRow
                task_row = await session.get(TaskRow, task_id)
                if task_row:
                    task_row.session_id = session_id
                    task_row.questions_asked = (task_row.questions_asked or 0) + 1
                    await session.commit()
        else:
            # No session_id: still increment the counter
            async with db._session_factory() as session:
                from forge.storage.db import TaskRow
                task_row = await session.get(TaskRow, task_id)
                if task_row:
                    task_row.questions_asked = (task_row.questions_asked or 0) + 1
                    await session.commit()

        # Transition state
        await db.update_task_state(task_id, TaskState.AWAITING_INPUT.value)
        await self._emit("task:state_changed", {
            "task_id": task_id, "state": "awaiting_input",
        }, db=db, pipeline_id=pid)

        # Emit the question event for the UI / API consumers
        await self._emit("task:question", {
            "task_id": task_id,
            "question": {
                "id": q.id,
                "question": q.question,
                "suggestions": question_data.get("suggestions", []),
                "context": question_data.get("context"),
            },
        }, db=db, pipeline_id=pid)

        # Release the agent slot — no subprocess running while paused
        await db.release_agent(agent_id)

    # -- interjection delivery --------------------------------------------

    async def _deliver_interjections(
        self, db, runtime, worktree_mgr, task_id: str, task, agent_id: str,
        worktree_path: str, pipeline_id: str, session_id: str | None,
        pipeline_branch: str | None = None,
    ) -> tuple[bool, str | None]:
        """Check for and deliver pending interjections to a running agent.

        Returns (was_delivered, latest_session_id).
        Must be called after _run_agent() returns and BEFORE _enforce_file_scope().
        """
        delivered_any = False
        current_session = session_id
        max_rounds = 5  # Prevent unbounded loop if interjections arrive faster than processing

        for _round in range(max_rounds):
            interjections = await db.get_pending_interjections(task_id)
            if not interjections:
                break

            combined = "\n\n".join(
                f"Human message: {ij.message}" for ij in interjections
            )
            prompt = (
                f"The human has sent you a message while you were working:\n\n"
                f"{combined}\n\n"
                f"Read their input carefully. Adjust your approach if needed, "
                f"then continue working on the task."
            )

            for ij in interjections:
                await db.mark_interjection_delivered(ij.id)

            agent_result = await self._run_agent(
                db, runtime, worktree_mgr, task, task_id, agent_id,
                worktree_path, pipeline_id, pipeline_branch=pipeline_branch,
                resume=current_session, prompt_override=prompt,
            )

            if agent_result is None:
                break

            delivered_any = True
            current_session = agent_result.session_id

            # If agent asked a question in response, handle it and return
            question_data = _parse_forge_question(agent_result.summary)
            if question_data:
                await self._handle_agent_question(
                    db, task_id, agent_id, pipeline_id=pipeline_id,
                    question_data=question_data,
                    session_id=agent_result.session_id,
                )
                return True, current_session

        return delivered_any, current_session

    async def _resume_task(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, answer: str, pipeline_id: str | None = None,
        repo_id: str = "default",
    ) -> None:
        """Resume a task after a human answered a FORGE_QUESTION.

        The human's *answer* is sent as the new prompt to the SDK, which
        continues the prior conversation via ``resume=session_id``.  After
        the agent returns, question-detection runs again — the agent may ask
        another follow-up question, or proceed to finish the task.
        """
        pid = pipeline_id or ""
        task = await db.get_task(task_id)
        if not task or task.state != TaskState.AWAITING_INPUT.value:
            logger.warning("_resume_task: task %s not in awaiting_input (got %s)", task_id, getattr(task, "state", None))
            return

        session_id = getattr(task, "session_id", None)

        # Transition back to in_progress
        await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)
        await self._emit("task:state_changed", {
            "task_id": task_id, "state": "in_progress",
        }, db=db, pipeline_id=pid)
        await self._emit("task:resumed", {"task_id": task_id}, db=db, pipeline_id=pid)

        # Resolve worktree path (reuse existing — the agent's code is still there)
        worktree_path = self._worktree_path(repo_id, task_id)
        if not os.path.isdir(worktree_path):
            console.print(f"[red]{task_id}: worktree missing on resume — scheduling full retry[/red]")
            await self._handle_retry(db, task_id, worktree_mgr, pipeline_id=pid)
            await db.release_agent(agent_id)
            return

        pipeline_branch = merge_worker._main

        # Re-run the agent: the human's answer becomes the new user message,
        # and resume=session_id restores the prior conversation context.
        agent_result = await self._run_agent(
            db, runtime, worktree_mgr, task, task_id, agent_id,
            worktree_path, pid, pipeline_branch=pipeline_branch,
            resume=session_id, prompt_override=answer,
        )

        if agent_result is None:
            # _run_agent already handled the retry/release
            return

        # Another question?
        question_data = _parse_forge_question(agent_result.summary)
        if question_data:
            await self._handle_agent_question(
                db, task_id, agent_id, pipeline_id=pid,
                question_data=question_data,
                session_id=agent_result.session_id,
            )
            return

        # Check for pending human interjections before review
        interjection_delivered, final_session = await self._deliver_interjections(
            db=db, runtime=runtime, worktree_mgr=worktree_mgr,
            task_id=task_id, task=task, agent_id=agent_id,
            worktree_path=worktree_path, pipeline_id=pid,
            session_id=agent_result.session_id,
            pipeline_branch=pipeline_branch,
        )
        if interjection_delivered and final_session != agent_result.session_id:
            task_after = await db.get_task(task_id)
            if task_after and task_after.state == "awaiting_input":
                return

        # Agent finished — proceed to review
        has_in_scope_changes, reverted_files = await self._enforce_file_scope(
            task, worktree_path, pipeline_branch,
        )
        if not has_in_scope_changes:
            if not reverted_files:
                # Agent made zero changes and nothing was reverted —
                # the task legitimately required no modifications.
                console.print(f"[bold green]{task_id}: no changes needed — marking done (after resume)[/bold green]")
                await db.update_task_state(task_id, TaskState.DONE.value)
                await self._emit("task:state_changed", {"task_id": task_id, "state": "done"}, db=db, pipeline_id=pid)
                await db.release_agent(agent_id)
                return
            console.print(f"[red]{task_id}: all changes were outside file scope (after resume)[/red]")
            reverted_list = "\n".join(f"  - {f}" for f in reverted_files)
            await self._handle_retry(
                db, task_id, worktree_mgr,
                review_feedback=(
                    "ALL your changes were to files outside your allowed scope "
                    "and have been REVERTED by the system.\n\n"
                    f"Files you modified that were REVERTED:\n{reverted_list}\n\n"
                    f"You are ONLY allowed to modify these files:\n"
                    + "\n".join(f"  - {f}" for f in task.files)
                    + "\n\nDo NOT touch any other files. Focus ONLY on the files listed above."
                ),
                pipeline_id=pid,
            )
            await db.release_agent(agent_id)
            return

        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        await self._attempt_merge(
            db, merge_worker, worktree_mgr, task, task_id, worktree_path,
            agent_model, pid, pipeline_branch=pipeline_branch,
            agent_summary=agent_result.summary if agent_result else "",
        )
        await self._cleanup_and_release(db, worktree_mgr, task_id, agent_id)

    # -- event-driven resume ------------------------------------------------

    async def _on_task_answered(
        self, data: dict, db,
    ) -> None:
        """Handle task:answer event -- resume a task after human answers a question."""
        task_id = data.get("task_id")
        answer = data.get("answer")
        pipeline_id = data.get("pipeline_id", "")
        if not task_id or not answer:
            return

        task = await db.get_task(task_id)
        if not task or task.state != TaskState.AWAITING_INPUT.value:
            logger.debug(
                "_on_task_answered: task %s not awaiting_input (state=%s)",
                task_id, getattr(task, "state", None),
            )
            return

        # Skip if task is already being resumed (in active pool)
        async with self._active_tasks_lock:
            if task_id in self._active_tasks:
                logger.debug("_on_task_answered: task %s already active, skipping", task_id)
                return

        # Acquire an agent slot via Scheduler
        from forge.core.scheduler import Scheduler
        from forge.core.models import row_to_agent, row_to_record

        prefix = pipeline_id[:8] if pipeline_id else None
        agents = await db.list_agents(prefix=prefix)
        agent_records = [row_to_agent(a) for a in agents]
        tasks = await (
            db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks()
        )
        task_records = [row_to_record(t) for t in tasks]
        dispatch_plan = Scheduler.dispatch_plan(
            task_records, agent_records, self._effective_max_agents,
        )

        agent_id = None
        for tid, aid in dispatch_plan:
            if tid == task_id:
                agent_id = aid
                break

        if not agent_id:
            logger.info(
                "_on_task_answered: no slot available for %s, will retry on next cycle",
                task_id,
            )
            return

        await db.assign_task(task_id, agent_id)
        logger.info("Resuming task %s after human answer (agent=%s)", task_id, agent_id)

        atask = asyncio.create_task(
            self._safe_execute_resume(
                db, self._runtime, self._worktree_mgr, self._merge_worker,
                task_id, agent_id, answer, pipeline_id,
            ),
            name=f"forge-resume-{task_id}",
        )
        async with self._active_tasks_lock:
            self._active_tasks[task_id] = atask

    async def _safe_execute_resume(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, answer: str, pipeline_id: str | None = None,
        repo_id: str = "default",
    ) -> None:
        """Safe wrapper around _resume_task with cleanup on error."""
        try:
            await self._resume_task(
                db, runtime, worktree_mgr, merge_worker,
                task_id, agent_id, answer, pipeline_id,
                repo_id=repo_id,
            )
        except asyncio.CancelledError:
            logger.info("Resume of %s was cancelled", task_id)
        except Exception as e:
            logger.error("Resume of %s crashed: %s", task_id, e, exc_info=True)
            try:
                # Mark as ERROR (not AWAITING_INPUT) so the pipeline can terminate.
                # AWAITING_INPUT with no pending question creates a zombie task.
                await db.update_task_state(task_id, TaskState.ERROR.value)
                await db.release_agent(agent_id)
            except Exception:
                logger.exception("Failed to clean up after resume crash for %s", task_id)
        finally:
            async with self._active_tasks_lock:
                self._active_tasks.pop(task_id, None)

    # -- file scope enforcement -------------------------------------------

    async def _enforce_file_scope(
        self, task, worktree_path: str, pipeline_branch: str | None,
    ) -> tuple[bool, list[str]]:
        """Strip changes to files outside the task's allowed scope.

        Runs after the agent finishes, before review.  Reverts any modified
        files not in ``task.files`` back to the pipeline branch state.

        Returns a tuple of (has_in_scope_changes, reverted_files).
        ``has_in_scope_changes`` is True if in-scope changes remain,
        False if nothing is left.  ``reverted_files`` lists the files
        that were reverted (empty if nothing was out of scope).
        """
        if not pipeline_branch:
            return True, []  # Can't enforce without a base ref

        allowed = set(task.files or [])
        if not allowed:
            return True, []  # No file list = no enforcement (safety valve)

        # Get all files changed by the agent vs pipeline branch
        result = await _run_git(
            ["diff", "--name-only", pipeline_branch, "HEAD"],
            cwd=worktree_path, check=False, description="scope diff",
        )
        changed = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

        # Exempt test files that are related to in-scope source files.
        # Agents often need to create/modify test files for the source files
        # they're working on — those shouldn't be reverted.
        related_tests = set(
            await _find_related_test_files(worktree_path, list(allowed))
        )
        # Always exempt .claude/ and .forge/ — these are Forge infrastructure
        # files (e.g. agent permissions), not agent work product.
        infra_prefixes = (".claude/", ".forge/")
        out_of_scope = [
            f for f in changed
            if f not in allowed
            and f not in related_tests
            and not any(f.startswith(p) for p in infra_prefixes)
        ]

        if not out_of_scope:
            return True, []  # All changes are in scope

        console.print(
            f"[yellow]  Scope enforcement: reverting {len(out_of_scope)} "
            f"out-of-scope file(s): {', '.join(out_of_scope[:5])}"
            f"{'...' if len(out_of_scope) > 5 else ''}[/yellow]"
        )

        for file in out_of_scope:
            # Restore file to pipeline branch state (works for modified/deleted)
            restore = await _run_git(
                ["checkout", pipeline_branch, "--", file],
                cwd=worktree_path, check=False, description="restore out-of-scope",
            )
            if restore.returncode != 0:
                # File was newly created (doesn't exist in base) — remove it
                await _run_git(
                    ["rm", "-f", file],
                    cwd=worktree_path, check=False, description="rm out-of-scope",
                )

        # Stage and commit the reverts
        await _run_git(["add", "-A"], cwd=worktree_path, check=False, description="stage scope reverts")
        staged = await _run_git(
            ["diff", "--cached", "--name-only"],
            cwd=worktree_path, check=False, description="check staged",
        )
        if staged.stdout.strip():
            await _run_git(
                ["commit", "--no-verify", "-m", "chore: revert out-of-scope file changes"],
                cwd=worktree_path, check=False, description="commit scope reverts",
            )

        # Check if any in-scope changes remain
        remaining = await _run_git(
            ["diff", "--name-only", pipeline_branch, "HEAD"],
            cwd=worktree_path, check=False, description="check remaining",
        )
        return bool(remaining.stdout.strip()), out_of_scope

    # -- post-review merge with Tier 1/Tier 2 --------------------------

    async def _attempt_merge(
        self, db, merge_worker, worktree_mgr, task,
        task_id: str, worktree_path: str, agent_model: str, pid: str,
        *, pipeline_branch: str | None = None,
        pre_retry_ref: str | None = None,
        agent_summary: str = "",
    ) -> None:
        """Review then merge; handles Tier 1 + Tier 2 conflict resolution."""
        diff = await _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)
        # Compute delta diff for retry reviews: shows ONLY what the retry
        # agent changed, so the reviewer can focus on the fix rather than
        # re-reading the entire accumulated diff.
        delta_diff = None
        no_changes_on_retry = False
        if pre_retry_ref and task.retry_count > 0:
            delta_result = await _run_git(
                ["diff", pre_retry_ref, "HEAD"],
                cwd=worktree_path, check=False, description="delta diff",
            )
            if delta_result.returncode == 0 and delta_result.stdout.strip():
                delta_diff = delta_result.stdout
            else:
                # Agent made no changes in the delta between retries.
                # Auto-pass ONLY when there is truly nothing to review:
                # the full diff vs base must also be empty (no code at all).
                # If a diff exists, always run LLM review — the agent may have
                # fixed lint on retry and the underlying code still needs review.
                if not diff.strip():
                    no_changes_on_retry = True
                    logger.info(
                        "Task %s retry %d: no delta changes AND no diff vs base "
                        "— auto-passing review (nothing to review)",
                        task_id, task.retry_count,
                    )
                else:
                    # Delta is empty but full diff exists — always run full review.
                    logger.info(
                        "Task %s retry %d: no delta changes but full diff exists "
                        "— running full LLM review",
                        task_id, task.retry_count,
                    )
        await db.update_task_state(task_id, TaskState.IN_REVIEW.value)
        # Store and emit the diff so the TUI can display it immediately.
        # This is the diff computed from the worktree (task's actual changes)
        # rather than from the pipeline branch (which doesn't have them yet).
        await db.set_task_review_diff(task_id, diff)
        await self._emit("task:review_diff", {
            "task_id": task_id,
            "diff": diff,
        }, db=db, pipeline_id=pid)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "in_review"}, db=db, pipeline_id=pid)
        # Resolve per-pipeline build/test commands for review gates
        pipeline = await db.get_pipeline(pid) if pid else None
        self._pipeline_build_cmd = getattr(pipeline, 'build_cmd', None) if pipeline else None
        self._pipeline_test_cmd = getattr(pipeline, 'test_cmd', None) if pipeline else None
        if no_changes_on_retry:
            console.print(f"[dim]{task_id}: no changes on retry — auto-passing review[/dim]")
            passed, feedback = True, None
        else:
            # Review with automatic re-review on transient failures (empty response,
            # SDK errors) so they don't waste the task's limited retry budget.
            max_re_reviews = 2
            passed, feedback = False, None
            for re_review_attempt in range(max_re_reviews + 1):
                passed, feedback = await self._run_review(
                    task, worktree_path, diff, db=db, pipeline_id=pid,
                    pipeline_branch=pipeline_branch, delta_diff=delta_diff,
                    repo_id=getattr(task, 'repo_id', None),
                )
                if passed:
                    break
                if feedback and "[RETRIABLE]" in feedback and re_review_attempt < max_re_reviews:
                    console.print(f"[yellow]{task_id}: transient review failure, re-reviewing ({re_review_attempt + 1}/{max_re_reviews})...[/yellow]")
                    continue
                break
            if not passed:
                # Build focused retry feedback: reviewer feedback + changed files + diff snippet.
                # Include the diff so the retry agent can see what it wrote without re-reading every file.
                enriched_feedback = feedback or ""
                changed_files = await _get_changed_files_vs_main(
                    worktree_path, base_ref=pipeline_branch,
                )
                if changed_files:
                    files_summary = "\n".join(f"  - {f}" for f in changed_files)
                    diff_snippet = diff[:3000] if diff else "(no diff available)"
                    enriched_feedback = (
                        f"=== REVIEWER FEEDBACK ===\n{enriched_feedback}\n\n"
                        f"=== FILES YOU MODIFIED ===\n{files_summary}\n\n"
                        f"=== YOUR CURRENT DIFF (for reference) ===\n"
                        f"```diff\n{diff_snippet}\n```\n\n"
                        "Your code is still in the worktree. Read the specific files and lines "
                        "mentioned in the reviewer feedback above, then fix ONLY those issues."
                    )
                else:
                    enriched_feedback = (
                        f"=== REVIEWER FEEDBACK ===\n{enriched_feedback}\n\n"
                        "Your code is still in the worktree. Fix the specific issues above."
                    )
                # Store current diff so re-reviewer can compare on next attempt
                await db.set_task_prior_diff(task_id, diff[:10000])
                await self._handle_retry(db, task_id, worktree_mgr, review_feedback=enriched_feedback, pipeline_id=pid)
                return

        # ── Capture agent self-reported learning (success after prior failure) ──
        if task.retry_count > 0 and agent_summary:
            try:
                from forge.core.daemon_helpers import _parse_forge_learning
                from forge.learning.extractor import extract_from_agent_learning
                learning_data = _parse_forge_learning(agent_summary)
                if learning_data:
                    lesson = extract_from_agent_learning(
                        learning_data,
                        task_title=getattr(task, "title", ""),
                        project_dir=getattr(self, "_project_dir", None),
                    )
                    if lesson:
                        existing = await db.find_matching_lesson(
                            lesson.trigger,
                            project_dir=getattr(self, "_project_dir", None),
                        )
                        if existing:
                            await db.bump_lesson_hit(existing.id)
                            logger.info("Bumped existing learning: %s", existing.title)
                        else:
                            await db.add_lesson(
                                scope=lesson.scope, category=lesson.category,
                                title=lesson.title, content=lesson.content,
                                trigger=lesson.trigger, resolution=lesson.resolution,
                                confidence=lesson.confidence,
                                project_dir=getattr(self, "_project_dir", None) if lesson.scope == "project" else None,
                            )
                            logger.info("Agent learning captured: %s", lesson.title)
            except Exception:
                logger.debug("Failed to capture agent learning (non-fatal)", exc_info=True)

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
        branch = f"forge/{validate_task_id(task_id)}"

        # Ensure worktree is clean before merge — commits staged changes,
        # stashes untracked files, so rebase never hits "uncommitted changes"
        await self._ensure_clean_for_rebase(worktree_path, task_id)

        # Snapshot pipeline branch BEFORE merge so diff stats reflect only this task's changes
        pre_merge_ref = await _resolve_ref(worktree_path, merge_worker._main)
        async with self._merge_lock:
            merge_result = await merge_worker.merge(branch, worktree_path=worktree_path)
        if merge_result.success:
            await self._emit_merge_success(db, task_id, pid, worktree_path, pipeline_branch=pre_merge_ref)
            return
        console.print(f"[yellow]{task_id}: trying Tier 1 merge retry (auto-rebase)...[/yellow]")
        await self._emit_merge_failure(db, task_id, merge_result.error, pid)
        await self._ensure_clean_for_rebase(worktree_path, task_id)  # clean before retry
        async with self._merge_lock:
            retry_result = await merge_worker.retry_merge(branch, worktree_path=worktree_path)
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
        async with self._merge_lock:
            prep = await merge_worker.prepare_for_resolution(branch, worktree_path=worktree_path)
        if prep.success:
            await self._try_race_resolved_merge(db, merge_worker, worktree_mgr, task_id, worktree_path, branch, pid, pre_merge_ref=pre_merge_ref)
            return
        resolved = await self._resolve_conflicts(task_id, worktree_path, prep.conflicting_files, agent_model, db=db)
        if resolved:
            async with self._merge_lock:
                final = await merge_worker.merge(branch, worktree_path=worktree_path)
            if final.success:
                await self._emit_merge_success(db, task_id, pid, worktree_path, label="after conflict resolution", pipeline_branch=pre_merge_ref)
                return
            await merge_worker._abort_rebase(worktree_path)
        else:
            await merge_worker._abort_rebase(worktree_path)
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
        # Extract agent_prompt_modifier from template config if available
        template_config = getattr(self, "_template_config", None)
        agent_prompt_modifier = template_config.get("agent_prompt_modifier", "") if template_config else ""

        if task.retry_count > 0 and getattr(task, "review_feedback", None):
            console.print(f"[yellow]{getattr(task, 'id', '?')}: retry {task.retry_count} — including review feedback[/yellow]")
            return _build_retry_prompt(
                task.title, task.description, task.files,
                task.review_feedback, task.retry_count,
                agent_prompt_modifier=agent_prompt_modifier,
            )
        return _build_agent_prompt(task.title, task.description, task.files, agent_prompt_modifier=agent_prompt_modifier)

    async def _stream_agent(self, runtime, agent_id: str, prompt: str, worktree_path: str, task, task_id: str, pid: str, db, agent_model: str, *, resume: str | None = None):
        """Run agent with batched streaming callback."""
        _last_flush = [time.monotonic()]
        _batch: list[str] = []

        # RuntimeGuard — detects wasteful retry loops
        guard = RuntimeGuard()

        async def _on_msg(msg):
            # Check for retry loops BEFORE processing
            try:
                result = guard.inspect(msg)
                if result == "warning":
                    warning = guard.get_warning_message()
                    await self._emit("task:agent_output", {"task_id": task_id, "line": warning}, db=db, pipeline_id=pid)
            except GuardTriggered:
                raise  # Let it propagate to the outer try/except

            text = _extract_activity(msg)
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

        # Load contracts for this task
        contracts_block = ""
        contract_set = getattr(self, "_contracts", None)
        if contract_set is None and pid:
            # Load from DB if not in memory (e.g., web flow, resume)
            contracts_json = await db.get_pipeline_contracts(pid)
            if contracts_json:
                from forge.core.contracts import ContractSet as CS
                try:
                    contract_set = CS.model_validate_json(contracts_json)
                except Exception:
                    logger.warning("Failed to parse contracts_json for pipeline %s", pid)
                    await self._emit("task:review_update", {
                        "task_id": task_id, "gate": "contract_loading",
                        "passed": True,
                        "details": "Contract loading failed — executing without contract compliance checks",
                    }, db=db, pipeline_id=pid)

        if contract_set:
            task_contracts = contract_set.contracts_for_task(task_id)
            contracts_block = task_contracts.format_for_agent()

        task_timeout = _complexity_timeout(
            self._settings.agent_timeout_seconds,
            getattr(task, "complexity", None),
        )

        # Inject lessons into agent prompt
        lessons_block = ""
        try:
            lesson_rows = await db.get_relevant_lessons(
                project_dir=self._project_dir, max_count=20,
            )
            lessons_block = format_lessons_block([row_to_lesson(r) for r in lesson_rows])
        except Exception as exc:
            logger.warning("Failed to load lessons: %s", exc)

        try:
            result = await runtime.run_task(
                agent_id, prompt, worktree_path, task.files,
                allowed_dirs=self._settings.allowed_dirs, model=agent_model, on_message=_on_msg,
                project_context=self._build_project_context(),
                conventions_json=conventions_json,
                conventions_md=conventions_md,
                completed_deps=completed_deps if completed_deps else None,
                contracts_block=contracts_block,
                lessons_block=lessons_block,
                resume=resume,
                autonomy=self._settings.autonomy,
                questions_remaining=self._settings.question_limit,
                timeout_seconds=task_timeout,
                project_dir=self._project_dir,
                agent_max_turns=self._settings.agent_max_turns,
            )
        except GuardTriggered as exc:
            logger.warning("RuntimeGuard triggered for task %s: %s", task_id, exc)
            await self._emit("task:agent_output", {"task_id": task_id, "line": f"Agent stopped: {exc}"}, db=db, pipeline_id=pid)
            # Create lesson from failures — db is always available
            try:
                lesson = extract_from_command_failures(exc.failures, project_dir=self._project_dir)
                existing = await db.find_matching_lesson(lesson.trigger, project_dir=self._project_dir)
                if existing:
                    await db.bump_lesson_hit(existing.id)
                else:
                    await db.add_lesson(
                        scope=lesson.scope, category=lesson.category,
                        title=lesson.title, content=lesson.content,
                        trigger=lesson.trigger, resolution=lesson.resolution,
                        project_dir=self._project_dir if lesson.scope == "project" else None,
                        confidence=0.7,
                    )
                logger.info("Lesson captured: %s", lesson.title)
            except Exception as le:
                logger.warning("Failed to capture lesson: %s", le)
            # Return a failure result
            from forge.agents.adapter import AgentResult
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Agent stopped by RuntimeGuard: {exc}",
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                session_id=None,
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
        async with self._merge_lock:
            ff_result = await merge_worker.merge(branch, worktree_path=worktree_path)
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

        If integration post-merge checks are enabled, runs the health check
        BEFORE marking the task as DONE. If the check fails and the user (or
        policy) chooses to stop, the task is NOT marked done — an early return
        leaves it in MERGING state and emits a pipeline error.

        Args:
            pipeline_branch: The pipeline branch ref (e.g. ``forge/pipeline-abc123``)
                used as the diff base so that stats reflect only *this* task's
                own changes rather than the cumulative total of all previously
                merged tasks.  When ``None``, falls back to the commit-count
                heuristic.
        """
        tag = f" ({label})" if label else ""
        console.print(f"[bold green]{task_id} merged{tag}![/bold green]")

        # ── Post-merge integration health check ─────────────────────
        integration_config = getattr(self, "_integration_config", None)
        if integration_config is not None:
            from forge.core.integration import effective_enabled, run_post_merge_check

            if effective_enabled(integration_config.post_merge):
                baseline_exit = getattr(self, "_baseline_exit_code", None)
                # Use the actual pipeline branch name (merge target)
                mw = getattr(self, "_merge_worker", None)
                actual_pb = mw._main if mw else (pipeline_branch or "HEAD")

                await self._emit("integration:check_started", {
                    "task_id": task_id,
                }, db=db, pipeline_id=pid)

                check_result = await run_post_merge_check(
                    integration_config.post_merge,
                    self._project_dir,
                    actual_pb,
                    baseline_exit,
                    task_id,
                )

                if check_result.status == "infra_error":
                    logger.warning(
                        "Post-merge integration check infra error for %s: %s — skipping",
                        task_id, check_result.stderr[:200],
                    )
                    await self._emit("integration:check_result", {
                        "task_id": task_id,
                        "status": "infra_error",
                    }, db=db, pipeline_id=pid)
                elif check_result.status in ("failed", "timeout"):
                    action = await self._resolve_integration_failure(
                        integration_config.post_merge,
                        check_result, db, pid,
                        task_id=task_id, phase="post_merge",
                    )
                    await self._emit("integration:check_result", {
                        "task_id": task_id,
                        "status": check_result.status,
                        "exit_code": check_result.exit_code,
                        "is_regression": check_result.is_regression,
                        "action": action,
                    }, db=db, pipeline_id=pid)

                    if action == "stop_pipeline":
                        # Do NOT mark task DONE — leave in MERGING state
                        await self._emit("pipeline:error", {
                            "error": f"Integration check failed after merging {task_id}",
                        }, db=db, pipeline_id=pid)
                        await self._emit("pipeline:phase_changed", {
                            "phase": "error",
                        }, db=db, pipeline_id=pid)
                        return  # early return — task NOT marked done
                else:
                    await self._emit("integration:check_result", {
                        "task_id": task_id,
                        "status": "passed",
                    }, db=db, pipeline_id=pid)

        # ── Mark task DONE (existing logic) ─────────────────────────
        await db.update_task_state(task_id, TaskState.DONE.value)

        # Extract and store implementation summary for downstream tasks
        task = await db.get_task(task_id)
        agent_summary = getattr(task, "description", "") if task else ""
        # Use the agent result summary if available (stored during agent run)
        # Fall back to task description
        summary = await _extract_implementation_summary(worktree_path, agent_summary, pipeline_branch)
        await db.update_task_implementation_summary(task_id, summary)

        stats = await _get_diff_stats(worktree_path, pipeline_branch=pipeline_branch)
        await self._emit("task:merge_result", {"task_id": task_id, "success": True, "error": None, **stats}, db=db, pipeline_id=pid)
        await self._emit("task:state_changed", {"task_id": task_id, "state": "done"}, db=db, pipeline_id=pid)

    async def _emit_merge_failure(self, db, task_id: str, error: str | None, pid: str) -> None:
        """Emit merge-failure event (does not change task state)."""
        console.print(f"[red]{task_id} merge failed: {error}[/red]")
        await self._emit("task:merge_result", {"task_id": task_id, "success": False, "error": error}, db=db, pipeline_id=pid)
