"""Forge daemon. Async orchestration loop: plan -> schedule -> dispatch -> review -> merge."""

import asyncio
import logging
import os
import subprocess

from rich.console import Console
from rich.table import Table

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.config.settings import ForgeSettings
from forge.core.engine import _row_to_record
from forge.core.models import TaskState
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


class ForgeDaemon:
    """Main orchestration loop. Ties all components together."""

    def __init__(self, project_dir: str, settings: ForgeSettings | None = None) -> None:
        self._project_dir = project_dir
        self._settings = settings or ForgeSettings()
        self._state_machine = TaskStateMachine()

    async def run(self, user_input: str) -> None:
        """Execute the full forge pipeline: plan -> execute -> review -> merge."""
        db_path = os.path.join(self._project_dir, ".forge", "forge.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"

        # Fresh DB per run to avoid stale state from previous runs
        if os.path.exists(db_path):
            os.remove(db_path)

        db = Database(db_url)
        await db.initialize()

        try:
            await self._run_pipeline(db, user_input)
        finally:
            await db.close()

    async def _run_pipeline(self, db: Database, user_input: str) -> None:
        planner_llm = ClaudePlannerLLM(cwd=self._project_dir)
        planner = Planner(planner_llm, max_retries=self._settings.max_retries)
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
        merge_worker = MergeWorker(self._project_dir, main_branch="main")

        console.print("[bold blue]Planning...[/bold blue]")
        graph = await planner.plan(user_input, context=self._gather_context())
        console.print(f"[green]Plan created: {len(graph.tasks)} tasks[/green]")

        for task_def in graph.tasks:
            console.print(f"  - {task_def.id}: {task_def.title}")
            await db.create_task(
                id=task_def.id,
                title=task_def.title,
                description=task_def.description,
                files=task_def.files,
                depends_on=task_def.depends_on,
                complexity=task_def.complexity.value,
            )

        for i in range(self._settings.max_agents):
            await db.create_agent(f"agent-{i}")

        await self._execution_loop(db, runtime, worktree_mgr, merge_worker, monitor)

    async def _execution_loop(
        self,
        db: Database,
        runtime: AgentRuntime,
        worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker,
        monitor: ResourceMonitor,
    ) -> None:
        """Loop until all tasks are DONE or ERROR."""
        while True:
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
            agents = await db.list_agents()
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
            return

        console.print(f"\n[cyan]{'='*50}[/cyan]")
        console.print(f"[cyan]Starting {task_id}: {task.title}[/cyan]")
        console.print(f"[cyan]{'='*50}[/cyan]")

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
                return
        except Exception as e:
            console.print(f"[red]Worktree creation failed for {task_id}: {e}[/red]")
            await db.update_task_state(task_id, TaskState.ERROR.value)
            return

        prompt = _build_agent_prompt(task.title, task.description, task.files)
        result = await runtime.run_task(agent_id, prompt, worktree_path, task.files)

        if not result.success:
            console.print(f"[red]{task_id} agent failed: {result.error}[/red]")
            await self._handle_retry(db, task_id, worktree_mgr)
            return

        # Check what actually changed vs main branch
        diff = _get_diff_vs_main(worktree_path)
        if not diff.strip():
            console.print(f"[red]{task_id} agent produced no changes[/red]")
            await self._handle_retry(db, task_id, worktree_mgr)
            return

        console.print(f"[green]{task_id} agent completed ({len(diff.splitlines())} diff lines)[/green]")
        await db.update_task_state(task_id, TaskState.IN_REVIEW.value)

        review_passed = await self._run_review(task, worktree_path, diff)

        if review_passed:
            await db.update_task_state(task_id, TaskState.MERGING.value)
            branch = f"forge/{task_id}"
            merge_result = merge_worker.merge(branch)
            if merge_result.success:
                console.print(f"[bold green]{task_id} merged successfully![/bold green]")
                await db.update_task_state(task_id, TaskState.DONE.value)
            else:
                console.print(f"[red]{task_id} merge failed: {merge_result.error}[/red]")
                await self._handle_retry(db, task_id, worktree_mgr)
        else:
            await self._handle_retry(db, task_id, worktree_mgr)

        try:
            worktree_mgr.remove(task_id)
        except Exception:
            pass

    async def _run_review(self, task, worktree_path: str, diff: str) -> bool:
        """Run the 3-gate review pipeline."""
        # Gate 1: lint only the changed files (not full test suite)
        console.print(f"[blue]  Gate 1: Auto-checks for {task.id}...[/blue]")
        gate1_result = await self._gate1(worktree_path)
        if not gate1_result.passed:
            console.print(f"[red]  Gate 1 failed: {gate1_result.details}[/red]")
            return False
        console.print("[green]  Gate 1 passed[/green]")

        # Gate 2: LLM review
        console.print(f"[blue]  Gate 2: LLM review for {task.id}...[/blue]")
        gate2_result = await gate2_llm_review(
            task.title, task.description, diff, worktree_path,
        )
        if not gate2_result.passed:
            console.print(f"[red]  Gate 2 failed: {gate2_result.details}[/red]")
            return False
        console.print("[green]  Gate 2 passed[/green]")

        # Gate 3: skip for now — merge check is handled by merge_worker
        console.print("[green]  Gate 3 (merge readiness): auto-pass[/green]")
        return True

    async def _gate1(self, worktree_path: str) -> GateResult:
        """Gate 1: Lint check on the worktree. Simple and fast."""
        # Only run ruff on changed files vs main
        changed = _get_changed_files_vs_main(worktree_path)
        py_files = [f for f in changed if f.endswith(".py")]

        if not py_files:
            return GateResult(passed=True, gate="gate1_auto_check", details="No Python files changed")

        lint_result = subprocess.run(
            ["python", "-m", "ruff", "check"] + py_files,
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        lint_clean = lint_result.returncode == 0

        if lint_clean:
            return GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")

        return GateResult(
            passed=False,
            gate="gate1_auto_check",
            details=f"Lint errors:\n{lint_result.stdout[:500]}",
        )

    async def _handle_retry(self, db: Database, task_id: str, worktree_mgr: WorktreeManager) -> None:
        """Handle task failure: retry up to max_retries, then mark as error."""
        task = await db.get_task(task_id)
        if not task:
            return

        if task.retry_count < self._settings.max_retries:
            console.print(
                f"[yellow]{task_id}: retry {task.retry_count + 1}/{self._settings.max_retries}[/yellow]"
            )
            await db.retry_task(task_id)
        else:
            console.print(f"[bold red]{task_id}: max retries exceeded, marking as error[/bold red]")
            await db.update_task_state(task_id, TaskState.ERROR.value)

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


def _get_diff_vs_main(worktree_path: str) -> str:
    """Get diff of the worktree branch vs main."""
    result = subprocess.run(
        ["git", "diff", "main...HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _get_changed_files_vs_main(worktree_path: str) -> list[str]:
    """Get list of files changed vs main."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "main...HEAD"],
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
