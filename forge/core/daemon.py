"""Forge daemon. Async orchestration loop: plan -> schedule -> dispatch -> review -> merge."""

import asyncio
import logging
import os
import subprocess
import uuid

from rich.console import Console
from rich.table import Table

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.config.settings import ForgeSettings
from forge.core.engine import _row_to_record
from forge.core.events import EventEmitter
from forge.core.model_router import select_model
from forge.core.models import TaskGraph, TaskState
from forge.core.monitor import ResourceMonitor
from forge.core.planner import Planner
from forge.core.claude_planner import ClaudePlannerLLM
from forge.core.scheduler import Scheduler
from forge.core.state import TaskStateMachine
from forge.merge.worker import MergeWorker
from forge.merge.worktree import WorktreeManager
from forge.review.llm_review import gate2_llm_review
from forge.review.pipeline import GateResult
from forge.storage.db import Database

logger = logging.getLogger("forge")
console = Console()


def _extract_text(message) -> str | None:
    """Extract human-readable text from a claude-code-sdk message."""
    try:
        from claude_code_sdk import AssistantMessage, ResultMessage
    except ImportError:
        return None
    if isinstance(message, AssistantMessage):
        parts = []
        for block in (message.content or []):
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else None
    if isinstance(message, ResultMessage):
        return message.result if message.result else None
    return None


