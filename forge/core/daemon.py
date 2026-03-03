"""Forge daemon. Async orchestration loop: plan -> schedule -> dispatch -> review -> merge."""

import asyncio
import logging
import os
import re
import subprocess
import uuid

from rich.console import Console

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.config.settings import ForgeSettings
from forge.core.context import ProjectSnapshot, gather_project_snapshot
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
from forge.storage.db import Database

# Mixin classes providing decomposed daemon functionality
from forge.core.daemon_executor import ExecutorMixin
from forge.core.daemon_review import ReviewMixin
from forge.core.daemon_merge import MergeMixin

# Re-export all helpers at module level for backward compatibility.
from forge.core.daemon_helpers import (  # noqa: F401
    _extract_text,
    _get_current_branch,
    _build_agent_prompt,
    _build_retry_prompt,
    _get_diff_vs_main,
    _get_diff_stats,
    _get_changed_files_vs_main,
    _print_status_table,
)

logger = logging.getLogger("forge")
console = Console()


def _sanitize_branch_name(description: str) -> str:
    """Generate a clean git branch name from a task description.

    Sanitizes: lowercase, replace spaces/underscores with hyphens, remove
    special chars, truncate to ~50 chars, prefix with ``forge/``.

    Example: "Add JWT auth and user registration" → "forge/add-jwt-auth-and-user-registration"
    """
    name = description.lower().strip()
    # Replace whitespace and underscores with hyphens
    name = re.sub(r"[\s_]+", "-", name)
    # Remove anything that isn't alphanumeric or hyphen
    name = re.sub(r"[^a-z0-9\-]", "", name)
    # Collapse multiple consecutive hyphens
    name = re.sub(r"-{2,}", "-", name)
    # Strip leading/trailing hyphens
    name = name.strip("-")
    # Truncate to ~50 chars, preferring to break at a hyphen boundary
    if len(name) > 50:
        truncated = name[:50]
        last_hyphen = truncated.rfind("-")
        name = truncated[:last_hyphen] if last_hyphen > 10 else truncated
    if not name:
        name = "pipeline-task"
    return f"forge/{name}"


