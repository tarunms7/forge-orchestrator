"""Forge daemon. Async orchestration loop: plan -> schedule -> dispatch -> review -> merge."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import shutil
import subprocess
import uuid

from rich.console import Console

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.config.settings import ForgeSettings
from forge.core.budget import BudgetExceededError, check_budget
from forge.core.context import ProjectSnapshot, gather_project_snapshot
from forge.core.cost_estimator import estimate_pipeline_cost
from forge.core.engine import _row_to_record
from forge.core.events import EventEmitter
from forge.core.contract_builder import ContractBuilder, ContractBuilderLLM
from forge.core.contracts import ContractSet, IntegrationHint
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
    _extract_activity,
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


def _classify_pipeline_result(task_states: list[str]) -> str:
    """Classify pipeline outcome from terminal task states."""
    active_states = [s for s in task_states if s != "cancelled"]
    if not active_states:
        return "complete"
    done_count = sum(1 for s in active_states if s == "done")
    if done_count == len(active_states):
        return "complete"
    if done_count == 0:
        return "error"
    return "partial_success"


def _sanitize_branch_name(raw: str) -> str:
    """Sanitize a string into a valid git branch name.

    Lowercase, replace spaces/underscores with hyphens, remove special chars,
    truncate to ~50 chars, prefix with ``forge/``.
    """
    name = raw.lower().strip()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9\-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    if len(name) > 50:
        truncated = name[:50]
        last_hyphen = truncated.rfind("-")
        name = truncated[:last_hyphen] if last_hyphen > 10 else truncated
    if not name:
        name = "pipeline-task"
    return f"forge/{name}"


async def _generate_branch_name(description: str) -> str:
    """Generate a short, meaningful branch name from a task description using an LLM.

    Falls back to the dumb slugify approach if the LLM call fails.

    Examples:
      "Fix duplicating progress log lines in mining CLI" → "forge/fix-mining-progress-duplicates"
      "We haven't updated anything in the README, can we update it?" → "forge/update-readme"
    """
    from forge.core.sdk_helpers import sdk_query
    from claude_code_sdk import ClaudeCodeOptions

    try:
        result = await sdk_query(
            prompt=(
                "Generate a short git branch name (3-5 words, kebab-case) for this task. "
                "Reply with ONLY the branch name, nothing else. No 'forge/' prefix.\n\n"
                f"Task: {description}"
            ),
            options=ClaudeCodeOptions(max_turns=1),
        )
        if result and result.result_text:
            candidate = result.result_text.strip().strip("`\"'").strip()
            # Remove any prefix the LLM might add
            for prefix in ("forge/", "feature/", "fix/", "feat/"):
                if candidate.lower().startswith(prefix):
                    candidate = candidate[len(prefix):]
            # Sanitize what the LLM gave us
            sanitized = _sanitize_branch_name(candidate)
            if sanitized != "forge/pipeline-task":
                logger.debug("LLM generated branch name: %s", sanitized)
                return sanitized
    except Exception:
        logger.debug("LLM branch name generation failed, falling back to slugify", exc_info=True)

    return _sanitize_branch_name(description)


def _should_use_deep_planning(
    planning_mode: str,
    spec_path: str | None,
    user_input: str,
    total_files: int,
) -> bool:
    """Decide whether to use multi-pass deep planning."""
    if planning_mode == "deep":
        return True
    if planning_mode == "simple":
        return False
    # Auto mode heuristics
    if spec_path:
        return True
    if total_files > 200:
        return True
    # Check for structured input (markdown headers, numbered lists)
    if re.search(r"^#{1,3}\s", user_input, re.MULTILINE):
        return True
    if re.search(r"^\d+\.\s", user_input, re.MULTILINE):
        return True
    return False


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
        self._merge_lock = asyncio.Lock()

    async def _emit(self, event_type: str, data: dict, *, db: Database, pipeline_id: str) -> None:
        """Emit event to WebSocket AND persist to DB."""
        await self._events.emit(event_type, data)
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id=data.get("task_id"),
            event_type=event_type,
            payload=data,
        )

    def _auto_detect_commands(self, project_dir: str) -> None:
        """Auto-detect build_cmd and test_cmd from project config files.

        Only sets values that are ``None`` — an empty string means the user
        explicitly wants to skip, so it is never overridden.
        """
        # --- build_cmd ---
        if self._settings.build_cmd is None:
            pkg_json = os.path.join(project_dir, "package.json")
            if os.path.exists(pkg_json):
                try:
                    with open(pkg_json, encoding="utf-8") as fh:
                        data = json.load(fh)
                    if data.get("scripts", {}).get("build"):
                        self._settings.build_cmd = "npm run build"
                        logger.info("Auto-detected build_cmd: %s", self._settings.build_cmd)
                except (json.JSONDecodeError, OSError):
                    pass

        # --- test_cmd ---
        if self._settings.test_cmd is None:
            pyproject = os.path.join(project_dir, "pyproject.toml")
            if os.path.exists(pyproject):
                try:
                    with open(pyproject, encoding="utf-8") as fh:
                        content = fh.read()
                    if "[tool.pytest]" in content or "[tool.pytest.ini_options]" in content:
                        self._settings.test_cmd = "python -m pytest"
                        logger.info("Auto-detected test_cmd: %s", self._settings.test_cmd)
                except OSError:
                    pass

        if self._settings.test_cmd is None:
            makefile = os.path.join(project_dir, "Makefile")
            if os.path.exists(makefile):
                try:
                    with open(makefile, encoding="utf-8") as fh:
                        content = fh.read()
                    if re.search(r"^test[:\s]", content, re.MULTILINE):
                        self._settings.test_cmd = "make test"
                        logger.info("Auto-detected test_cmd: %s", self._settings.test_cmd)
                except OSError:
                    pass

    async def _preflight_checks(self, project_dir: str, db: Database, pipeline_id: str) -> bool:
        """Run pre-execution validation. Returns True if all checks pass."""
        self._auto_detect_commands(project_dir)
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

    async def plan(self, user_input: str, db: Database, *, emit_plan_ready: bool = True, pipeline_id: str | None = None, spec_path: str | None = None, deep_plan: bool = False) -> TaskGraph:
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

        # Load template config from pipeline for prompt modifiers
        planner_prompt_modifier = ""
        if pipeline_id:
            pipeline_rec = await db.get_pipeline(pipeline_id)
            template_config_json = getattr(pipeline_rec, "template_config_json", None) if pipeline_rec else None
            if template_config_json:
                try:
                    self._template_config = json.loads(template_config_json)
                    planner_prompt_modifier = self._template_config.get("planner_prompt_modifier", "")
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to parse template_config_json for pipeline %s", pipeline_id)
                    self._template_config = None
            else:
                self._template_config = None
        else:
            self._template_config = None

        planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir, system_prompt_modifier=planner_prompt_modifier)
        planner = Planner(planner_llm, max_retries=self._settings.max_retries)

        async def _on_planner_msg(msg):
            text = _extract_activity(msg)
            if text:
                if pipeline_id:
                    await self._emit("planner:output", {"line": text}, db=db, pipeline_id=pipeline_id)
                else:
                    await self._events.emit("planner:output", {"line": text})

        # Run snapshot gathering in a thread to avoid blocking the event loop
        self._snapshot = await asyncio.get_event_loop().run_in_executor(
            None, gather_project_snapshot, self._project_dir,
        )

        # Decide planning mode
        spec_text = ""
        if spec_path:
            with open(spec_path, "r") as f:
                spec_text = f.read()

        use_deep = deep_plan or _should_use_deep_planning(
            planning_mode=self._settings.planning_mode,
            spec_path=spec_path,
            user_input=user_input,
            total_files=self._snapshot.total_files if self._snapshot else 0,
        )

        if use_deep:
            from forge.core.planning.pipeline import PlanningPipeline
            from forge.core.planning.scout import Scout
            from forge.core.planning.architect import Architect
            from forge.core.planning.detailer import DetailerFactory

            scout_model = select_model(strategy, "planner", "medium")
            architect_model = planner_model  # Same high-quality model
            detailer_model = select_model(strategy, "planner", "low")

            console.print(f"[dim]Deep planning: Scout({scout_model}) → Architect({architect_model}) → Detailers({detailer_model})[/dim]")

            scout = Scout(model=scout_model, cwd=self._project_dir)
            architect = Architect(
                model=architect_model, cwd=self._project_dir,
                autonomy=self._settings.autonomy,
                question_limit=self._settings.question_limit,
            )
            detailer_factory = DetailerFactory(
                model=detailer_model, cwd=self._project_dir,
                max_concurrent=self._settings.max_agents,
            )

            async def _on_pipeline_msg(stage, msg):
                if pipeline_id:
                    await self._emit(f"planning:{stage}", {"line": str(msg)}, db=db, pipeline_id=pipeline_id)
                else:
                    await self._events.emit(f"planning:{stage}", {"line": str(msg)})

            # Planning question support via asyncio.Event synchronization
            pending_planning_answer: dict[str, asyncio.Event] = {}
            planning_answers: dict[str, str] = {}

            async def _on_architect_question(question_data: dict) -> str:
                """Called by Architect when it has a question. Blocks until human answers."""
                q = await db.create_task_question(
                    task_id="__planning__",
                    pipeline_id=pipeline_id or "",
                    question=question_data["question"],
                    suggestions=question_data.get("suggestions"),
                    context={"text": question_data.get("context", "")},
                    stage="planning",
                )
                if pipeline_id:
                    await self._emit("planning:question", {
                        "question_id": q.id,
                        "question": question_data,
                    }, db=db, pipeline_id=pipeline_id)
                else:
                    await self._events.emit("planning:question", {
                        "question_id": q.id,
                        "question": question_data,
                    })

                event = asyncio.Event()
                pending_planning_answer[q.id] = event
                try:
                    timeout = self._settings.question_timeout
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pending_planning_answer.pop(q.id, None)
                    logger.info("Planning question %s timed out after %ds", q.id, timeout)
                    return "Proceed with your best judgment."
                except asyncio.CancelledError:
                    pending_planning_answer.pop(q.id, None)
                    return "Proceed with your best judgment."
                return planning_answers.pop(q.id, "Proceed with your best judgment.")

            async def _on_planning_answer(data: dict):
                """Handle planning:answer event — resolve the waiting asyncio.Event."""
                q_id = data.get("question_id")
                answer = data.get("answer")
                if q_id and answer:
                    planning_answers[q_id] = answer
                    event = pending_planning_answer.pop(q_id, None)
                    if event:
                        event.set()

            # Register listener for planning answers
            self._events.on("planning:answer", _on_planning_answer)

            pipeline = PlanningPipeline(
                scout=scout, architect=architect,
                detailer_factory=detailer_factory,
                on_message=_on_pipeline_msg,
                on_question=_on_architect_question,
            )

            try:
                planning_result = await pipeline.run(
                    user_input=user_input,
                    spec_text=spec_text,
                    snapshot_text=self._snapshot.format_for_planner() if self._snapshot else "",
                    conventions=planner_prompt_modifier,
                )
            finally:
                # Clean up listener to prevent accumulation
                handlers = self._events._handlers.get("planning:answer", [])
                if _on_planning_answer in handlers:
                    handlers.remove(_on_planning_answer)

            if planning_result.task_graph is None:
                raise RuntimeError("Deep planning pipeline failed to produce a TaskGraph")

            graph = planning_result.task_graph

            # Save CodebaseMap to cache for incremental scouting
            if planning_result.codebase_map:
                from forge.core.planning.cache import CodebaseMapCache
                try:
                    forge_dir = os.path.join(self._project_dir, ".forge")
                    cache = CodebaseMapCache(forge_dir)
                    # Get current git state for cache metadata
                    commit_result = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=self._project_dir, capture_output=True, text=True,
                    )
                    branch_result = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=self._project_dir, capture_output=True, text=True,
                    )
                    current_commit = commit_result.stdout.strip() if commit_result.returncode == 0 else "unknown"
                    current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"
                    cache.save(
                        planning_result.codebase_map,
                        git_commit=current_commit,
                        git_branch=current_branch,
                        file_hashes={},
                    )
                    console.print("[dim]CodebaseMap cached for incremental scouting[/dim]")
                except Exception as e:
                    logger.warning("Failed to cache CodebaseMap: %s", e)

            # Track costs
            if pipeline_id and planning_result.total_cost_usd > 0:
                await db.add_pipeline_cost(pipeline_id, planning_result.total_cost_usd)
                await db.set_pipeline_planner_cost(pipeline_id, planning_result.total_cost_usd)
                total_cost = await db.get_pipeline_cost(pipeline_id)
                await self._emit("pipeline:cost_update", {
                    "planner_cost_usd": planning_result.total_cost_usd,
                    "total_cost_usd": total_cost,
                    "cost_breakdown": planning_result.cost_breakdown,
                }, db=db, pipeline_id=pipeline_id)
        else:
            # Existing simple planner path
            graph = await planner.plan(user_input, context=self._snapshot.format_for_planner(), on_message=_on_planner_msg)

            # Persist planner-discovered conventions
            if pipeline_id and graph.conventions is not None:
                await db.update_pipeline_conventions(pipeline_id, json.dumps(graph.conventions))

            # Track planner cost
            if pipeline_id and planner_llm._last_sdk_result:
                sdk_result = planner_llm._last_sdk_result
                if sdk_result.cost_usd > 0:
                    await db.add_pipeline_cost(pipeline_id, sdk_result.cost_usd)
                    await db.set_pipeline_planner_cost(pipeline_id, sdk_result.cost_usd)
                    total_cost = await db.get_pipeline_cost(pipeline_id)
                    await self._emit("pipeline:cost_update", {
                        "planner_cost_usd": sdk_result.cost_usd,
                        "total_cost_usd": total_cost,
                    }, db=db, pipeline_id=pipeline_id)

        # Emit pipeline cost estimate
        if pipeline_id and graph.tasks:
            estimated = await estimate_pipeline_cost(
                len(graph.tasks), self._settings, strategy,
            )
            await self._emit("pipeline:cost_estimate", {
                "estimated_cost_usd": estimated,
                "task_count": len(graph.tasks),
            }, db=db, pipeline_id=pipeline_id)

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

        # Explicitly transition phase to 'planned' AFTER plan_ready (if
        # emitted) so the frontend receives task data before the phase
        # changes.  This must run regardless of emit_plan_ready — the
        # phase still transitions even when plan_ready is suppressed.
        if pipeline_id:
            await self._emit("pipeline:phase_changed", {"phase": "planned"}, db=db, pipeline_id=pipeline_id)
        else:
            await self._events.emit("pipeline:phase_changed", {"phase": "planned"})
        return graph

    async def generate_contracts(
        self,
        graph: TaskGraph,
        db: Database,
        pipeline_id: str,
    ) -> ContractSet:
        """Generate interface contracts from planner integration hints.

        Runs between plan() and execute(). Skips if no integration hints exist.
        """
        raw_hints = graph.integration_hints or []
        if not raw_hints:
            logger.info("No integration hints — skipping contract generation")
            return ContractSet()

        hints = [IntegrationHint.model_validate(h) for h in raw_hints]

        await self._emit(
            "pipeline:phase_changed",
            {"phase": "contracts"},
            db=db,
            pipeline_id=pipeline_id,
        )

        contract_model = select_model(self._strategy, "contract_builder", "high")
        builder_llm = ContractBuilderLLM(model=contract_model, cwd=self._project_dir)
        builder = ContractBuilder(builder_llm)

        async def _on_contract_msg(msg):
            text = _extract_text(msg)
            if text:
                await self._emit(
                    "contracts:output",
                    {"line": text},
                    db=db,
                    pipeline_id=pipeline_id,
                )

        context = self._snapshot.format_for_planner() if self._snapshot else ""
        try:
            contract_set = await builder.build(
                graph, hints, project_context=context, on_message=_on_contract_msg,
            )
        except Exception as exc:
            logger.error("Contract builder failed: %s", exc)
            await self._emit(
                "pipeline:contracts_failed",
                {"error": str(exc)},
                db=db,
                pipeline_id=pipeline_id,
            )
            if self._settings.contracts_required:
                raise RuntimeError(f"Contract generation failed (contracts_required=True): {exc}") from exc
            return ContractSet()

        # Track cost
        if builder_llm._last_sdk_result:
            sdk_result = builder_llm._last_sdk_result
            if sdk_result.cost_usd > 0:
                await db.add_pipeline_cost(pipeline_id, sdk_result.cost_usd)

        # Persist contracts
        if contract_set.has_contracts():
            await db.set_pipeline_contracts(
                pipeline_id, contract_set.model_dump_json(),
            )
            console.print(
                f"[green]Contracts: {len(contract_set.api_contracts)} API, "
                f"{len(contract_set.type_contracts)} types[/green]"
            )
        else:
            console.print("[yellow]Contract generation produced no contracts[/yellow]")

        await self._emit(
            "pipeline:contracts_ready",
            {
                "api_count": len(contract_set.api_contracts),
                "type_count": len(contract_set.type_contracts),
            },
            db=db,
            pipeline_id=pipeline_id,
        )

        return contract_set

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

                # Remap contract task IDs to match the prefixed runtime IDs
                contract_set = getattr(self, "_contracts", None)
                if contract_set and contract_set.has_contracts():
                    self._contracts = contract_set.remap_task_ids(id_map)
                    await db.set_pipeline_contracts(
                        pid, self._contracts.model_dump_json(),
                    )

                # Re-emit plan_ready with prefixed IDs so TUI/subscribers
                # have the correct task keys for state_changed events
                await self._emit("pipeline:plan_ready", {"tasks": [
                    {"id": t.id, "title": t.title, "description": t.description,
                     "files": t.files, "depends_on": t.depends_on,
                     "complexity": t.complexity.value}
                    for t in graph.tasks
                ]}, db=db, pipeline_id=pid)

            for task_def in graph.tasks:
                await db.create_task(
                    id=task_def.id, title=task_def.title, description=task_def.description,
                    files=task_def.files, depends_on=task_def.depends_on,
                    complexity=task_def.complexity.value, pipeline_id=pid,
                )
            # Auto-scale agent pool: create enough agents to saturate
            # parallelism.  The max width of the DAG (tasks with no deps
            # or all deps satisfied at t=0) determines how many agents
            # can usefully run in parallel.  We cap at max_agents to
            # respect the user's resource budget.
            independent_count = sum(
                1 for t in graph.tasks if not t.depends_on
            )
            self._effective_max_agents = min(
                max(independent_count, self._settings.max_agents),
                len(graph.tasks),  # never more agents than tasks
            )
            for i in range(self._effective_max_agents):
                await db.create_agent(f"{prefix}-agent-{i}")

        monitor = ResourceMonitor(
            cpu_threshold=self._settings.cpu_threshold,
            memory_threshold_pct=self._settings.memory_threshold_pct,
            disk_threshold_gb=self._settings.disk_threshold_gb,
        )
        worktree_mgr = WorktreeManager(self._project_dir, f"{self._project_dir}/.forge/worktrees")
        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)

        # Determine pipeline branch name: use user-supplied name, or auto-generate from description
        pipeline_record = await db.get_pipeline(pid)

        # On resume/retry, use the stored base branch from the original run.
        # Re-detecting via _get_current_branch would pick up whatever the user
        # has checked out NOW, which may be different from the original base.
        if resume:
            base_branch = getattr(pipeline_record, "base_branch", None) or _get_current_branch(self._project_dir)
        else:
            base_branch = _get_current_branch(self._project_dir)
        custom_branch = getattr(pipeline_record, "branch_name", None) if pipeline_record else None
        if custom_branch and custom_branch.strip():
            pipeline_branch = custom_branch.strip()
        else:
            description = pipeline_record.description if pipeline_record else ""
            pipeline_branch = (await _generate_branch_name(description)) if description else f"forge/pipeline-{pid[:8]}"
        # Persist the final computed branch name so the PR creation endpoint can use it
        await db.set_pipeline_branch_name(pid, pipeline_branch)
        # Notify TUI so diff views can resolve the branch immediately
        await self._emit("pipeline:branch_resolved", {"branch": pipeline_branch}, db=db, pipeline_id=pid)

        # Isolated pipeline branch — code reaches main only through a PR.
        # On resume/retry the branch already exists and may contain merged
        # task changes — force-resetting it would DESTROY that work.
        if not resume:
            subprocess.run(
                ["git", "branch", "-f", pipeline_branch, base_branch],
                cwd=self._project_dir, check=True, capture_output=True,
            )
            await db.set_pipeline_base_branch(pid, base_branch)
        else:
            # Verify the branch still exists (safety check)
            branch_check = subprocess.run(
                ["git", "rev-parse", "--verify", pipeline_branch],
                cwd=self._project_dir, capture_output=True, text=True,
            )
            if branch_check.returncode != 0:
                # Branch was deleted — recreate it from base
                console.print(f"[yellow]Pipeline branch {pipeline_branch} missing — recreating from {base_branch}[/yellow]")
                subprocess.run(
                    ["git", "branch", "-f", pipeline_branch, base_branch],
                    cwd=self._project_dir, check=True, capture_output=True,
                )
        console.print(f"[dim]Merge target: {pipeline_branch} (base: {base_branch})[/dim]")
        merge_worker = MergeWorker(self._project_dir, main_branch=pipeline_branch)

        await self._execution_loop(db, runtime, worktree_mgr, merge_worker, monitor, pid)

        # Auto-update conventions file after all tasks complete successfully
        if self._settings.auto_update_conventions:
            pipeline_rec = await db.get_pipeline(pid)
            conventions_json_str = getattr(pipeline_rec, "conventions_json", None) if pipeline_rec else None
            if conventions_json_str:
                try:
                    conventions = json.loads(conventions_json_str)
                    from forge.core.conventions import update_conventions_file
                    update_conventions_file(self._project_dir, conventions)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to parse conventions_json for auto-update")

        await self._emit("pipeline:phase_changed", {"phase": "complete"}, db=db, pipeline_id=pid)

    async def retry_task(self, task_id: str, db: Database, pipeline_id: str) -> None:
        """Reset a failed task to 'todo' and re-queue it for execution.

        Clears the worktree if one exists, resets the task state in DB,
        and emits a task:state_changed event so the TUI/subscribers update.
        """
        # Reset state in DB
        await db.update_task_state(task_id, "todo")

        # Clear worktree if it exists
        try:
            worktree_base = os.path.join(self._project_dir, ".forge", "worktrees")
            worktree_mgr = WorktreeManager(self._project_dir, worktree_base)
            worktree_mgr.remove(task_id)
        except Exception:
            logger.debug("No worktree to clean for task %s (or already removed)", task_id)

        # Re-add to scheduler by emitting state change
        await self._emit(
            "task:state_changed",
            {"task_id": task_id, "state": "todo"},
            db=db,
            pipeline_id=pipeline_id,
        )
        logger.info("Task %s reset to 'todo' for retry", task_id)

    async def run(self, user_input: str, spec_path: str | None = None, deep_plan: bool = False) -> None:
        """Full pipeline for CLI: plan + execute. Maintains backward compat."""
        from forge.core.paths import forge_db_url
        db = Database(forge_db_url())
        await db.initialize()
        try:
            self._pipeline_id = str(uuid.uuid4())
            await db.create_pipeline(
                id=self._pipeline_id, description=user_input,
                project_dir=self._project_dir, model_strategy=self._strategy,
                budget_limit_usd=self._settings.budget_limit_usd,
                project_path=self._project_dir,
                project_name=os.path.basename(self._project_dir),
            )
            graph = await self.plan(user_input, db, pipeline_id=self._pipeline_id, spec_path=spec_path, deep_plan=deep_plan)
            # Contract generation phase
            self._contracts = await self.generate_contracts(graph, db, self._pipeline_id)
            try:
                await check_budget(db, self._pipeline_id, self._settings)
            except BudgetExceededError as exc:
                await self._emit("pipeline:budget_exceeded", {
                    "spent": exc.spent,
                    "limit": exc.limit,
                }, db=db, pipeline_id=self._pipeline_id)
                await db.update_pipeline_status(self._pipeline_id, "error")
                raise
            await self.execute(graph, db, pipeline_id=self._pipeline_id)
        finally:
            await db.close()

    async def _check_question_timeouts(self, db: Database, pipeline_id: str) -> None:
        """Auto-answer expired questions so waiting tasks can resume."""
        try:
            expired = await db.get_expired_questions(self._settings.question_timeout)
        except Exception:
            logger.exception("Failed to query expired questions for pipeline %s", pipeline_id)
            return
        for q in expired:
            if q.pipeline_id != pipeline_id:
                continue
            try:
                await db.answer_question(q.id, "Proceed with your best judgment.", "timeout")
                await self._emit(
                    "task:auto_decided",
                    {"task_id": q.task_id, "reason": "timeout", "question_id": q.id},
                    db=db,
                    pipeline_id=pipeline_id,
                )
                logger.info(
                    "Auto-answered timed-out question %s for task %s", q.id, q.task_id
                )
                # Resume the task now that it has an answer
                await self._on_task_answered(
                    data={
                        "task_id": q.task_id,
                        "answer": "Proceed with your best judgment.",
                        "pipeline_id": pipeline_id,
                    },
                    db=db,
                )
            except Exception:
                logger.exception(
                    "Failed to auto-answer question %s for task %s", q.id, q.task_id
                )

    async def _recover_answered_questions(self, db, pipeline_id: str) -> None:
        """Resume tasks that were answered while daemon was down.

        Called at the start of the execution loop. Skips __planning__ sentinel tasks.
        """
        try:
            tasks = await db.get_tasks_by_state(pipeline_id, "awaiting_input")
        except Exception:
            logger.exception("Failed to query awaiting_input tasks for recovery")
            return

        for task in tasks:
            if task.id == "__planning__":
                continue
            try:
                questions = await db.get_task_questions(task.id)
                answered = [q for q in questions if q.answer and q.answered_at]
                if answered:
                    latest = max(answered, key=lambda q: q.answered_at)
                    await self._on_task_answered(
                        data={
                            "task_id": task.id,
                            "answer": latest.answer,
                            "pipeline_id": pipeline_id,
                        },
                        db=db,
                    )
            except Exception:
                logger.exception("Failed to recover task %s", task.id)

    async def _safe_execute_task(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, pipeline_id: str | None = None,
    ) -> None:
        """Wrapper ensuring cleanup on cancellation or crash."""
        try:
            await self._execute_task(
                db, runtime, worktree_mgr, merge_worker,
                task_id, agent_id, pipeline_id=pipeline_id,
            )
        except asyncio.CancelledError:
            logger.info("Task %s was cancelled (shutdown)", task_id)
            raise
        except Exception:
            raise
        finally:
            try:
                await db.release_agent(agent_id)
            except Exception:
                logger.warning("Failed to release agent %s for task %s", agent_id, task_id)

    async def _handle_task_exception(
        self, task_id: str, exc: BaseException,
        db, worktree_mgr, pipeline_id: str | None,
    ) -> None:
        """Handle a task that raised an unhandled exception in the pool."""
        logger.error("Task %s raised unhandled exception: %s", task_id, exc, exc_info=exc)
        try:
            await db.update_task_state(task_id, TaskState.ERROR.value)
            await self._emit("task:state_changed", {
                "task_id": task_id, "state": "error", "error": str(exc),
            }, db=db, pipeline_id=pipeline_id or "")
        except Exception:
            logger.exception("Failed to mark crashed task %s as error", task_id)
        try:
            task_rec = await db.get_task(task_id)
            if task_rec and task_rec.assigned_agent:
                await db.release_agent(task_rec.assigned_agent)
        except Exception:
            pass
        try:
            worktree_mgr.remove(task_id)
        except Exception as cleanup_err:
            logger.warning("Failed to clean up worktree for task %s: %s", task_id, cleanup_err)
        if pipeline_id:
            try:
                remaining = await db.list_tasks_by_pipeline(pipeline_id)
                terminal = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value)
                if all(t.state in terminal for t in remaining):
                    await self._emit("pipeline:error", {
                        "error": f"Pipeline failed: task {task_id} crashed",
                    }, db=db, pipeline_id=pipeline_id)
            except Exception:
                logger.exception("Failed to check pipeline state after task %s crash", task_id)

    async def _shutdown_active_tasks(self) -> None:
        """Cancel all active tasks in the pool and wait for cleanup."""
        active = getattr(self, "_active_tasks", {})
        for atask in active.values():
            atask.cancel()
        if active:
            await asyncio.gather(*active.values(), return_exceptions=True)
        active.clear()

    async def _execution_loop(
        self, db: Database, runtime: AgentRuntime, worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker, monitor: ResourceMonitor, pipeline_id: str | None = None,
    ) -> None:
        """Loop until all tasks are DONE or ERROR."""
        try:
            await self._execution_loop_inner(db, runtime, worktree_mgr, merge_worker, monitor, pipeline_id)
        except Exception as e:
            logger.error("Execution loop crashed: %s", e, exc_info=True)
            try:
                await self._emit("pipeline:error", {"error": f"Pipeline crashed: {e}"}, db=db, pipeline_id=pipeline_id or "")
                await self._emit("pipeline:phase_changed", {"phase": "error"}, db=db, pipeline_id=pipeline_id or "")
            except Exception:
                pass
            raise
        finally:
            await self._shutdown_active_tasks()
            if pipeline_id:
                try:
                    await db.clear_executor_info(pipeline_id)
                except Exception:
                    logger.debug("Failed to clear executor info for pipeline %s", pipeline_id)

    async def _execution_loop_inner(
        self, db: Database, runtime: AgentRuntime, worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker, monitor: ResourceMonitor, pipeline_id: str | None = None,
    ) -> None:
        """Inner execution loop body — wrapped by _execution_loop for error handling."""
        import uuid as _uuid_mod
        prefix = pipeline_id[:8] if pipeline_id else None
        start_time = asyncio.get_event_loop().time()
        timeout = self._settings.pipeline_timeout_seconds
        # Pipeline pause tracking: timestamp (loop time) when all tasks went into awaiting_input
        _all_paused_since: float | None = None
        # Throttle question-timeout checks: only run every 30 seconds
        _last_timeout_check: float = 0.0
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._effective_max_agents = self._settings.max_agents
        self._executor_token = str(_uuid_mod.uuid4())

        # Store dependencies for event-driven resume
        self._runtime = runtime
        self._worktree_mgr = worktree_mgr
        self._merge_worker = merge_worker

        # Register task:answer listener for event-driven resume
        async def _answer_handler(data):
            await self._on_task_answered(data=data, db=db)

        self._events.on("task:answer", _answer_handler)

        # Recover tasks that were answered while daemon was down
        if pipeline_id:
            await self._recover_answered_questions(db, pipeline_id)

        if pipeline_id:
            await db.set_executor_info(pipeline_id, pid=os.getpid(), token=self._executor_token)
        while True:
            # Reap completed tasks from the pool
            done_ids = [tid for tid, atask in self._active_tasks.items() if atask.done()]
            for tid in done_ids:
                atask = self._active_tasks.pop(tid)
                exc = atask.exception() if not atask.cancelled() else None
                if exc:
                    await self._handle_task_exception(tid, exc, db, worktree_mgr, pipeline_id)

            # Watchdog: check elapsed time
            elapsed = asyncio.get_event_loop().time() - start_time
            if timeout > 0 and elapsed > timeout:
                logger.error("Pipeline timeout exceeded (%ds > %ds)", int(elapsed), timeout)
                all_tasks = await (db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks())
                for t in all_tasks:
                    if t.state not in (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value):
                        await db.update_task_state(t.id, TaskState.ERROR.value)
                        await self._emit("task:state_changed", {
                            "task_id": t.id, "state": "error",
                            "error": "Pipeline timeout exceeded",
                        }, db=db, pipeline_id=pipeline_id or "")
                break

            # Check pause flag — don't dispatch new tasks while paused
            if pipeline_id:
                pipeline_rec = await db.get_pipeline(pipeline_id)
                if pipeline_rec and getattr(pipeline_rec, "paused", False):
                    # Don't dispatch new tasks. Already-running tasks continue.
                    await asyncio.sleep(self._settings.scheduler_poll_interval)
                    continue

            tasks = await (db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks())
            _print_status_table(tasks)

            # Periodic question-timeout checker (every 30 s)
            if pipeline_id:
                now = asyncio.get_event_loop().time()
                if now - _last_timeout_check >= 30.0:
                    _last_timeout_check = now
                    await self._check_question_timeouts(db, pipeline_id)

            # AWAITING_APPROVAL, BLOCKED, and CANCELLED count as "parked" — not blocking the loop
            parked_states = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.AWAITING_APPROVAL.value, TaskState.CANCELLED.value, TaskState.BLOCKED.value)
            all_parked = all(t.state in parked_states for t in tasks)
            if all_parked:
                # If any tasks are still awaiting approval, sleep and poll
                # rather than exiting — approvals/rejections create new work
                has_awaiting = any(t.state == TaskState.AWAITING_APPROVAL.value for t in tasks)
                if has_awaiting:
                    await asyncio.sleep(self._settings.scheduler_poll_interval)
                    continue
                # All tasks are truly terminal (done/error/blocked/cancelled)
                done_count = sum(1 for t in tasks if t.state == TaskState.DONE.value)
                error_count = sum(1 for t in tasks if t.state == TaskState.ERROR.value)
                blocked_count = sum(1 for t in tasks if t.state == TaskState.BLOCKED.value)
                cancelled_count = sum(1 for t in tasks if t.state == TaskState.CANCELLED.value)
                total_count = len(tasks)

                result = _classify_pipeline_result([t.state for t in tasks])
                if result == "complete":
                    console.print(f"\n[bold green]Complete: {done_count}/{total_count} done[/bold green]")
                elif result == "partial_success":
                    console.print(
                        f"\n[bold yellow]Partial: {done_count} done, {error_count} errors, "
                        f"{blocked_count} blocked[/bold yellow]"
                    )
                else:
                    console.print(f"\n[bold red]Failed: all {error_count} tasks errored[/bold red]")

                if pipeline_id:
                    # If we were tracking a pause window, close it out now
                    if _all_paused_since is not None:
                        paused_elapsed = asyncio.get_event_loop().time() - _all_paused_since
                        _all_paused_since = None
                        await db.add_pipeline_paused_duration(pipeline_id, paused_elapsed)
                        await db.set_pipeline_paused_at(pipeline_id, None)
                    await db.update_pipeline_status(pipeline_id, result)
                    await self._emit("pipeline:all_tasks_done", {
                        "summary": {
                            "done": done_count,
                            "error": error_count,
                            "blocked": blocked_count,
                            "cancelled": cancelled_count,
                            "total": total_count,
                            "result": result,
                        },
                    }, db=db, pipeline_id=pipeline_id)
                break

            # Pipeline pause tracking: detect when ALL non-terminal active tasks are awaiting_input
            if pipeline_id:
                non_terminal = [
                    t for t in tasks
                    if t.state not in (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value)
                ]
                all_awaiting_input = bool(non_terminal) and all(
                    t.state == TaskState.AWAITING_INPUT.value for t in non_terminal
                )
                if all_awaiting_input:
                    if _all_paused_since is None:
                        # Transition into paused state
                        _all_paused_since = asyncio.get_event_loop().time()
                        paused_at_iso = datetime.now(timezone.utc).isoformat()
                        await db.set_pipeline_paused_at(pipeline_id, paused_at_iso)
                        await self._emit("pipeline:paused", {
                            "reason": "awaiting_input",
                            "task_count": len(non_terminal),
                        }, db=db, pipeline_id=pipeline_id)
                        logger.info(
                            "Pipeline %s paused — all %d tasks are awaiting_input",
                            pipeline_id, len(non_terminal),
                        )
                else:
                    if _all_paused_since is not None:
                        # Tasks have resumed; accumulate pause duration
                        paused_elapsed = asyncio.get_event_loop().time() - _all_paused_since
                        _all_paused_since = None
                        await db.add_pipeline_paused_duration(pipeline_id, paused_elapsed)
                        await db.set_pipeline_paused_at(pipeline_id, None)
                        logger.info(
                            "Pipeline %s resumed after %.1fs pause",
                            pipeline_id, paused_elapsed,
                        )

            snapshot = monitor.take_snapshot()
            if not monitor.can_dispatch(snapshot):
                console.print(f"[yellow]Backpressure: {', '.join(monitor.blocked_reasons(snapshot))}[/yellow]")
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

            if pipeline_id:
                pipeline_rec = await db.get_pipeline(pipeline_id)
                if pipeline_rec and pipeline_rec.executor_token and pipeline_rec.executor_token != self._executor_token:
                    console.print("[yellow]Pipeline taken over by another session. Exiting.[/yellow]")
                    break

            task_records = [_row_to_record(t) for t in tasks]
            agents = await db.list_agents(prefix=prefix)
            from forge.core.engine import _row_to_agent
            agent_records = [_row_to_agent(a) for a in agents]
            dispatch_plan = Scheduler.dispatch_plan(task_records, agent_records, self._effective_max_agents)

            if not dispatch_plan:
                if not self._active_tasks:
                    if any(t.state in (TaskState.AWAITING_APPROVAL.value, TaskState.AWAITING_INPUT.value) for t in tasks):
                        await asyncio.sleep(self._settings.scheduler_poll_interval)
                        continue
                    console.print("[yellow]No tasks to dispatch and none in progress. Stopping.[/yellow]")
                    break
                _done, _pending = await asyncio.wait(
                    self._active_tasks.values(),
                    timeout=self._settings.scheduler_poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                continue

            # Guard: skip tasks already in pool
            dispatch_plan = [
                (tid, aid) for tid, aid in dispatch_plan
                if tid not in self._active_tasks
            ]

            # Cap to actual free slots (pool is authoritative)
            available_slots = max(0, self._effective_max_agents - len(self._active_tasks))
            dispatch_plan = dispatch_plan[:available_slots]

            # Launch into pool
            for task_id, agent_id in dispatch_plan:
                await db.assign_task(task_id, agent_id)
                await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)
                atask = asyncio.create_task(
                    self._safe_execute_task(db, runtime, worktree_mgr, merge_worker,
                                            task_id, agent_id, pipeline_id=pipeline_id),
                    name=f"forge-task-{task_id}",
                )
                self._active_tasks[task_id] = atask

            # Wait efficiently
            if self._active_tasks:
                _done, _pending = await asyncio.wait(
                    self._active_tasks.values(),
                    timeout=self._settings.scheduler_poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await asyncio.sleep(self._settings.scheduler_poll_interval)

        # Normal exit: shutdown active tasks
        await self._shutdown_active_tasks()