class ForgeDaemon:
    """Main orchestration loop. Ties all components together."""

    def __init__(
        self,
        project_dir: str,
        settings: ForgeSettings | None = None,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self._project_dir = project_dir
        self._settings = settings or ForgeSettings()
        self._state_machine = TaskStateMachine()
        self._events = event_emitter or EventEmitter()
        self._strategy = self._settings.model_strategy

    async def plan(self, user_input: str, db: Database, *, emit_plan_ready: bool = True) -> TaskGraph:
        """Run planning only. Returns the TaskGraph for user approval.

        Args:
            emit_plan_ready: If False, skip emitting the plan_ready event.
                The web flow sets this to False because it remaps task IDs
                before emitting the event with the correct prefixed IDs.
        """
        await self._events.emit("pipeline:phase_changed", {"phase": "planning"})

        strategy = self._settings.model_strategy
        planner_model = select_model(strategy, "planner", "high")
        console.print(f"[dim]Strategy: {strategy} | Planner: {planner_model}[/dim]")

        planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir)
        planner = Planner(planner_llm, max_retries=self._settings.max_retries)

        graph = await planner.plan(user_input, context=self._gather_context())
        console.print(f"[green]Plan: {len(graph.tasks)} tasks[/green]")

        for task_def in graph.tasks:
            console.print(f"  - {task_def.id}: {task_def.title} [{task_def.complexity.value}]")

        if emit_plan_ready:
            await self._events.emit("pipeline:plan_ready", {
                "tasks": [
                    {
                        "id": t.id, "title": t.title, "description": t.description,
                        "files": t.files, "depends_on": t.depends_on,
                        "complexity": t.complexity.value,
                    }
                    for t in graph.tasks
                ]
            })
        return graph

    async def execute(self, graph: TaskGraph, db: Database, pipeline_id: str | None = None) -> None:
        """Execute a previously approved TaskGraph."""
        await self._events.emit("pipeline:phase_changed", {"phase": "executing"})

        # Use provided pipeline_id, fall back to self._pipeline_id (CLI flow), or generate one
        pid = pipeline_id or getattr(self, '_pipeline_id', None) or str(uuid.uuid4())
        prefix = pid[:8]

        # Task IDs may already be prefixed (web flow remaps in _run_plan).
        # Only remap if they haven't been prefixed yet (CLI flow).
        first_id = graph.tasks[0].id if graph.tasks else ""
        needs_remap = not first_id.startswith(prefix)

        if needs_remap:
            id_map = {t.id: f"{prefix}-{t.id}" for t in graph.tasks}
            for t in graph.tasks:
                t.depends_on = [id_map.get(d, d) for d in t.depends_on]
                t.id = id_map[t.id]

        for task_def in graph.tasks:
            await db.create_task(
                id=task_def.id,
                title=task_def.title,
                description=task_def.description,
                files=task_def.files,
                depends_on=task_def.depends_on,
                complexity=task_def.complexity.value,
                pipeline_id=pid,
            )

        for i in range(self._settings.max_agents):
            await db.create_agent(f"{prefix}-agent-{i}")

        monitor = ResourceMonitor(
            cpu_threshold=self._settings.cpu_threshold,
            memory_threshold_pct=self._settings.memory_threshold_pct,
            disk_threshold_gb=self._settings.disk_threshold_gb,
        )
        worktree_mgr = WorktreeManager(
            self._project_dir,
            f"{self._project_dir}/.forge/worktrees",
        )
        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)
        current_branch = _get_current_branch(self._project_dir)
        console.print(f"[dim]Merge target: {current_branch}[/dim]")
        merge_worker = MergeWorker(self._project_dir, main_branch=current_branch)

        await self._execution_loop(db, runtime, worktree_mgr, merge_worker, monitor, pid)
        await self._events.emit("pipeline:phase_changed", {"phase": "complete"})

    async def run(self, user_input: str) -> None:
        """Full pipeline for CLI: plan + execute. Maintains backward compat."""
        db_path = os.path.join(self._project_dir, ".forge", "forge.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_path}"

        # DB is persistent — each run creates a new pipeline with a unique ID.

        db = Database(db_url)
        await db.initialize()

        try:
            self._pipeline_id = str(uuid.uuid4())
            await db.create_pipeline(
                id=self._pipeline_id,
                description=user_input[:200],
                project_dir=self._project_dir,
                model_strategy=self._strategy,
            )

            graph = await self.plan(user_input, db)
            await self.execute(graph, db)
        finally:
            await db.close()

    async def _execution_loop(
        self,
        db: Database,
        runtime: AgentRuntime,
        worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker,
        monitor: ResourceMonitor,
        pipeline_id: str | None = None,
    ) -> None:
        """Loop until all tasks are DONE or ERROR."""
        prefix = pipeline_id[:8] if pipeline_id else None
        while True:
            # Scope to current pipeline only — avoids stale tasks/agents from prior runs
            if pipeline_id:
                tasks = await db.list_tasks_by_pipeline(pipeline_id)
            else:
                tasks = await db.list_tasks()
            _print_status_table(tasks)

            all_done = all(t.state in (TaskState.DONE.value, TaskState.ERROR.value) for t in tasks)
            if all_done:
                done_count = sum(1 for t in tasks if t.state == TaskState.DONE.value)
                error_count = sum(1 for t in tasks if t.state == TaskState.ERROR.value)
                console.print(f"\n[bold green]Complete: {done_count} done, {error_count} errors[/bold green]")
                break

            snapshot = monitor.take_snapshot()
            if not monitor.can_dispatch(snapshot):
                reasons = monitor.blocked_reasons(snapshot)
                console.print(f"[yellow]Backpressure: {', '.join(reasons)}[/yellow]")
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

            task_records = [_row_to_record(t) for t in tasks]
            agents = await db.list_agents(prefix=prefix)
            from forge.core.engine import _row_to_agent
            agent_records = [_row_to_agent(a) for a in agents]

            dispatch_plan = Scheduler.dispatch_plan(
                task_records, agent_records, self._settings.max_agents,
            )

            if not dispatch_plan:
                in_progress = any(t.state == TaskState.IN_PROGRESS.value for t in tasks)
                if not in_progress:
                    console.print("[yellow]No tasks to dispatch and none in progress. Stopping.[/yellow]")
                    break
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

            for task_id, agent_id in dispatch_plan:
                await db.assign_task(task_id, agent_id)
                await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)

            coros = [
                self._execute_task(db, runtime, worktree_mgr, merge_worker, task_id, agent_id)
                for task_id, agent_id in dispatch_plan
            ]
            await asyncio.gather(*coros)

    async def _execute_task(
        self,
        db: Database,
        runtime: AgentRuntime,
        worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker,
        task_id: str,
        agent_id: str,
    ) -> None:
        """Execute a single task: create worktree -> run agent -> review -> merge."""
        task = await db.get_task(task_id)
        if not task:
            await db.release_agent(agent_id)
            return

        console.print(f"\n[cyan]{'='*50}[/cyan]")
        console.print(f"[cyan]Starting {task_id}: {task.title}[/cyan]")
        console.print(f"[cyan]{'='*50}[/cyan]")

        await self._events.emit("task:state_changed", {"task_id": task_id, "state": "in_progress"})

        try:
            worktree_path = worktree_mgr.create(task_id)
        except ValueError:
            # Worktree already exists from a previous retry — reuse it
            worktree_path = os.path.join(
                self._project_dir, ".forge", "worktrees", task_id,
            )
            if not os.path.isdir(worktree_path):
                console.print(f"[red]Worktree path doesn't exist for {task_id}[/red]")
                await db.update_task_state(task_id, TaskState.ERROR.value)
                await self._events.emit("task:state_changed", {"task_id": task_id, "state": "error"})
                await db.release_agent(agent_id)
                return
        except Exception as e:
            console.print(f"[red]Worktree creation failed for {task_id}: {e}[/red]")
            await db.update_task_state(task_id, TaskState.ERROR.value)
            await self._events.emit("task:state_changed", {"task_id": task_id, "state": "error"})
            await db.release_agent(agent_id)
            return

        agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
        console.print(f"[dim]{task_id}: using {agent_model}[/dim]")

        # Build prompt — include review feedback if this is a retry
        if task.retry_count > 0 and getattr(task, "review_feedback", None):
            prompt = _build_retry_prompt(
                task.title, task.description, task.files,
                task.review_feedback, task.retry_count,
            )
            console.print(f"[yellow]{task_id}: retry {task.retry_count} — including review feedback in prompt[/yellow]")
        else:
            prompt = _build_agent_prompt(task.title, task.description, task.files)

        # Create streaming callback for live logs
        import time

        _last_flush = [time.monotonic()]
        _batch: list[str] = []

        async def _on_agent_message(msg):
            text = _extract_text(msg)
            if not text:
                return
            _batch.append(text)
            now = time.monotonic()
            # Batch: flush every 100ms to prevent WebSocket flooding
            if now - _last_flush[0] >= 0.1:
                for line in _batch:
                    await self._events.emit("task:agent_output", {
                        "task_id": task_id, "line": line,
                    })
                _batch.clear()
                _last_flush[0] = now

        result = await runtime.run_task(
            agent_id, prompt, worktree_path, task.files,
            allowed_dirs=self._settings.allowed_dirs,
            model=agent_model,
            on_message=_on_agent_message,
        )

        # Flush any remaining batched messages
        for line in _batch:
            await self._events.emit("task:agent_output", {
                "task_id": task_id, "line": line,
            })
        _batch.clear()

        if not result.success:
            console.print(f"[red]{task_id} agent failed: {result.error}[/red]")
            await self._handle_retry(db, task_id, worktree_mgr)
            await db.release_agent(agent_id)
            return

        # Check what actually changed vs main branch
        diff = _get_diff_vs_main(worktree_path)
        if not diff.strip():
            console.print(f"[red]{task_id} agent produced no changes[/red]")
            await self._handle_retry(db, task_id, worktree_mgr)
            await db.release_agent(agent_id)
            return

        console.print(f"[green]{task_id} agent completed ({len(diff.splitlines())} diff lines)[/green]")

        if result.files_changed:
            await self._events.emit("task:files_changed", {
                "task_id": task_id, "files": result.files_changed,
            })

        await db.update_task_state(task_id, TaskState.IN_REVIEW.value)
        await self._events.emit("task:state_changed", {"task_id": task_id, "state": "in_review"})

        review_passed, review_feedback = await self._run_review(task, worktree_path, diff)

        if review_passed:
            await db.update_task_state(task_id, TaskState.MERGING.value)
            await self._events.emit("task:state_changed", {"task_id": task_id, "state": "merging"})
            branch = f"forge/{task_id}"
            merge_result = merge_worker.merge(branch, worktree_path=worktree_path)
            if merge_result.success:
                console.print(f"[bold green]{task_id} merged successfully![/bold green]")
                await db.update_task_state(task_id, TaskState.DONE.value)
                stats = _get_diff_stats(self._project_dir)
                await self._events.emit("task:merge_result", {
                    "task_id": task_id, "success": True, "error": None,
                    **stats,
                })
                await self._events.emit("task:state_changed", {"task_id": task_id, "state": "done"})
            else:
                console.print(f"[red]{task_id} merge failed: {merge_result.error}[/red]")
                console.print(f"[yellow]{task_id}: trying Tier 1 merge retry (auto-rebase)...[/yellow]")
                await self._events.emit("task:merge_result", {
                    "task_id": task_id, "success": False, "error": merge_result.error,
                })

                # Tier 1: retry merge only (no agent re-run)
                retry_result = merge_worker.retry_merge(branch, worktree_path=worktree_path)
                if retry_result.success:
                    console.print(f"[bold green]{task_id} merged on retry![/bold green]")
                    await db.update_task_state(task_id, TaskState.DONE.value)
                    stats = _get_diff_stats(self._project_dir)
                    await self._events.emit("task:merge_result", {
                        "task_id": task_id, "success": True, "error": None,
                        **stats,
                    })
                    await self._events.emit("task:state_changed", {"task_id": task_id, "state": "done"})
                else:
                    console.print(f"[red]{task_id} merge retry also failed: {retry_result.error}[/red]")
                    # Tier 2: try agent conflict resolution if we have conflicting files
                    if retry_result.conflicting_files:
                        resolved = await self._resolve_conflicts(
                            task_id, worktree_path,
                            retry_result.conflicting_files, agent_model,
                        )
                        if resolved:
                            final_result = merge_worker.merge(branch, worktree_path=worktree_path)
                            if final_result.success:
                                console.print(f"[bold green]{task_id} merged after conflict resolution![/bold green]")
                                await db.update_task_state(task_id, TaskState.DONE.value)
                                stats = _get_diff_stats(self._project_dir)
                                await self._events.emit("task:merge_result", {
                                    "task_id": task_id, "success": True, "error": None,
                                    **stats,
                                })
                                await self._events.emit("task:state_changed", {"task_id": task_id, "state": "done"})
                            else:
                                await self._handle_retry(db, task_id, worktree_mgr)
                        else:
                            await self._handle_retry(db, task_id, worktree_mgr)
                    else:
                        # Tier 3: full retry (existing behavior)
                        await self._handle_retry(db, task_id, worktree_mgr)
        else:
            await self._handle_retry(db, task_id, worktree_mgr, review_feedback=review_feedback)

        try:
            worktree_mgr.remove(task_id)
        except Exception:
            pass

        # Release the agent so the scheduler can reuse it for the next task
        await db.release_agent(agent_id)

    async def _run_review(self, task, worktree_path: str, diff: str) -> tuple[bool, str | None]:
        """Run the 3-gate review pipeline.

        Returns:
            (passed, feedback) — feedback is a string with failure details
            if any gate failed, None if all passed.
        """
        feedback_parts: list[str] = []

        # L1: lint only the changed files (not full test suite)
        console.print(f"[blue]  L1 (general): Auto-checks for {task.id}...[/blue]")
        gate1_result = await self._gate1(worktree_path)
        await self._events.emit("task:review_update", {
            "task_id": task.id, "gate": "L1", "passed": gate1_result.passed,
            "details": gate1_result.details,
        })
        if not gate1_result.passed:
            console.print(f"[red]  L1 failed: {gate1_result.details}[/red]")
            feedback_parts.append(f"L1 (lint) FAILED:\n{gate1_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L1 passed[/green]")

        # L2: LLM review
        console.print(f"[blue]  L2 (LLM): Code review for {task.id}...[/blue]")
        reviewer_model = select_model(self._strategy, "reviewer", task.complexity or "medium")
        gate2_result = await gate2_llm_review(
            task.title, task.description, diff, worktree_path,
            model=reviewer_model,
        )
        await self._events.emit("task:review_update", {
            "task_id": task.id, "gate": "L2", "passed": gate2_result.passed,
            "details": gate2_result.details,
        })
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
        import sys

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
        subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
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

    async def _resolve_conflicts(
        self, task_id: str, worktree_path: str,
        conflicting_files: list[str], agent_model: str,
    ) -> bool:
        """Tier 2: Use a targeted Claude call to resolve merge conflicts."""
        if not conflicting_files:
            return False

        console.print(f"[yellow]{task_id}: Tier 2 — asking Claude to resolve {len(conflicting_files)} conflicts[/yellow]")

        conflict_prompt = (
            f"The following files have merge conflicts that need to be resolved:\n"
            f"{', '.join(conflicting_files)}\n\n"
            f"Instructions:\n"
            f"1. Open each conflicting file\n"
            f"2. Resolve the merge conflict markers (<<<<<<, =======, >>>>>>)\n"
            f"3. Keep the intent of BOTH changes where possible\n"
            f"4. Stage and commit the resolved files: git add -A && git commit -m 'fix: resolve merge conflicts'\n"
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

    async def _handle_retry(
        self, db: Database, task_id: str, worktree_mgr: WorktreeManager,
        review_feedback: str | None = None,
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
            await db.retry_task(task_id, review_feedback=review_feedback)
            await self._events.emit("task:state_changed", {
                "task_id": task_id, "state": "retrying",
            })
        else:
            console.print(f"[bold red]{task_id}: max retries exceeded, marking as error[/bold red]")
            await db.update_task_state(task_id, TaskState.ERROR.value)
            await self._events.emit("task:state_changed", {"task_id": task_id, "state": "error"})

        try:
            worktree_mgr.remove(task_id)
        except Exception:
            pass

    def _gather_context(self) -> str:
        """Gather project context for the planner."""
        result = subprocess.run(
            ["find", ".", "-name", "*.py", "-not", "-path", "./.forge/*",
             "-not", "-path", "./.venv/*", "-not", "-path", "*__pycache__*"],
            cwd=self._project_dir,
            capture_output=True,
            text=True,
        )
        files = result.stdout.strip()
        return f"Project files:\n{files}" if files else ""


def _get_current_branch(repo_path: str) -> str:
    """Get the current branch name of the repo."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    branch = result.stdout.strip()
    return branch if branch else "main"


def _build_agent_prompt(title: str, description: str, files: list[str]) -> str:
    return (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"Files to create/modify: {', '.join(files)}\n\n"
        "Instructions:\n"
        "1. Implement this task completely\n"
        "2. Write clean, working code\n"
        "3. When done, stage and commit all changes with: git add -A && git commit -m 'feat: <description>'\n"
        "4. Make sure you actually commit — the system checks for committed changes"
    )


def _build_retry_prompt(
    title: str, description: str, files: list[str],
    review_feedback: str, retry_number: int,
) -> str:
    """Build a prompt for a retry that includes the review failure feedback.

    The agent gets the original task spec PLUS the reviewer's notes so it
    can fix the specific issues instead of starting from scratch.
    """
    return (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"Files to create/modify: {', '.join(files)}\n\n"
        f"=== IMPORTANT: This is RETRY #{retry_number} ===\n\n"
        f"Your previous implementation was reviewed and REJECTED. "
        f"The worktree already contains your previous changes. "
        f"DO NOT start from scratch — fix the specific issues below.\n\n"
        f"Review feedback from the reviewer:\n"
        f"---\n"
        f"{review_feedback}\n"
        f"---\n\n"
        "Instructions:\n"
        "1. Read the review feedback above carefully\n"
        "2. Look at your existing code (it's already in the worktree)\n"
        "3. Fix ONLY the issues the reviewer flagged\n"
        "4. Make sure your code actually works — run it if possible\n"
        "5. Stage and commit your fixes: git add -A && git commit -m 'fix: address review feedback'\n"
        "6. Make sure you actually commit — the system checks for committed changes"
    )


def _get_diff_vs_main(worktree_path: str) -> str:
    """Get diff of the worktree branch vs its merge-base with the parent branch.

    Uses ``git merge-base HEAD~N HEAD`` to find where the worktree branch
    diverged, then diffs against that point.  Falls back to diffing the
    last commit only (``HEAD~1..HEAD``) when the merge-base can't be
    determined — this keeps the diff scoped to the agent's actual changes
    rather than the entire feature branch.
    """
    # Try to find how many commits the agent added on top of the base
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    # If that fails, just diff the last commit
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    # Diff only the agent's commits, not the entire feature branch
    base_ref = f"HEAD~{commit_count}"
    result = subprocess.run(
        ["git", "diff", base_ref, "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    # Fallback: if that fails, just diff last commit
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
    return result.stdout


def _get_diff_stats(repo_path: str) -> dict[str, int]:
    """Get lines added/removed for the last merge commit."""
    result = subprocess.run(
        ["git", "diff", "--shortstat", "HEAD~1", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    added, removed = 0, 0
    if result.returncode == 0 and result.stdout.strip():
        import re
        m_add = re.search(r"(\d+) insertion", result.stdout)
        m_del = re.search(r"(\d+) deletion", result.stdout)
        if m_add:
            added = int(m_add.group(1))
        if m_del:
            removed = int(m_del.group(1))
    return {"linesAdded": added, "linesRemoved": removed}


def _get_changed_files_vs_main(worktree_path: str) -> list[str]:
    """Get list of files changed by the agent (not the entire feature branch)."""
    # Reuse the same scoping logic: count agent-only commits
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    result = subprocess.run(
        ["git", "diff", "--name-only", f"HEAD~{commit_count}", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
    return [f for f in result.stdout.strip().split("\n") if f.strip()]


def _print_status_table(tasks) -> None:
    table = Table(title="Forge Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("State")
    table.add_column("Agent")
    table.add_column("Retries")

    state_colors = {
        "todo": "white",
        "in_progress": "yellow",
        "in_review": "blue",
        "merging": "magenta",
        "done": "green",
        "error": "red",
        "cancelled": "dim",
    }

    for t in tasks:
        color = state_colors.get(t.state, "white")
        table.add_row(
            t.id,
            t.title,
            f"[{color}]{t.state}[/{color}]",
            t.assigned_agent or "-",
            str(t.retry_count),
        )

    console.print(table)