class ForgeDaemon(ExecutorMixin, ReviewMixin, MergeMixin):
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
        self._snapshot: ProjectSnapshot | None = None

    async def _emit(self, event_type: str, data: dict, *, db: Database, pipeline_id: str) -> None:
        """Emit event to WebSocket AND persist to DB."""
        await self._events.emit(event_type, data)
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id=data.get("task_id"),
            event_type=event_type,
            payload=data,
        )

    async def _preflight_checks(self, project_dir: str, db: Database, pipeline_id: str) -> bool:
        """Run pre-execution validation. Returns True if all checks pass."""
        import shutil
        errors = []

        # Valid git repo?
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project_dir, capture_output=True, text=True,
        )
        if result.returncode != 0:
            errors.append("Not a git repository")

        # Ensure at least one commit exists (worktrees need valid HEAD)
        has_commits = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_dir, capture_output=True,
        ).returncode == 0
        if not has_commits:
            console.print("[dim]  Creating initial commit (empty repo)...[/dim]")
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "chore: initial commit (forge)"],
                cwd=project_dir, capture_output=True, text=True,
            )

        # Git remote (warning only)
        result = subprocess.run(
            ["git", "remote"], cwd=project_dir, capture_output=True, text=True,
        )
        if not result.stdout.strip():
            console.print("[yellow]  Warning: No git remote configured. PR creation will be skipped.[/yellow]")

        # gh CLI auth (optional)
        if shutil.which("gh"):
            result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
            if result.returncode != 0:
                console.print("[yellow]  Warning: gh CLI not authenticated (PR creation will fail)[/yellow]")

        if errors:
            console.print(f"[bold red]Pre-flight failed: {'; '.join(errors)}[/bold red]")
            await self._emit("pipeline:preflight_failed", {"errors": errors}, db=db, pipeline_id=pipeline_id)
            await db.update_pipeline_status(pipeline_id, "error")
            return False
        return True

    async def plan(self, user_input: str, db: Database, *, emit_plan_ready: bool = True, pipeline_id: str | None = None) -> TaskGraph:
        """Run planning only. Returns the TaskGraph for user approval.

        Args:
            emit_plan_ready: If False, skip emitting the plan_ready event.
                The web flow sets this to False because it remaps task IDs
                before emitting the event with the correct prefixed IDs.
        """
        if pipeline_id:
            await self._emit("pipeline:phase_changed", {"phase": "planning"}, db=db, pipeline_id=pipeline_id)
        else:
            await self._events.emit("pipeline:phase_changed", {"phase": "planning"})

        strategy = self._settings.model_strategy
        planner_model = select_model(strategy, "planner", "high")
        console.print(f"[dim]Strategy: {strategy} | Planner: {planner_model}[/dim]")

        planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir)
        planner = Planner(planner_llm, max_retries=self._settings.max_retries)

        async def _on_planner_msg(msg):
            text = _extract_text(msg)
            if text:
                if pipeline_id:
                    await self._emit("planner:output", {"line": text}, db=db, pipeline_id=pipeline_id)
                else:
                    await self._events.emit("planner:output", {"line": text})

        self._snapshot = gather_project_snapshot(self._project_dir)
        graph = await planner.plan(user_input, context=self._snapshot.format_for_planner(), on_message=_on_planner_msg)
        console.print(f"[green]Plan: {len(graph.tasks)} tasks[/green]")
        for task_def in graph.tasks:
            console.print(f"  - {task_def.id}: {task_def.title} [{task_def.complexity.value}]")

        if emit_plan_ready:
            plan_data = {"tasks": [
                {"id": t.id, "title": t.title, "description": t.description,
                 "files": t.files, "depends_on": t.depends_on, "complexity": t.complexity.value}
                for t in graph.tasks
            ]}
            if pipeline_id:
                await self._emit("pipeline:plan_ready", plan_data, db=db, pipeline_id=pipeline_id)
            else:
                await self._events.emit("pipeline:plan_ready", plan_data)
        return graph

    async def execute(self, graph: TaskGraph, db: Database, pipeline_id: str | None = None, *, resume: bool = False) -> None:
        """Execute a previously approved TaskGraph.

        Args:
            resume: If True, skip task/agent creation (they already exist
                from the original run). Used by the resume endpoint.
        """
        pid = pipeline_id or getattr(self, '_pipeline_id', None) or str(uuid.uuid4())
        await self._emit("pipeline:phase_changed", {"phase": "executing"}, db=db, pipeline_id=pid)
        prefix = pid[:8]

        if not await self._preflight_checks(self._project_dir, db, pid):
            raise RuntimeError("Pre-flight checks failed — see pipeline events for details")

        if not resume:
            # Only remap IDs if they haven't been prefixed yet (CLI flow)
            first_id = graph.tasks[0].id if graph.tasks else ""
            if not first_id.startswith(prefix):
                id_map = {t.id: f"{prefix}-{t.id}" for t in graph.tasks}
                for t in graph.tasks:
                    t.depends_on = [id_map.get(d, d) for d in t.depends_on]
                    t.id = id_map[t.id]

            for task_def in graph.tasks:
                await db.create_task(
                    id=task_def.id, title=task_def.title, description=task_def.description,
                    files=task_def.files, depends_on=task_def.depends_on,
                    complexity=task_def.complexity.value, pipeline_id=pid,
                )
            for i in range(self._settings.max_agents):
                await db.create_agent(f"{prefix}-agent-{i}")

        monitor = ResourceMonitor(
            cpu_threshold=self._settings.cpu_threshold,
            memory_threshold_pct=self._settings.memory_threshold_pct,
            disk_threshold_gb=self._settings.disk_threshold_gb,
        )
        worktree_mgr = WorktreeManager(self._project_dir, f"{self._project_dir}/.forge/worktrees")
        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)
        base_branch = _get_current_branch(self._project_dir)

        # Determine pipeline branch name: use user-supplied name, or auto-generate from description
        pipeline_record = await db.get_pipeline(pid)
        custom_branch = getattr(pipeline_record, "branch_name", None) if pipeline_record else None
        if custom_branch and custom_branch.strip():
            pipeline_branch = custom_branch.strip()
        else:
            description = pipeline_record.description if pipeline_record else ""
            pipeline_branch = _sanitize_branch_name(description) if description else f"forge/pipeline-{pid[:8]}"
        # Persist the final computed branch name so the PR creation endpoint can use it
        await db.set_pipeline_branch_name(pid, pipeline_branch)

        # Isolated pipeline branch — code reaches main only through a PR
        subprocess.run(
            ["git", "branch", "-f", pipeline_branch, base_branch],
            cwd=self._project_dir, check=True, capture_output=True,
        )
        console.print(f"[dim]Merge target: {pipeline_branch} (base: {base_branch})[/dim]")
        merge_worker = MergeWorker(self._project_dir, main_branch=pipeline_branch)
        await db.set_pipeline_base_branch(pid, base_branch)

        await self._execution_loop(db, runtime, worktree_mgr, merge_worker, monitor, pid)
        await self._emit("pipeline:phase_changed", {"phase": "complete"}, db=db, pipeline_id=pid)

    async def run(self, user_input: str) -> None:
        """Full pipeline for CLI: plan + execute. Maintains backward compat."""
        db_path = os.path.join(self._project_dir, ".forge", "forge.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = Database(f"sqlite+aiosqlite:///{db_path}")
        await db.initialize()
        try:
            self._pipeline_id = str(uuid.uuid4())
            await db.create_pipeline(
                id=self._pipeline_id, description=user_input[:200],
                project_dir=self._project_dir, model_strategy=self._strategy,
            )
            graph = await self.plan(user_input, db, pipeline_id=self._pipeline_id)
            await self.execute(graph, db, pipeline_id=self._pipeline_id)
        finally:
            await db.close()

    async def _execution_loop(
        self, db: Database, runtime: AgentRuntime, worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker, monitor: ResourceMonitor, pipeline_id: str | None = None,
    ) -> None:
        """Loop until all tasks are DONE or ERROR."""
        prefix = pipeline_id[:8] if pipeline_id else None
        while True:
            tasks = await (db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks())
            _print_status_table(tasks)

            all_done = all(t.state in (TaskState.DONE.value, TaskState.ERROR.value) for t in tasks)
            if all_done:
                done_count = sum(1 for t in tasks if t.state == TaskState.DONE.value)
                error_count = sum(1 for t in tasks if t.state == TaskState.ERROR.value)
                console.print(f"\n[bold green]Complete: {done_count} done, {error_count} errors[/bold green]")
                break

            snapshot = monitor.take_snapshot()
            if not monitor.can_dispatch(snapshot):
                console.print(f"[yellow]Backpressure: {', '.join(monitor.blocked_reasons(snapshot))}[/yellow]")
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

            task_records = [_row_to_record(t) for t in tasks]
            agents = await db.list_agents(prefix=prefix)
            from forge.core.engine import _row_to_agent
            agent_records = [_row_to_agent(a) for a in agents]
            dispatch_plan = Scheduler.dispatch_plan(task_records, agent_records, self._settings.max_agents)

            if not dispatch_plan:
                if not any(t.state == TaskState.IN_PROGRESS.value for t in tasks):
                    console.print("[yellow]No tasks to dispatch and none in progress. Stopping.[/yellow]")
                    break
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

            for task_id, agent_id in dispatch_plan:
                await db.assign_task(task_id, agent_id)
                await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)

            await asyncio.gather(*[
                self._execute_task(db, runtime, worktree_mgr, merge_worker, task_id, agent_id, pipeline_id=pipeline_id)
                for task_id, agent_id in dispatch_plan
            ])
