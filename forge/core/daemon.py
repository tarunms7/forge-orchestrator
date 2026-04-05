"""Forge daemon. Async orchestration loop: plan -> schedule -> dispatch -> review -> merge."""

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import UTC, datetime

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.config.project_config import load_repo_configs
from forge.config.settings import ForgeSettings
from forge.core.budget import BudgetExceededError, check_budget
from forge.core.claude_planner import ClaudePlannerLLM
from forge.core.context import ProjectSnapshot, gather_project_snapshot
from forge.core.contract_builder import ContractBuilder, ContractBuilderLLM
from forge.core.contracts import ContractSet, IntegrationHint
from forge.core.cost_estimator import estimate_pipeline_cost

# Mixin classes providing decomposed daemon functionality
from forge.core.daemon_executor import ExecutorMixin

# Re-export all helpers at module level for backward compatibility.
from forge.core.daemon_helpers import (  # noqa: F401
    _build_agent_prompt,
    _build_retry_prompt,
    _extract_activity,
    _extract_text,
    _get_changed_files_vs_main,
    _get_current_branch,
    _get_diff_stats,
    _get_diff_vs_main,
    _print_status_table,
    async_subprocess,
    update_repos_json_branches,
)
from forge.core.daemon_merge import MergeMixin
from forge.core.daemon_review import ReviewMixin
from forge.core.errors import ForgeError
from forge.core.events import EventEmitter
from forge.core.logging_config import make_console
from forge.core.model_router import select_model
from forge.core.models import AgentState, RepoConfig, TaskGraph, TaskState, row_to_record
from forge.core.monitor import ResourceMonitor
from forge.core.planner import Planner
from forge.core.sanitize import validate_task_id
from forge.core.scheduler import Scheduler, SchedulingAnalysis
from forge.core.state import TaskStateMachine
from forge.learning.store import format_lessons_block, row_to_lesson
from forge.merge.worker import MergeWorker
from forge.merge.worktree import WorktreeManager
from forge.storage.db import Database

logger = logging.getLogger("forge")
console = make_console()


_EXCLUDE_KEYWORDS = re.compile(
    r"\b(?:ignore|skip|exclude|don'?t\s+touch|don'?t\s+use|don'?t\s+include|nothing\s+to\s+do\s+with)\b",
    re.IGNORECASE,
)


def _detect_excluded_repos(user_input: str, repo_ids: set[str]) -> set[str]:
    """Detect repos the user wants excluded from planning.

    Scans user input for exclude keywords (ignore, skip, exclude, etc.)
    near repo names. If a sentence contains both an exclude keyword and
    a repo name, that repo is excluded.
    """
    excluded = set()
    # Pre-compile word-boundary patterns for each repo id to avoid
    # substring false positives (e.g. "web" matching "webutils").
    repo_patterns = {
        repo_id: re.compile(r"\b" + re.escape(repo_id.lower()) + r"\b") for repo_id in repo_ids
    }
    # Split by sentence boundaries (., !, newlines, commas with spaces)
    sentences = re.split(r"[.!\n]+|,\s+", user_input)
    for sentence in sentences:
        if not _EXCLUDE_KEYWORDS.search(sentence):
            continue
        sentence_lower = sentence.lower()
        for repo_id, pattern in repo_patterns.items():
            if pattern.search(sentence_lower):
                excluded.add(repo_id)
    return excluded


def _classify_pipeline_result(task_states: list[str]) -> str:
    """Classify pipeline outcome from terminal task states."""
    active_states = [s for s in task_states if s != "cancelled"]
    if not active_states:
        return "complete"
    done_count = sum(1 for s in active_states if s == "done")
    blocked_count = sum(1 for s in active_states if s == "blocked")
    if done_count == len(active_states):
        return "complete"
    # All remaining tasks are either done or blocked — partial success if
    # at least one task completed.
    if done_count + blocked_count == len(active_states) and done_count > 0:
        return "partial_success"
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


def _max_dependency_wave_width(tasks: list[object]) -> int:
    """Estimate the widest dependency frontier for a task graph.

    We provision the initial agent pool from this "wave" width rather than
    only counting root tasks. A graph can have a single root task that later
    fans out into multiple ready tasks; sizing by roots would serialize that
    valid parallel work.
    """
    if not tasks:
        return 0

    task_ids = {str(getattr(task, "id")) for task in tasks}
    dependents: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    remaining_deps: dict[str, int] = {}

    for task in tasks:
        task_id = str(getattr(task, "id"))
        deps = [
            str(dep)
            for dep in (getattr(task, "depends_on", None) or [])
            if str(dep) in task_ids
        ]
        remaining_deps[task_id] = len(deps)
        for dep in deps:
            dependents.setdefault(dep, []).append(task_id)

    ready = [task_id for task_id, dep_count in remaining_deps.items() if dep_count == 0]
    if not ready:
        return 1

    max_width = len(ready)
    processed = 0

    while ready:
        current_wave = ready
        processed += len(current_wave)
        next_wave: list[str] = []
        for task_id in current_wave:
            for child_id in dependents.get(task_id, []):
                remaining_deps[child_id] -= 1
                if remaining_deps[child_id] == 0:
                    next_wave.append(child_id)
        ready = next_wave
        max_width = max(max_width, len(ready))

    if processed != len(task_ids):
        return max(max_width, 1)
    return max_width


async def _ensure_agent_pool_size(db: Database, prefix: str, target_size: int) -> None:
    """Create any missing agent rows up to ``target_size`` for this pipeline."""
    if target_size <= 0:
        return

    existing_agents = await db.list_agents(prefix=prefix)
    existing_ids = {agent.id for agent in existing_agents}
    for i in range(target_size):
        agent_id = f"{prefix}-agent-{i}"
        if agent_id not in existing_ids:
            await db.create_agent(agent_id)


async def _generate_branch_name(description: str) -> str:
    """Generate a short, meaningful branch name from a task description using an LLM.

    Falls back to the dumb slugify approach if the LLM call fails.

    Examples:
      "Fix duplicating progress log lines in mining CLI" → "forge/fix-mining-progress-duplicates"
      "We haven't updated anything in the README, can we update it?" → "forge/update-readme"
    """
    from claude_code_sdk import ClaudeCodeOptions

    from forge.core.sdk_helpers import sdk_query

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
                    candidate = candidate[len(prefix) :]
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
    """Decide whether to use the unified planner (full codebase access).

    Returns True when the task likely benefits from the unified planner
    with codebase exploration. The heuristics here intentionally lean
    towards deep planning for any non-trivial request.
    """
    if planning_mode == "deep":
        return True
    if planning_mode == "simple":
        return False
    try:
        total_files_value = int(total_files)
    except (TypeError, ValueError):
        total_files_value = 0
    # Auto mode heuristics
    if spec_path:
        return True
    if total_files_value > 100:
        return True
    # Structured input (markdown headers, numbered lists, bullet lists)
    if re.search(r"^#{1,3}\s", user_input, re.MULTILINE):
        return True
    if re.search(r"^\d+\.\s", user_input, re.MULTILINE):
        return True
    if re.search(r"^[-*]\s", user_input, re.MULTILINE):
        return True
    # Long/complex input (>100 words suggests a non-trivial request)
    if len(user_input.split()) > 100:
        return True
    return False


class ForgeDaemon(ExecutorMixin, ReviewMixin, MergeMixin):
    """Main orchestration loop. Ties all components together."""

    def __init__(
        self,
        project_dir: str,
        settings: ForgeSettings | None = None,
        event_emitter: EventEmitter | None = None,
        repos: list[RepoConfig] | None = None,
    ) -> None:
        from forge.config.project_config import ProjectConfig

        self._project_dir = project_dir
        self._workspace_dir = project_dir  # alias for multi-repo clarity
        self._settings = settings or ForgeSettings()
        self._state_machine = TaskStateMachine()
        self._events = event_emitter or EventEmitter()
        self._strategy = self._settings.model_strategy
        self._snapshot: ProjectSnapshot | None = None
        self._merge_locks: dict[str, asyncio.Lock] = {}  # per-repo merge locks
        self._merge_lock = asyncio.Lock()  # default lock for single-repo backwards compat
        # Limit concurrent subprocess-heavy gates (lint, build, test) to prevent
        # CPU saturation when multiple agents run ESLint/tsc/pytest simultaneously.
        self._gate_semaphore = asyncio.Semaphore(2)
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._active_tasks_lock = asyncio.Lock()
        self._effective_max_agents: int = self._settings.max_agents
        self._project_config = ProjectConfig.load(project_dir)
        # Task list cache — avoids re-fetching from DB every poll iteration
        self._cached_tasks: list | None = None
        self._task_cache_time: float = 0.0
        self._last_scheduling_fingerprint: str | None = None

        # Multi-repo support: build repos dict
        if repos:
            self._repos: dict[str, RepoConfig] = {r.id: r for r in repos}
        else:
            self._repos = {
                "default": RepoConfig(id="default", path=project_dir, base_branch=""),
            }

        # Lessons use the central DB — no separate initialization needed

    def _get_merge_lock(self, repo_id: str = "default") -> asyncio.Lock:
        """Return a per-repo merge lock. Independent repos don't block each other."""
        if repo_id not in self._merge_locks:
            self._merge_locks[repo_id] = asyncio.Lock()
        return self._merge_locks[repo_id]

    async def _emit(self, event_type: str, data: dict, *, db: Database, pipeline_id: str) -> None:
        """Emit event to WebSocket AND persist to DB.

        This method is **fail-safe**: exceptions from event handlers or DB
        logging are caught and logged, never propagated.  Event emission
        must never kill a running task — it's telemetry, not business logic.
        """
        try:
            await self._events.emit(event_type, data)
        except Exception:
            logger.warning("Event handler failed for %s (non-fatal)", event_type, exc_info=True)
        try:
            await db.log_event(
                pipeline_id=pipeline_id,
                task_id=data.get("task_id"),
                event_type=event_type,
                payload=data,
            )
        except Exception:
            logger.warning(
                "Failed to persist event %s to DB (non-fatal)", event_type, exc_info=True
            )

    async def _emit_scheduling_update(
        self,
        *,
        db: Database,
        pipeline_id: str,
        analysis: SchedulingAnalysis,
        agents,
        dispatch_plan: list[tuple[str, str]],
    ) -> None:
        """Persist queue insight only when the scheduler view meaningfully changes."""
        working_count = sum(1 for agent in agents if agent.state == AgentState.WORKING.value)
        idle_count = sum(1 for agent in agents if agent.state == AgentState.IDLE.value)
        payload = analysis.to_payload(dispatching_now=[task_id for task_id, _ in dispatch_plan])
        payload.update(
            {
                "idle_agents": idle_count,
                "busy_agents": working_count,
                "max_agents": self._effective_max_agents,
                "available_slots": max(0, self._effective_max_agents - working_count),
            }
        )
        fingerprint = json.dumps(payload, sort_keys=True)
        if fingerprint == self._last_scheduling_fingerprint:
            return
        self._last_scheduling_fingerprint = fingerprint
        await self._emit(
            "pipeline:scheduling_update",
            payload,
            db=db,
            pipeline_id=pipeline_id,
        )

    # ── Multi-repo initialization & infrastructure ─────────────────────

    async def _init_repos(self) -> None:
        """Validate repos and resolve empty base_branch values.

        Called at the start of execute() before worktree/merge worker creation.
        Checks each repo for dirty working tree and resolves any empty
        base_branch using _get_current_branch().
        """
        from dataclasses import replace as _dc_replace

        for repo_id, rc in list(self._repos.items()):
            # Check for staged changes only (modified tracked files + untracked
            # like .forge/, .claude/, uv.lock are expected and not an error)
            staged = await async_subprocess(
                ["git", "diff", "--cached", "--quiet"],
                cwd=rc.path,
            )
            if staged.returncode != 0:
                raise ForgeError(
                    f"Repository '{repo_id}' at {rc.path} has staged changes. "
                    "Commit or stash changes before running Forge."
                )

            # Resolve empty base_branch
            if not rc.base_branch:
                branch = await _get_current_branch(rc.path)
                self._repos[repo_id] = _dc_replace(rc, base_branch=branch)

    def _setup_per_repo_infra(self, pipeline_branch: str) -> None:
        """Create per-repo WorktreeManager, MergeWorker, and pipeline branch names.

        Single default repo uses flat layout (<project_dir>/.forge/worktrees/).
        Multi-repo creates worktrees inside each repo's own directory
        (<repo_path>/.forge/worktrees/) so git operations use the correct remote.

        Args:
            pipeline_branch: The resolved pipeline branch name (e.g. 'forge/pipeline-abc12345').
        """
        self._worktree_managers: dict[str, WorktreeManager] = {}
        self._merge_workers: dict[str, MergeWorker] = {}
        self._pipeline_branches: dict[str, str] = {}

        multi = len(self._repos) > 1

        for repo_id, rc in self._repos.items():
            if multi:
                # Each repo gets worktrees in its own .forge/ directory
                # so git push/pull use the correct remote
                wt_dir = os.path.join(rc.path, ".forge", "worktrees")
            else:
                wt_dir = os.path.join(self._workspace_dir, ".forge", "worktrees")

            self._worktree_managers[repo_id] = WorktreeManager(rc.path, wt_dir)
            self._merge_workers[repo_id] = MergeWorker(rc.path, main_branch=pipeline_branch)
            self._pipeline_branches[repo_id] = pipeline_branch

    async def _create_pipeline_branches(self) -> None:
        """Create git pipeline branches for each repo using asyncio subprocess."""
        for repo_id, rc in self._repos.items():
            branch_name = self._pipeline_branches[repo_id]
            result = await asyncio.create_subprocess_exec(
                "git",
                "branch",
                "-f",
                branch_name,
                rc.base_branch,
                cwd=rc.path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()
            if result.returncode != 0:
                raise ForgeError(
                    f"Failed to create pipeline branch '{branch_name}' in repo "
                    f"'{repo_id}' at {rc.path}: {stderr.decode().strip()}"
                )

    def _worktree_path(self, repo_id: str, task_id: str) -> str:
        """Compute the filesystem path for a task's git worktree.

        Single default repo: <project_dir>/.forge/worktrees/<task_id> (flat, backward compat).
        Multi-repo: <repo_path>/.forge/worktrees/<task_id> (inside each repo).
        """
        if len(self._repos) > 1:
            rc = self._repos.get(repo_id)
            if rc is None:
                logger.error(
                    "repo_id '%s' not found in configured repos: %s",
                    repo_id,
                    sorted(self._repos.keys()),
                )
                raise ValueError(
                    f"repo_id '{repo_id}' not found in configured repos: "
                    f"{sorted(self._repos.keys())}"
                )
            return os.path.join(rc.path, ".forge", "worktrees", validate_task_id(task_id))
        return os.path.join(self._workspace_dir, ".forge", "worktrees", validate_task_id(task_id))

    def _get_repo_infra(self, repo_id: str) -> tuple[WorktreeManager, MergeWorker, str]:
        """Return the per-repo infrastructure tuple for *repo_id*.

        Returns:
            (WorktreeManager, MergeWorker, pipeline_branch_name)

        Raises:
            ForgeError: if repo_id is not found.
        """
        if repo_id not in self._worktree_managers:
            raise ForgeError(
                f"Unknown repo '{repo_id}' — available repos: "
                f"{sorted(self._worktree_managers.keys())}"
            )
        return (
            self._worktree_managers[repo_id],
            self._merge_workers[repo_id],
            self._pipeline_branches[repo_id],
        )

    def _build_allowed_dirs(self) -> list[str]:
        """Return union of settings.allowed_dirs + all repo paths."""
        dirs = list(self._settings.allowed_dirs or [])
        for rc in self._repos.values():
            if rc.path not in dirs:
                dirs.append(rc.path)
        return dirs

    def _build_repos_json(self) -> str | None:
        """Serialize repos + pipeline branches to JSON.

        Returns None for single-repo (no need to store).
        Format: list of dicts with "id", "path", "base_branch", "branch_name".
        Must match what update_repos_json_branches() and app.py PR creator expect.
        """
        if len(self._repos) <= 1:
            return None
        data: list[dict] = []
        for repo_id, rc in self._repos.items():
            data.append(
                {
                    "id": repo_id,
                    "path": rc.path,
                    "base_branch": rc.base_branch,
                    "branch_name": self._pipeline_branches.get(repo_id, ""),
                }
            )
        return json.dumps(data)

    async def _cleanup_pipeline_branches(self) -> None:
        """Delete pipeline branches from all repos on pipeline error.

        Best-effort: logs failures instead of raising so the original
        exception is never masked.
        """
        branches = getattr(self, "_pipeline_branches", {})
        if not branches:
            return
        for repo_id, branch_name in branches.items():
            rc = self._repos.get(repo_id)
            if not rc:
                continue
            try:
                result = await async_subprocess(
                    ["git", "branch", "-D", branch_name],
                    cwd=rc.path,
                )
                if result.returncode == 0:
                    logger.info(
                        "Cleaned up pipeline branch '%s' in repo '%s'",
                        branch_name,
                        repo_id,
                    )
                else:
                    logger.debug(
                        "Pipeline branch '%s' already removed or doesn't exist in '%s'",
                        branch_name,
                        repo_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to clean up pipeline branch '%s' in repo '%s': %s",
                    branch_name,
                    repo_id,
                    exc,
                )

    def _auto_detect_commands(self, project_dir: str) -> None:
        """Auto-detect build_cmd and test_cmd from project config files.

        Only sets values that are ``None`` — an empty string means the user
        explicitly wants to skip, so it is never overridden.
        """
        # For multi-repo, check each repo. For single-repo, check project_dir.
        dirs_to_scan = (
            [rc.path for rc in self._repos.values()] if len(self._repos) > 1 else [project_dir]
        )

        # --- build_cmd ---
        if self._settings.build_cmd is None:
            for scan_dir in dirs_to_scan:
                pkg_json = os.path.join(scan_dir, "package.json")
                if os.path.exists(pkg_json):
                    try:
                        with open(pkg_json, encoding="utf-8") as fh:
                            data = json.load(fh)
                        if data.get("scripts", {}).get("build"):
                            self._settings.build_cmd = "npm run build"
                            logger.info("Auto-detected build_cmd: %s", self._settings.build_cmd)
                            break
                    except (json.JSONDecodeError, OSError):
                        logger.debug("Failed to read package.json for build_cmd auto-detection")

        # --- test_cmd ---
        if self._settings.test_cmd is None:
            for scan_dir in dirs_to_scan:
                pyproject = os.path.join(scan_dir, "pyproject.toml")
                if os.path.exists(pyproject):
                    try:
                        with open(pyproject, encoding="utf-8") as fh:
                            content = fh.read()
                        if "[tool.pytest]" in content or "[tool.pytest.ini_options]" in content:
                            self._settings.test_cmd = "python -m pytest"
                            logger.info("Auto-detected test_cmd: %s", self._settings.test_cmd)
                            break
                    except OSError:
                        logger.debug("Failed to read pyproject.toml for test_cmd auto-detection")

        if self._settings.test_cmd is None:
            for scan_dir in dirs_to_scan:
                makefile = os.path.join(scan_dir, "Makefile")
                if os.path.exists(makefile):
                    try:
                        with open(makefile, encoding="utf-8") as fh:
                            content = fh.read()
                        if re.search(r"^test[:\s]", content, re.MULTILINE):
                            self._settings.test_cmd = "make test"
                            logger.info("Auto-detected test_cmd: %s", self._settings.test_cmd)
                            break
                    except OSError:
                        logger.debug("Failed to read Makefile for test_cmd auto-detection")

    async def _preflight_checks(self, project_dir: str, db: Database, pipeline_id: str) -> bool:
        """Run pre-execution validation. Returns True if all checks pass."""
        self._auto_detect_commands(project_dir)
        errors = []

        # Determine which directories to check — repo paths for multi-repo,
        # project_dir for single-repo.
        multi = len(self._repos) > 1
        check_dirs = (
            [(rid, rc.path) for rid, rc in self._repos.items()]
            if multi
            else [("default", project_dir)]
        )

        for repo_id, repo_path in check_dirs:
            label = f" [{repo_id}]" if multi else ""

            # Valid git repo?
            result = await async_subprocess(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=repo_path,
            )
            if result.returncode != 0:
                errors.append(f"Not a git repository{label}: {repo_path}")
                continue  # Skip remaining checks for this repo

            # Ensure at least one commit exists (worktrees need valid HEAD)
            has_commits_result = await async_subprocess(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
            )
            if has_commits_result.returncode != 0:
                console.print(f"[dim]  Creating initial commit{label} (empty repo)...[/dim]")
                await async_subprocess(
                    ["git", "commit", "--allow-empty", "-m", "chore: initial commit (forge)"],
                    cwd=repo_path,
                )

            # Git remote (warning only)
            result = await async_subprocess(
                ["git", "remote"],
                cwd=repo_path,
            )
            if not result.stdout.strip():
                console.print(
                    f"[yellow]  Warning: No git remote configured{label}. PR creation will be skipped.[/yellow]"
                )

        # gh CLI auth (optional, check once)
        if shutil.which("gh"):
            result = await async_subprocess(["gh", "auth", "status"], cwd=check_dirs[0][1])
            if result.returncode != 0:
                console.print(
                    "[yellow]  Warning: gh CLI not authenticated (PR creation will fail)[/yellow]"
                )

        if errors:
            console.print(f"[bold red]Pre-flight failed: {'; '.join(errors)}[/bold red]")
            await self._emit(
                "pipeline:preflight_failed", {"errors": errors}, db=db, pipeline_id=pipeline_id
            )
            await db.update_pipeline_status(pipeline_id, "error")
            return False
        return True

    async def plan(
        self,
        user_input: str,
        db: Database,
        *,
        emit_plan_ready: bool = True,
        pipeline_id: str | None = None,
        spec_path: str | None = None,
        deep_plan: bool = False,
    ) -> TaskGraph:
        """Run planning only. Returns the TaskGraph for user approval.

        Args:
            emit_plan_ready: If False, skip emitting the plan_ready event.
                The web flow sets this to False because it remaps task IDs
                before emitting the event with the correct prefixed IDs.
        """
        if pipeline_id:
            await self._emit(
                "pipeline:phase_changed", {"phase": "planning"}, db=db, pipeline_id=pipeline_id
            )
        else:
            await self._events.emit("pipeline:phase_changed", {"phase": "planning"})

        strategy = self._settings.model_strategy
        planner_model = select_model(strategy, "planner", "high")
        console.print(f"[dim]Strategy: {strategy} | Planner: {planner_model}[/dim]")

        async def _planner_progress(msg: str) -> None:
            """Emit a planner progress message to fill visual gaps."""
            if pipeline_id:
                await self._emit("planner:output", {"line": msg}, db=db, pipeline_id=pipeline_id)
            else:
                await self._events.emit("planner:output", {"line": msg})

        await _planner_progress("Analyzing codebase structure…")

        # Load template config from pipeline for prompt modifiers
        planner_prompt_modifier = ""
        if pipeline_id:
            pipeline_rec = await db.get_pipeline(pipeline_id)
            template_config_json = (
                getattr(pipeline_rec, "template_config_json", None) if pipeline_rec else None
            )
            if template_config_json:
                try:
                    self._template_config = json.loads(template_config_json)
                    planner_prompt_modifier = self._template_config.get(
                        "planner_prompt_modifier", ""
                    )
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "Failed to parse template_config_json for pipeline %s", pipeline_id
                    )
                    self._template_config = None
            else:
                self._template_config = None
        else:
            self._template_config = None

        planner_llm = ClaudePlannerLLM(
            model=planner_model,
            cwd=self._project_dir,
            system_prompt_modifier=planner_prompt_modifier,
        )
        planner = Planner(planner_llm, max_retries=self._settings.max_retries)

        _planner_token_count = [0]
        _last_token_update = [0.0]

        async def _on_planner_msg(msg):
            text = _extract_activity(msg)

            # Count tokens from ALL messages (including filtered JSON output)
            # so we can show generation progress even when text is hidden.
            _msg_tokens = 0
            try:
                from claude_code_sdk import AssistantMessage

                if isinstance(msg, AssistantMessage):
                    for block in msg.content or []:
                        if hasattr(block, "text") and block.text:
                            # Rough token estimate: chars / 4
                            _msg_tokens += max(1, len(block.text) // 4)
            except ImportError:
                pass
            if _msg_tokens > 0:
                _planner_token_count[0] += _msg_tokens
                now = time.monotonic()
                # Emit token count update every 2 seconds (not every message)
                if now - _last_token_update[0] >= 2.0:
                    _last_token_update[0] = now
                    token_line = f"⚙ Planner generating… ({_planner_token_count[0]:,} tokens)"
                    if pipeline_id:
                        await self._emit(
                            "planner:output", {"line": token_line}, db=db, pipeline_id=pipeline_id
                        )
                    else:
                        await self._events.emit("planner:output", {"line": token_line})

            if text:
                # Reset token update timer so activity lines don't get buried
                _last_token_update[0] = time.monotonic()
                if pipeline_id:
                    await self._emit(
                        "planner:output", {"line": text}, db=db, pipeline_id=pipeline_id
                    )
                else:
                    await self._events.emit("planner:output", {"line": text})

        # Ensure each repo is on its base_branch before reading files.
        # Without this, the planner reads whatever branch is checked out locally
        # instead of the branch specified in workspace.toml.
        for repo_id, rc in self._repos.items():
            if not rc.base_branch:
                continue  # Skip repos with no base_branch set
            current = await _get_current_branch(rc.path)
            if current != rc.base_branch:
                result = await async_subprocess(
                    ["git", "checkout", rc.base_branch],
                    cwd=rc.path,
                )
                if result.returncode != 0:
                    logger.warning(
                        "Could not checkout %s in repo %s: %s",
                        rc.base_branch,
                        repo_id,
                        result.stderr.strip(),
                    )

        # Filter out repos the user asked to exclude — before gathering
        # snapshots so the planner never sees them at all.
        planning_repos = self._repos
        excluded_repos: set[str] = set()
        if len(self._repos) > 1:
            excluded_repos = _detect_excluded_repos(user_input, set(self._repos.keys()))
            if excluded_repos:
                planning_repos = {k: v for k, v in self._repos.items() if k not in excluded_repos}
                logger.info("Excluding repos from planning: %s", ", ".join(sorted(excluded_repos)))

        # Run snapshot gathering in a thread to avoid blocking the event loop
        await _planner_progress("Gathering project snapshot…")
        if len(planning_repos) == 1:
            repo = next(iter(planning_repos.values()))
            self._snapshot = await asyncio.get_running_loop().run_in_executor(
                None,
                gather_project_snapshot,
                repo.path,
            )
            snapshot_text = self._snapshot.format_for_planner() if self._snapshot else ""
            repo_ids = None
        else:
            from forge.core.context import format_multi_repo_snapshot, gather_multi_repo_snapshots

            snapshots = await gather_multi_repo_snapshots(planning_repos)
            snapshot_text = format_multi_repo_snapshot(snapshots, planning_repos)
            repo_ids = set(planning_repos.keys())
            self._snapshot = next(iter(snapshots.values())) if snapshots else None

        # Decide planning mode
        spec_text = ""
        if spec_path:
            with open(spec_path, encoding="utf-8") as f:
                spec_text = f.read()

        use_deep = deep_plan or _should_use_deep_planning(
            planning_mode=self._settings.planning_mode,
            spec_path=spec_path,
            user_input=user_input,
            total_files=self._snapshot.total_files if self._snapshot else 0,
        )

        await _planner_progress("Building task graph…")

        if use_deep:
            from forge.core.planning.unified_planner import UnifiedPlanner

            console.print(f"[dim]Unified planning: {planner_model} (full codebase access)[/dim]")

            planner_cwd = self._workspace_dir if len(self._repos) > 1 else self._project_dir
            unified_planner = UnifiedPlanner(
                model=planner_model,
                cwd=planner_cwd,
                autonomy=self._settings.autonomy,
                question_limit=self._settings.question_limit,
                repo_ids=repo_ids,
            )

            async def _on_unified_msg(msg):
                """Forward SDK streaming messages as planner:output events."""
                # Count tokens for progress display (reuses outer counters)
                _msg_tokens = 0
                if not isinstance(msg, str):
                    try:
                        from claude_code_sdk import AssistantMessage

                        if isinstance(msg, AssistantMessage):
                            for block in msg.content or []:
                                if hasattr(block, "text") and block.text:
                                    _msg_tokens += max(1, len(block.text) // 4)
                    except ImportError:
                        pass
                if _msg_tokens > 0:
                    _planner_token_count[0] += _msg_tokens
                    now = time.monotonic()
                    if now - _last_token_update[0] >= 2.0:
                        _last_token_update[0] = now
                        token_line = f"⚙ Planner generating… ({_planner_token_count[0]:,} tokens)"
                        if pipeline_id:
                            await self._emit(
                                "planner:output",
                                {"line": token_line},
                                db=db,
                                pipeline_id=pipeline_id,
                            )
                        else:
                            await self._events.emit("planner:output", {"line": token_line})

                text = _extract_activity(msg) if not isinstance(msg, str) else msg
                if not text:
                    return
                _last_token_update[0] = time.monotonic()
                if pipeline_id:
                    await self._emit(
                        "planner:output", {"line": text}, db=db, pipeline_id=pipeline_id
                    )
                else:
                    await self._events.emit("planner:output", {"line": text})

            # Planning question support via asyncio.Event synchronization
            pending_planning_answer: dict[str, asyncio.Event] = {}
            planning_answers: dict[str, str] = {}

            async def _on_planner_question(question_data: dict) -> str:
                """Called by planner when it has a question. Blocks until human answers."""
                q = await db.create_task_question(
                    task_id="__planning__",
                    pipeline_id=pipeline_id or "",
                    question=question_data["question"],
                    suggestions=question_data.get("suggestions"),
                    context={"text": question_data.get("context", "")},
                    stage="planning",
                )
                if pipeline_id:
                    await self._emit(
                        "planning:question",
                        {
                            "question_id": q.id,
                            "question": question_data,
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                else:
                    await self._events.emit(
                        "planning:question",
                        {
                            "question_id": q.id,
                            "question": question_data,
                        },
                    )

                event = asyncio.Event()
                pending_planning_answer[q.id] = event
                try:
                    timeout = self._settings.question_timeout
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                except TimeoutError:
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

            # Gather lessons for the planner
            try:
                lesson_rows = await db.get_relevant_lessons(
                    project_dir=self._project_dir,
                    categories=["review_failure", "code_pattern"],
                    max_count=20,
                )
                planner_lessons_block = format_lessons_block(
                    [row_to_lesson(r) for r in lesson_rows]
                )
            except Exception as exc:
                logger.warning("Failed to retrieve lessons for planner: %s", exc)
                planner_lessons_block = ""

            try:
                planning_result = await unified_planner.run(
                    user_input=user_input,
                    spec_text=spec_text,
                    snapshot_text=snapshot_text,
                    conventions=planner_prompt_modifier,
                    on_message=_on_unified_msg,
                    on_question=_on_planner_question,
                    lessons_block=planner_lessons_block,
                )
            finally:
                # Clean up listener to prevent accumulation
                handlers = self._events._handlers.get("planning:answer", [])
                if _on_planning_answer in handlers:
                    handlers.remove(_on_planning_answer)

            if planning_result.task_graph is None:
                raise RuntimeError("Unified planner failed to produce a TaskGraph")

            graph = planning_result.task_graph

            # Safety net: drop tasks for excluded repos even if the planner
            # ignored the instruction. This is a hard programmatic filter.
            if len(self._repos) > 1 and excluded_repos:
                before = len(graph.tasks)
                graph.tasks = [
                    t for t in graph.tasks if getattr(t, "repo", None) not in excluded_repos
                ]
                dropped = before - len(graph.tasks)
                if dropped:
                    logger.info(
                        "Dropped %d tasks for excluded repos: %s",
                        dropped,
                        ", ".join(sorted(excluded_repos)),
                    )

            # Track costs
            if pipeline_id and planning_result.total_cost_usd > 0:
                await db.add_pipeline_cost(pipeline_id, planning_result.total_cost_usd)
                await db.set_pipeline_planner_cost(pipeline_id, planning_result.total_cost_usd)
                total_cost = await db.get_pipeline_cost(pipeline_id)
                await self._emit(
                    "pipeline:cost_update",
                    {
                        "planner_cost_usd": planning_result.total_cost_usd,
                        "total_cost_usd": total_cost,
                        "cost_breakdown": planning_result.cost_breakdown,
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
        else:
            # Existing simple planner path
            graph = await planner.plan(
                user_input, context=self._snapshot.format_for_planner(), on_message=_on_planner_msg
            )

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
                    await self._emit(
                        "pipeline:cost_update",
                        {
                            "planner_cost_usd": sdk_result.cost_usd,
                            "total_cost_usd": total_cost,
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )

        await _planner_progress("Validating plan…")

        # Emit pipeline cost estimate
        if pipeline_id and graph.tasks:
            estimated = await estimate_pipeline_cost(
                len(graph.tasks),
                self._settings,
                strategy,
            )
            await self._emit(
                "pipeline:cost_estimate",
                {
                    "estimated_cost_usd": estimated,
                    "task_count": len(graph.tasks),
                },
                db=db,
                pipeline_id=pipeline_id,
            )

        console.print(f"[green]Plan: {len(graph.tasks)} tasks[/green]")
        for task_def in graph.tasks:
            console.print(
                f"  - {task_def.id}: {task_def.title} [{task_def.complexity.value if hasattr(task_def.complexity, 'value') else task_def.complexity}]"
            )

        if emit_plan_ready:
            plan_data = {
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "files": t.files,
                        "depends_on": t.depends_on,
                        "complexity": t.complexity.value
                        if hasattr(t.complexity, "value")
                        else t.complexity,
                    }
                    for t in graph.tasks
                ]
            }
            if pipeline_id:
                await self._emit("pipeline:plan_ready", plan_data, db=db, pipeline_id=pipeline_id)
            else:
                await self._events.emit("pipeline:plan_ready", plan_data)

        # Explicitly transition phase to 'planned' AFTER plan_ready (if
        # emitted) so the frontend receives task data before the phase
        # changes.  This must run regardless of emit_plan_ready — the
        # phase still transitions even when plan_ready is suppressed.
        if pipeline_id:
            await self._emit(
                "pipeline:phase_changed", {"phase": "planned"}, db=db, pipeline_id=pipeline_id
            )
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
        await db.update_pipeline_status(pipeline_id, "contracts")

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
                graph,
                hints,
                project_context=context,
                on_message=_on_contract_msg,
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
                raise RuntimeError(
                    f"Contract generation failed (contracts_required=True): {exc}"
                ) from exc
            return ContractSet()

        # Track cost
        if builder_llm._last_sdk_result:
            sdk_result = builder_llm._last_sdk_result
            if sdk_result.cost_usd > 0:
                await db.add_pipeline_cost(pipeline_id, sdk_result.cost_usd)

        # Persist contracts
        if contract_set.has_contracts():
            await db.set_pipeline_contracts(
                pipeline_id,
                contract_set.model_dump_json(),
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

    async def execute(
        self,
        graph: TaskGraph,
        db: Database,
        pipeline_id: str | None = None,
        *,
        resume: bool = False,
    ) -> None:
        """Execute a previously approved TaskGraph.

        Args:
            resume: If True, skip task/agent creation (they already exist
                from the original run). Used by the resume endpoint.
        """
        pid = pipeline_id or getattr(self, "_pipeline_id", None) or str(uuid.uuid4())
        await self._emit("pipeline:phase_changed", {"phase": "executing"}, db=db, pipeline_id=pid)
        await db.update_pipeline_status(pid, "executing")
        # Reset phase-emission guards so review/merging phases emit correctly
        self._review_phase_emitted = False
        self._merging_phase_emitted = False
        self._last_scheduling_fingerprint = None
        prefix = pid[:8]

        # Multi-repo: validate repos and resolve base branches
        await self._init_repos()

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
                        pid,
                        self._contracts.model_dump_json(),
                    )

                # Re-emit plan_ready with prefixed IDs so TUI/subscribers
                # have the correct task keys for state_changed events
                await self._emit(
                    "pipeline:plan_ready",
                    {
                        "tasks": [
                            {
                                "id": t.id,
                                "title": t.title,
                                "description": t.description,
                                "files": t.files,
                                "depends_on": t.depends_on,
                                "complexity": t.complexity.value
                                if hasattr(t.complexity, "value")
                                else t.complexity
                                if hasattr(t.complexity, "value")
                                else t.complexity,
                            }
                            for t in graph.tasks
                        ]
                    },
                    db=db,
                    pipeline_id=pid,
                )

            for task_def in graph.tasks:
                task_repo = getattr(task_def, "repo", None) or "default"
                await db.create_task(
                    id=task_def.id,
                    title=task_def.title,
                    description=task_def.description,
                    files=task_def.files,
                    depends_on=task_def.depends_on,
                    complexity=task_def.complexity.value
                    if hasattr(task_def.complexity, "value")
                    else task_def.complexity,
                    pipeline_id=pid,
                    repo_id=task_repo,
                )
            # Auto-scale agent pool from the widest dependency frontier, not
            # just root-task count. A one-root DAG can legitimately fan out
            # into multiple ready tasks after the first task completes.
            max_wave_width = _max_dependency_wave_width(graph.tasks)
            self._effective_max_agents = min(
                max_wave_width,
                self._settings.max_agents,
                len(graph.tasks),
            )
        else:
            # Resume path: restore contracts and recalculate agent scaling
            contracts_json = await db.get_pipeline_contracts(pid)
            if contracts_json:
                try:
                    self._contracts = ContractSet.model_validate_json(contracts_json)
                except Exception:
                    logger.warning("Failed to restore contracts from DB — using empty set")
                    self._contracts = ContractSet()
            else:
                self._contracts = ContractSet()

            # Recalculate effective max agents based on remaining work
            tasks = await db.list_tasks_by_pipeline(pid)
            remaining = sum(1 for t in tasks if t.state in ("todo", "in_review", "blocked"))
            self._effective_max_agents = min(max(remaining, 1), self._settings.max_agents)
        await _ensure_agent_pool_size(db, prefix, self._effective_max_agents)

        monitor = ResourceMonitor(
            cpu_threshold=self._settings.cpu_threshold,
            memory_threshold_pct=self._settings.memory_threshold_pct,
            disk_threshold_gb=self._settings.disk_threshold_gb,
        )
        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)

        # Initialize lesson stores
        # Determine pipeline branch name: use user-supplied name, or auto-generate from description
        pipeline_record = await db.get_pipeline(pid)

        # On resume/retry, use the stored base branch from the original run.
        # Re-detecting via _get_current_branch would pick up whatever the user
        # has checked out NOW, which may be different from the original base.
        # Use the base branch stored by the TUI (user's explicit choice).
        # Fall back to detecting the current checkout only if not stored.
        base_branch = getattr(pipeline_record, "base_branch", None) or await _get_current_branch(
            next(iter(self._repos.values())).path
        )
        custom_branch = getattr(pipeline_record, "branch_name", None) if pipeline_record else None
        if custom_branch and custom_branch.strip():
            pipeline_branch = custom_branch.strip()
        else:
            description = pipeline_record.description if pipeline_record else ""
            pipeline_branch = (
                (await _generate_branch_name(description))
                if description
                else f"forge/pipeline-{pid[:8]}"
            )
        # Persist the final computed branch name so the PR creation endpoint can use it
        await db.set_pipeline_branch_name(pid, pipeline_branch)
        # Notify TUI so diff views can resolve the branch immediately
        await self._emit(
            "pipeline:branch_resolved", {"branch": pipeline_branch}, db=db, pipeline_id=pid
        )

        # Set up per-repo infrastructure (worktree managers, merge workers, pipeline branches)
        self._setup_per_repo_infra(pipeline_branch)

        # Create pipeline branches in all repos.
        # _create_pipeline_branches is idempotent (git branch -f).
        if not resume:
            await self._create_pipeline_branches()
            await db.set_pipeline_base_branch(pid, base_branch)
        else:
            # Verify the branch still exists in at least one repo
            branch_exists = False
            for rc in self._repos.values():
                branch_check = await async_subprocess(
                    ["git", "rev-parse", "--verify", pipeline_branch],
                    cwd=rc.path,
                )
                if branch_check.returncode == 0:
                    branch_exists = True
                    break

            if not branch_exists:
                console.print(
                    f"[yellow]Pipeline branch {pipeline_branch} missing — recreating from {base_branch}[/yellow]"
                )
                await self._create_pipeline_branches()

        console.print(f"[dim]Merge target: {pipeline_branch} (base: {base_branch})[/dim]")

        # Load per-repo configs for review gates (build/test/lint commands)
        self._repo_configs = load_repo_configs(self._repos)

        # Update repos_json with per-repo branch names
        if len(self._repos) > 1:
            await update_repos_json_branches(db, pid, self._pipeline_branches)

        # For backward compat, keep single-object references for _execution_loop
        worktree_mgr = self._worktree_managers.get(
            "default", next(iter(self._worktree_managers.values()))
        )
        merge_worker = self._merge_workers.get("default", next(iter(self._merge_workers.values())))

        # ── Integration baseline capture ────────────────────────────
        from forge.config.project_config import ProjectConfig
        from forge.core.integration import (
            capture_baseline,
            effective_enabled,
            run_final_gate,
        )

        project_config = ProjectConfig.load(self._project_dir)
        integration_config = project_config.integration
        pm_enabled = effective_enabled(integration_config.post_merge)
        fg_enabled = effective_enabled(integration_config.final_gate)

        if pm_enabled or fg_enabled:
            await self._emit(
                "integration:baseline_started",
                {},
                db=db,
                pipeline_id=pid,
            )
            # Use post_merge cmd for baseline; fall back to final_gate if only that is enabled
            baseline_cfg = (
                integration_config.post_merge if pm_enabled else integration_config.final_gate
            )
            # Use first repo path for integration checks (integration commands
            # run in a worktree of a real git repo, not the workspace wrapper).
            _integration_repo_path = next(iter(self._repos.values())).path
            baseline_exit = await capture_baseline(
                baseline_cfg,
                _integration_repo_path,
                base_branch,
            )

            if baseline_exit is not None and baseline_exit != 0:
                # Baseline is red — ask user: ignore or cancel
                await self._emit(
                    "integration:baseline_failed_prompt",
                    {
                        "exit_code": baseline_exit,
                    },
                    db=db,
                    pipeline_id=pid,
                )

                action = await self._wait_for_integration_response(
                    db,
                    pid,
                    "baseline",
                )
                if action == "cancel_pipeline":
                    await self._emit(
                        "pipeline:phase_changed",
                        {
                            "phase": "cancelled",
                        },
                        db=db,
                        pipeline_id=pid,
                    )
                    return

                await self._emit(
                    "integration:baseline_response",
                    {
                        "action": "ignore_and_continue",
                    },
                    db=db,
                    pipeline_id=pid,
                )

            await self._emit(
                "integration:baseline_result",
                {
                    "status": (
                        "passed"
                        if baseline_exit == 0
                        else ("failed" if baseline_exit is not None else "skipped")
                    ),
                    "exit_code": baseline_exit,
                },
                db=db,
                pipeline_id=pid,
            )

            await db.set_baseline_exit_code(pid, baseline_exit)
            self._integration_config = integration_config
            self._baseline_exit_code = baseline_exit
        else:
            self._integration_config = None
            self._baseline_exit_code = None

        await self._execution_loop(db, runtime, worktree_mgr, merge_worker, monitor, pid)

        # Auto-update conventions file after all tasks complete successfully
        if self._settings.auto_update_conventions:
            pipeline_rec = await db.get_pipeline(pid)
            conventions_json_str = (
                getattr(pipeline_rec, "conventions_json", None) if pipeline_rec else None
            )
            if conventions_json_str:
                try:
                    conventions = json.loads(conventions_json_str)
                    from forge.core.conventions import update_conventions_file

                    update_conventions_file(self._project_dir, conventions)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to parse conventions_json for auto-update")

        # ── Integration final gate ──────────────────────────────────
        if self._integration_config and fg_enabled:
            await self._emit(
                "integration:final_gate_started",
                {},
                db=db,
                pipeline_id=pid,
            )
            _fg_repo_path = next(iter(self._repos.values())).path
            fg_result = await run_final_gate(
                self._integration_config.final_gate,
                _fg_repo_path,
                pipeline_branch,
            )
            await self._emit(
                "integration:final_gate_result",
                {
                    "status": fg_result.status,
                    "exit_code": fg_result.exit_code,
                    "stderr": fg_result.stderr[-2000:] if fg_result.stderr else "",
                },
                db=db,
                pipeline_id=pid,
            )

            if fg_result.status in ("failed", "timeout"):
                action = await self._resolve_integration_failure(
                    self._integration_config.final_gate,
                    fg_result,
                    db,
                    pid,
                    task_id=None,
                    phase="final_gate",
                )
                if action == "stop_pipeline":
                    await self._emit(
                        "pipeline:phase_changed",
                        {
                            "phase": "error",
                        },
                        db=db,
                        pipeline_id=pid,
                    )
                    return
            elif fg_result.status == "infra_error":
                logger.warning(
                    "Final gate infra error: %s — skipping",
                    fg_result.stderr[:200],
                )

        await self._emit("pipeline:phase_changed", {"phase": "complete"}, db=db, pipeline_id=pid)

    async def retry_task(self, task_id: str, db: Database, pipeline_id: str) -> None:
        """Reset a failed task to 'todo' and re-queue it for execution.

        Clears the worktree if one exists, resets the task state in DB,
        and emits a task:state_changed event so the TUI/subscribers update.
        """
        # Guard: only retry tasks in ERROR or CANCELLED state
        task_row = await db.get_task(task_id)
        if task_row and task_row.state not in ("error", "cancelled"):
            logger.warning(
                "Cannot retry task %s: current state '%s' is not error or cancelled",
                task_id,
                task_row.state,
            )
            return

        # Reset state in DB
        await db.update_task_state(task_id, "todo")

        # Clear worktree if it exists — use per-repo infra if available
        try:
            task_row = await db.get_task(task_id)
            repo_id = getattr(task_row, "repo_id", "default") if task_row else "default"
            if hasattr(self, "_worktree_managers") and repo_id in self._worktree_managers:
                wt_mgr, _, _ = self._get_repo_infra(repo_id)
            else:
                worktree_base = os.path.join(self._project_dir, ".forge", "worktrees")
                wt_mgr = WorktreeManager(self._project_dir, worktree_base)
            wt_mgr.remove(task_id)
        except Exception:
            logger.debug("No worktree to clean for task %s (or already removed)", task_id)

        # Cascade-reset downstream BLOCKED tasks so they can run once this
        # task succeeds.  Only unblock tasks whose ONLY failed/retried
        # dependency is this task — leave tasks that depend on OTHER errored
        # tasks alone (they'll remain blocked by those).
        try:
            all_tasks = await (
                db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks()
            )
            # Build a set of currently non-done, non-todo task IDs (error/blocked/etc.)
            # excluding the task we just reset (which is now todo).
            non_done_ids = frozenset(
                t.id
                for t in all_tasks
                if t.id != task_id and t.state not in ("done", "todo", "in_progress")
            )
            for t in all_tasks:
                if t.state != "blocked":
                    continue
                deps = t.depends_on or []
                if not deps:
                    continue
                # Only unblock if task_id is the sole non-done dependency
                blocking_deps = [d for d in deps if d in non_done_ids or d == task_id]
                if blocking_deps == [task_id]:
                    await db.update_task_state(t.id, "todo")
                    await self._emit(
                        "task:state_changed",
                        {"task_id": t.id, "state": "todo"},
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                    logger.info("Unblocked task %s (was waiting on retried task %s)", t.id, task_id)
        except Exception:
            logger.debug(
                "Failed to cascade-unblock dependents of %s (non-fatal)", task_id, exc_info=True
            )

        # Re-add to scheduler by emitting state change
        await self._emit(
            "task:state_changed",
            {"task_id": task_id, "state": "todo"},
            db=db,
            pipeline_id=pipeline_id,
        )
        logger.info("Task %s reset to 'todo' for retry", task_id)

    # ── Integration health check helpers ─────────────────────────────

    async def _wait_for_integration_response(
        self,
        db: Database,
        pipeline_id: str,
        phase: str,
    ) -> str:
        """Block until the user responds to an integration prompt.

        Args:
            phase: "baseline" → listens for integration:baseline_response
                   anything else → listens for integration:check_response

        Returns:
            "ignore_and_continue", "stop_pipeline", or "cancel_pipeline".
        """
        response_event = asyncio.Event()
        response_value: dict[str, str | None] = {"action": None}

        event_type = (
            "integration:baseline_response" if phase == "baseline" else "integration:check_response"
        )

        async def _handler(data: dict) -> None:
            response_value["action"] = data.get("action", "ignore_and_continue")
            response_event.set()

        self._events.on(event_type, _handler)
        try:
            await asyncio.wait_for(response_event.wait(), timeout=300)
        except TimeoutError:
            logger.warning(
                "Integration response timed out after 300s for %s — defaulting to ignore_and_continue",
                phase,
            )
            response_value["action"] = "ignore_and_continue"
        finally:
            handlers = self._events._handlers.get(event_type, [])
            if _handler in handlers:
                handlers.remove(_handler)

        return response_value["action"] or "ignore_and_continue"

    async def _resolve_integration_failure(
        self,
        config,  # IntegrationCheckConfig
        result,  # IntegrationCheckResult
        db: Database,
        pipeline_id: str,
        task_id: str | None,
        phase: str,
    ) -> str:
        """Determine action on health check failure based on config.on_failure.

        Returns "ignore_and_continue" or "stop_pipeline".
        """
        if config.on_failure == "ignore_and_continue":
            logger.warning(
                "Integration check failed (exit=%s) but on_failure=ignore_and_continue",
                result.exit_code,
            )
            return "ignore_and_continue"

        if config.on_failure == "stop_pipeline":
            logger.error(
                "Integration check failed (exit=%s) and on_failure=stop_pipeline",
                result.exit_code,
            )
            return "stop_pipeline"

        # on_failure == "ask" — emit prompt and wait for user
        await self._emit(
            "integration:check_prompt",
            {
                "task_id": task_id,
                "cmd": config.cmd,
                "exit_code": result.exit_code,
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "is_regression": result.is_regression,
                "baseline_was_red": (getattr(self, "_baseline_exit_code", None) or 0) != 0,
                "options": ["ignore_and_continue", "stop_pipeline"],
                "phase": phase,
            },
            db=db,
            pipeline_id=pipeline_id,
        )

        return await self._wait_for_integration_response(
            db,
            pipeline_id,
            "check",
        )

    async def run(
        self, user_input: str, spec_path: str | None = None, deep_plan: bool = False
    ) -> None:
        """Full pipeline for CLI: plan + execute. Maintains backward compat."""
        from forge.core.paths import forge_db_url

        db = Database(forge_db_url())
        await db.initialize()
        try:
            pruned = await db.prune_stale_lessons()
            if pruned:
                logger.info("Pruned %d stale lessons at startup", pruned)
        except Exception:
            logger.debug("Failed to prune stale lessons (non-fatal)", exc_info=True)
        try:
            self._pipeline_id = str(uuid.uuid4())
            await db.create_pipeline(
                id=self._pipeline_id,
                description=user_input,
                project_dir=self._project_dir,
                model_strategy=self._strategy,
                budget_limit_usd=self._settings.budget_limit_usd,
                project_path=self._project_dir,
                project_name=os.path.basename(self._project_dir),
            )
            graph = await self.plan(
                user_input,
                db,
                pipeline_id=self._pipeline_id,
                spec_path=spec_path,
                deep_plan=deep_plan,
            )
            # Contract generation phase
            self._contracts = await self.generate_contracts(graph, db, self._pipeline_id)
            try:
                await check_budget(db, self._pipeline_id, self._settings)
            except BudgetExceededError as exc:
                await self._emit(
                    "pipeline:budget_exceeded",
                    {
                        "spent": exc.spent,
                        "limit": exc.limit,
                    },
                    db=db,
                    pipeline_id=self._pipeline_id,
                )
                await db.update_pipeline_status(self._pipeline_id, "error")
                raise
            await self.execute(graph, db, pipeline_id=self._pipeline_id)
        except Exception:
            # Clean up pipeline branches on error to avoid orphaned branches
            await self._cleanup_pipeline_branches()
            raise
        finally:
            await db.close()

    async def dry_run(
        self, user_input: str, spec_path: str | None = None, deep_plan: bool = False
    ) -> dict:
        """Plan-only mode: build the task graph without executing agents.

        Returns a dict with the planned graph, cost estimate, and model assignments.
        Creates a pipeline in DB with status 'dry_run'.
        """
        from forge.core.paths import forge_db_url

        db = Database(forge_db_url())
        await db.initialize()
        try:
            self._pipeline_id = str(uuid.uuid4())
            await db.create_pipeline(
                id=self._pipeline_id,
                description=user_input,
                project_dir=self._project_dir,
                model_strategy=self._strategy,
                budget_limit_usd=self._settings.budget_limit_usd,
                project_path=self._project_dir,
                project_name=os.path.basename(self._project_dir),
            )
            graph = await self.plan(
                user_input,
                db,
                pipeline_id=self._pipeline_id,
                spec_path=spec_path,
                deep_plan=deep_plan,
            )
            cost = await estimate_pipeline_cost(len(graph.tasks), self._settings, self._strategy)
            model_assignments = {
                t.id: select_model(self._strategy, "agent", t.complexity.value) for t in graph.tasks
            }
            await db.update_pipeline_status(self._pipeline_id, "dry_run")
            return {
                "graph": graph,
                "cost_estimate": cost,
                "model_assignments": model_assignments,
            }
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
                logger.info("Auto-answered timed-out question %s for task %s", q.id, q.task_id)
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
                logger.exception("Failed to auto-answer question %s for task %s", q.id, q.task_id)

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
        self,
        db,
        runtime,
        worktree_mgr,
        merge_worker,
        task_id: str,
        agent_id: str,
        pipeline_id: str | None = None,
        repo_id: str | None = None,
    ) -> None:
        """Wrapper ensuring cleanup on cancellation or crash."""
        # Complexity-based timeout as a safety net beyond SDK-level timeout
        _COMPLEXITY_TIMEOUTS = {"low": 1800, "medium": 3600, "high": 7200}
        task_timeout = _COMPLEXITY_TIMEOUTS.get("medium")  # default 60min
        try:
            task_row = await db.get_task(task_id)
            if task_row:
                complexity = getattr(task_row, "complexity", "medium") or "medium"
                task_timeout = _COMPLEXITY_TIMEOUTS.get(complexity, 3600)
        except Exception:
            pass  # Use default timeout on DB error

        try:
            await asyncio.wait_for(
                self._execute_task(
                    db,
                    runtime,
                    worktree_mgr,
                    merge_worker,
                    task_id,
                    agent_id,
                    pipeline_id=pipeline_id,
                    repo_id=repo_id,
                ),
                timeout=task_timeout,
            )
        except TimeoutError:
            logger.error(
                "Task %s timed out after %ds (complexity-based safety net)",
                task_id,
                task_timeout,
            )
            try:
                await db.set_task_error(task_id, f"Task timed out after {task_timeout}s")
                await db.update_task_state(task_id, TaskState.ERROR.value)
                await self._emit(
                    "task:state_changed",
                    {
                        "task_id": task_id,
                        "state": "error",
                        "error": f"Task timed out after {task_timeout}s",
                    },
                    db=db,
                    pipeline_id=pipeline_id or "",
                )
            except Exception:
                logger.debug("Failed to mark timed-out task %s as error", task_id)
        except asyncio.CancelledError:
            logger.info("Task %s was cancelled (shutdown)", task_id)
            raise
        finally:
            released = False
            for attempt in range(3):
                try:
                    await db.release_agent(agent_id)
                    released = True
                    break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))  # 0.5s, 1s
                        logger.warning(
                            "Failed to release agent %s (attempt %d/3), retrying...",
                            agent_id,
                            attempt + 1,
                        )
                    else:
                        logger.error(
                            "All retries exhausted releasing agent %s for task %s",
                            agent_id,
                            task_id,
                        )
            if not released:
                try:
                    await db.force_release_agent(agent_id)
                except Exception:
                    logger.critical("SLOT LEAK: Cannot release agent %s by any means", agent_id)

    async def _handle_task_exception(
        self,
        task_id: str,
        exc: BaseException,
        db,
        worktree_mgr,
        pipeline_id: str | None,
    ) -> None:
        """Handle a task that raised an unhandled exception in the pool.

        Instead of permanently marking the task as ERROR, attempt a retry
        via ``_handle_retry`` if retries remain.  This is the safety net
        that catches transient infrastructure failures (DB locks, ESLint
        timeouts, disk errors) and gives the task another chance.
        """
        logger.error("Task %s raised unhandled exception: %s", task_id, exc, exc_info=exc)

        # Release the agent slot first — this must happen regardless of retry.
        try:
            task_rec = await db.get_task(task_id)
            if task_rec and task_rec.assigned_agent:
                await db.release_agent(task_rec.assigned_agent)
        except Exception:
            logger.exception("Failed to release agent for crashed task %s", task_id)

        # Attempt retry instead of permanent death.
        try:
            task_rec = task_rec or await db.get_task(task_id)
            if task_rec and task_rec.retry_count < self._settings.max_retries:
                crash_feedback = (
                    f"[INFRASTRUCTURE CRASH] Task crashed with {type(exc).__name__}: {str(exc)[:300]}\n"
                    "This was an infrastructure/runtime error, not a code quality issue. "
                    "The previous code changes in the worktree are preserved — retry from where you left off."
                )
                logger.warning(
                    "Task %s crashed, retrying (%d/%d): %s",
                    task_id,
                    task_rec.retry_count + 1,
                    self._settings.max_retries,
                    exc,
                )
                await self._handle_retry(
                    db,
                    task_id,
                    worktree_mgr,
                    review_feedback=crash_feedback,
                    pipeline_id=pipeline_id,
                )
                return  # Retry scheduled — do NOT mark as permanent error
        except Exception:
            logger.exception(
                "Failed to schedule retry for crashed task %s, falling back to ERROR", task_id
            )

        # Retry not possible (max retries exceeded or retry itself failed) — permanent ERROR.
        try:
            await db.set_task_error(task_id, str(exc))
            await db.update_task_state(task_id, TaskState.ERROR.value)
            await self._emit(
                "task:state_changed",
                {
                    "task_id": task_id,
                    "state": "error",
                    "error": str(exc),
                },
                db=db,
                pipeline_id=pipeline_id or "",
            )
        except Exception:
            logger.exception("Failed to mark crashed task %s as error", task_id)
        try:
            await worktree_mgr.async_remove(task_id)
        except Exception as cleanup_err:
            logger.warning("Failed to clean up worktree for task %s: %s", task_id, cleanup_err)
        if pipeline_id:
            try:
                remaining = await db.list_tasks_by_pipeline(pipeline_id)
                terminal = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value)
                if all(t.state in terminal for t in remaining):
                    await self._emit(
                        "pipeline:error",
                        {
                            "error": f"Pipeline failed: task {task_id} crashed",
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
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
        self,
        db: Database,
        runtime: AgentRuntime,
        worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker,
        monitor: ResourceMonitor,
        pipeline_id: str | None = None,
    ) -> None:
        """Loop until all tasks are DONE or ERROR."""
        # Start health monitor as a background task
        from forge.core.health_monitor import PipelineHealthMonitor

        health_monitor = None
        health_task = None
        if pipeline_id:

            async def _on_stuck_task(task_id: str, reason: str) -> None:
                logger.warning("Health monitor: task %s stuck — %s", task_id, reason)
                await self._emit(
                    "task:agent_output",
                    {"task_id": task_id, "line": f"⚠ Health monitor: {reason}"},
                    db=db,
                    pipeline_id=pipeline_id,
                )

            health_monitor = PipelineHealthMonitor(
                db=db,
                pipeline_id=pipeline_id,
                on_stuck_task=_on_stuck_task,
            )
            self._health_monitor = health_monitor
            health_task = asyncio.create_task(health_monitor.run(), name="health-monitor")

        try:
            await self._execution_loop_inner(
                db, runtime, worktree_mgr, merge_worker, monitor, pipeline_id
            )
        except Exception as e:
            logger.error("Execution loop crashed: %s", e, exc_info=True)
            try:
                await self._emit(
                    "pipeline:error",
                    {"error": f"Pipeline crashed: {e}"},
                    db=db,
                    pipeline_id=pipeline_id or "",
                )
                await self._emit(
                    "pipeline:phase_changed",
                    {"phase": "error"},
                    db=db,
                    pipeline_id=pipeline_id or "",
                )
            except Exception:
                logger.exception("Failed to emit pipeline:error event after execution loop crash")
            raise
        finally:
            # Stop health monitor
            if health_monitor:
                health_monitor.stop()
            if health_task and not health_task.done():
                health_task.cancel()
                try:
                    await health_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._health_monitor = None

            self._cleanup_answer_handler()
            await self._shutdown_active_tasks()
            if pipeline_id:
                try:
                    await db.clear_executor_info(pipeline_id)
                except Exception:
                    logger.debug("Failed to clear executor info for pipeline %s", pipeline_id)

    def _cleanup_answer_handler(self) -> None:
        """Remove the task:answer listener to prevent accumulation on re-entry."""
        handler = getattr(self, "_current_answer_handler", None)
        if handler:
            self._events.off("task:answer", handler)
            self._current_answer_handler = None

    async def _execution_loop_inner(
        self,
        db: Database,
        runtime: AgentRuntime,
        worktree_mgr: WorktreeManager,
        merge_worker: MergeWorker,
        monitor: ResourceMonitor,
        pipeline_id: str | None = None,
    ) -> None:
        """Inner execution loop body — wrapped by _execution_loop for error handling."""
        import uuid as _uuid_mod

        prefix = pipeline_id[:8] if pipeline_id else None
        start_time = asyncio.get_running_loop().time()
        timeout = self._settings.pipeline_timeout_seconds
        # Pipeline pause tracking: timestamp (loop time) when all tasks went into awaiting_input
        _all_paused_since: float | None = None
        # Throttle question-timeout checks: only run every 30 seconds
        _last_timeout_check: float = 0.0
        # Throttle answered-question recovery to the scheduler poll cadence
        _last_answer_recovery_check: float = 0.0
        self._active_tasks.clear()
        # _effective_max_agents is set in __init__ (default) and recalculated
        # in execute() based on DAG width — do NOT overwrite here on loop re-entry.
        self._executor_token = str(_uuid_mod.uuid4())

        # Store dependencies for event-driven resume
        self._runtime = runtime
        self._worktree_mgr = worktree_mgr
        self._merge_worker = merge_worker

        # Register task:answer listener for event-driven resume
        # Clean up any stale handler from a previous loop entry to prevent accumulation
        self._cleanup_answer_handler()

        async def _answer_handler(data):
            await self._on_task_answered(data=data, db=db)

        self._events.on("task:answer", _answer_handler)

        # Recover tasks that were answered while daemon was down
        if pipeline_id:
            await self._recover_answered_questions(db, pipeline_id)

        if pipeline_id:
            await db.set_executor_info(pipeline_id, pid=os.getpid(), token=self._executor_token)
        self._current_answer_handler = _answer_handler
        while True:
            # Reap completed tasks from the pool
            done_ids = [tid for tid, atask in self._active_tasks.items() if atask.done()]
            for tid in done_ids:
                async with self._active_tasks_lock:
                    atask = self._active_tasks.pop(tid, None)
                if atask is None:
                    continue  # Already removed by concurrent event handler
                # Invalidate task cache — task state changed
                self._cached_tasks = None
                exc = atask.exception() if not atask.cancelled() else None
                if exc:
                    await self._handle_task_exception(tid, exc, db, worktree_mgr, pipeline_id)

            # Watchdog: check elapsed time
            elapsed = asyncio.get_running_loop().time() - start_time
            if timeout > 0 and elapsed > timeout:
                logger.error("Pipeline timeout exceeded (%ds > %ds)", int(elapsed), timeout)
                all_tasks = await (
                    db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks()
                )
                for t in all_tasks:
                    if t.state not in (
                        TaskState.DONE.value,
                        TaskState.ERROR.value,
                        TaskState.CANCELLED.value,
                    ):
                        await db.update_task_state(t.id, TaskState.ERROR.value)
                        await self._emit(
                            "task:state_changed",
                            {
                                "task_id": t.id,
                                "state": "error",
                                "error": "Pipeline timeout exceeded",
                            },
                            db=db,
                            pipeline_id=pipeline_id or "",
                        )
                break

            # Check pause flag — don't dispatch new tasks while paused
            if pipeline_id:
                pipeline_rec = await db.get_pipeline(pipeline_id)
                if pipeline_rec and getattr(pipeline_rec, "paused", False):
                    # Don't dispatch new tasks. Already-running tasks continue.
                    await asyncio.sleep(self._settings.scheduler_poll_interval)
                    continue

            # Use cached task list with TTL to avoid DB fetch every poll iteration
            now_mono = time.monotonic()
            if (
                self._cached_tasks is None
                or now_mono - self._task_cache_time > self._settings.scheduler_poll_interval
            ):
                self._cached_tasks = await (
                    db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks()
                )
                self._task_cache_time = now_mono
            tasks = self._cached_tasks
            _print_status_table(tasks)

            # Periodic question-timeout checker (every 30 s)
            if pipeline_id:
                now = asyncio.get_running_loop().time()
                if now - _last_timeout_check >= 30.0:
                    _last_timeout_check = now
                    await self._check_question_timeouts(db, pipeline_id)

            # AWAITING_APPROVAL, BLOCKED, and CANCELLED count as "parked"
            # Note: AWAITING_INPUT is NOT parked — the loop needs to poll for
            # answered questions and emit pause/resume events.
            parked_states = (
                TaskState.DONE.value,
                TaskState.ERROR.value,
                TaskState.AWAITING_APPROVAL.value,
                TaskState.CANCELLED.value,
                TaskState.BLOCKED.value,
            )
            # Single-pass state counter — replaces multiple O(N) iterations
            state_counts: dict[str, int] = {}
            for t in tasks:
                state_counts[t.state] = state_counts.get(t.state, 0) + 1

            if pipeline_id and state_counts.get(TaskState.AWAITING_INPUT.value, 0) > 0:
                now = asyncio.get_running_loop().time()
                if now - _last_answer_recovery_check >= self._settings.scheduler_poll_interval:
                    _last_answer_recovery_check = now
                    await self._recover_answered_questions(db, pipeline_id)

            all_parked = all(t.state in parked_states for t in tasks)
            if all_parked:
                # If any tasks are still awaiting approval or input, sleep and poll
                # rather than exiting — answers/approvals create new work
                has_awaiting = (
                    state_counts.get(TaskState.AWAITING_APPROVAL.value, 0) > 0
                    or state_counts.get(TaskState.AWAITING_INPUT.value, 0) > 0
                )
                if has_awaiting:
                    await asyncio.sleep(self._settings.scheduler_poll_interval)
                    continue
                # All tasks are truly terminal (done/error/blocked/cancelled)
                done_count = state_counts.get(TaskState.DONE.value, 0)
                error_count = state_counts.get(TaskState.ERROR.value, 0)
                blocked_count = state_counts.get(TaskState.BLOCKED.value, 0)
                cancelled_count = state_counts.get(TaskState.CANCELLED.value, 0)
                total_count = len(tasks)

                result = _classify_pipeline_result([t.state for t in tasks])
                if result == "complete":
                    console.print(
                        f"\n[bold green]Complete: {done_count}/{total_count} done[/bold green]"
                    )
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
                        paused_elapsed = asyncio.get_running_loop().time() - _all_paused_since
                        _all_paused_since = None
                        await db.add_pipeline_paused_duration(pipeline_id, paused_elapsed)
                        await db.set_pipeline_paused_at(pipeline_id, None)
                    await db.update_pipeline_status(pipeline_id, result)
                    # Aggregate task-level metrics into pipeline-level metrics
                    try:
                        await db.finalize_pipeline_metrics(pipeline_id)
                    except Exception:
                        logger.debug(
                            "Failed to finalize pipeline metrics for %s",
                            pipeline_id,
                            exc_info=True,
                        )
                    await self._emit(
                        "pipeline:all_tasks_done",
                        {
                            "summary": {
                                "done": done_count,
                                "error": error_count,
                                "blocked": blocked_count,
                                "cancelled": cancelled_count,
                                "total": total_count,
                                "result": result,
                            },
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                break

            # Pipeline pause tracking: detect when ALL non-terminal active tasks are awaiting_input
            if pipeline_id:
                non_terminal = [
                    t
                    for t in tasks
                    if t.state
                    not in (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value)
                ]
                all_awaiting_input = bool(non_terminal) and all(
                    t.state == TaskState.AWAITING_INPUT.value for t in non_terminal
                )
                if all_awaiting_input:
                    if _all_paused_since is None:
                        # Transition into paused state
                        _all_paused_since = asyncio.get_running_loop().time()
                        paused_at_iso = datetime.now(UTC).isoformat()
                        await db.set_pipeline_paused_at(pipeline_id, paused_at_iso)
                        await self._emit(
                            "pipeline:paused",
                            {
                                "reason": "awaiting_input",
                                "task_count": len(non_terminal),
                            },
                            db=db,
                            pipeline_id=pipeline_id,
                        )
                        logger.info(
                            "Pipeline %s paused — all %d tasks are awaiting_input",
                            pipeline_id,
                            len(non_terminal),
                        )
                else:
                    if _all_paused_since is not None:
                        # Tasks have resumed; accumulate pause duration
                        paused_elapsed = asyncio.get_running_loop().time() - _all_paused_since
                        _all_paused_since = None
                        await db.add_pipeline_paused_duration(pipeline_id, paused_elapsed)
                        await db.set_pipeline_paused_at(pipeline_id, None)
                        logger.info(
                            "Pipeline %s resumed after %.1fs pause",
                            pipeline_id,
                            paused_elapsed,
                        )

            snapshot = await monitor.take_snapshot()
            if not monitor.can_dispatch(snapshot):
                console.print(
                    f"[yellow]Backpressure: {', '.join(monitor.blocked_reasons(snapshot))}[/yellow]"
                )
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

            if pipeline_id:
                pipeline_rec = await db.get_pipeline(pipeline_id)
                if (
                    pipeline_rec
                    and pipeline_rec.executor_token
                    and pipeline_rec.executor_token != self._executor_token
                ):
                    console.print(
                        "[yellow]Pipeline taken over by another session. Exiting.[/yellow]"
                    )
                    break

            task_records = [row_to_record(t) for t in tasks]
            agents = await db.list_agents(prefix=prefix)
            from forge.core.models import row_to_agent

            agent_records = [row_to_agent(a) for a in agents]
            scheduling = Scheduler.analyze(task_records)
            dispatch_plan = Scheduler.dispatch_plan(
                task_records,
                agent_records,
                self._effective_max_agents,
            )
            if pipeline_id:
                await self._emit_scheduling_update(
                    db=db,
                    pipeline_id=pipeline_id,
                    analysis=scheduling,
                    agents=agents,
                    dispatch_plan=dispatch_plan,
                )

            # Check for in_review tasks that need resume dispatch
            has_in_review = any(
                t.state == TaskState.IN_REVIEW.value
                for t in tasks
                if t.id not in self._active_tasks
            )

            if not dispatch_plan and not has_in_review:
                if not self._active_tasks:
                    if any(
                        t.state
                        in (TaskState.AWAITING_APPROVAL.value, TaskState.AWAITING_INPUT.value)
                        for t in tasks
                    ):
                        await asyncio.sleep(self._settings.scheduler_poll_interval)
                        continue
                    # Check if there are still TODO tasks that can't be dispatched
                    # (e.g., all agents are busy, or dependencies haven't resolved yet)
                    has_todo = any(t.state == TaskState.TODO.value for t in tasks)
                    has_blocked = any(t.state == TaskState.BLOCKED.value for t in tasks)
                    if has_todo:
                        # TODO tasks exist but can't be dispatched — wait for agents
                        logger.debug("TODO tasks exist but no dispatch possible, waiting...")
                        await asyncio.sleep(self._settings.scheduler_poll_interval)
                        continue
                    if has_blocked and not any(
                        t.state in (TaskState.TODO.value, TaskState.IN_PROGRESS.value)
                        for t in tasks
                    ):
                        # All remaining non-terminal tasks are BLOCKED with nothing to unblock them
                        console.print(
                            "[bold red]All remaining tasks are blocked — dependency failure cascade.[/bold red]"
                        )
                    else:
                        console.print(
                            "[yellow]No tasks to dispatch and none in progress. Stopping.[/yellow]"
                        )
                    break
                _done, _pending = await asyncio.wait(
                    self._active_tasks.values(),
                    timeout=self._settings.scheduler_poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                continue

            # Guard: skip tasks already in pool
            dispatch_plan = [
                (tid, aid) for tid, aid in dispatch_plan if tid not in self._active_tasks
            ]

            # Also find in_review tasks eligible for resume dispatch.
            # These have code in worktrees but need re-review + merge.
            in_review_tasks = [
                t
                for t in task_records
                if t.state == TaskState.IN_REVIEW.value and t.id not in self._active_tasks
            ]

            # Cap to actual free slots (pool is authoritative)
            available_slots = max(0, self._effective_max_agents - len(self._active_tasks))
            dispatch_plan = dispatch_plan[:available_slots]

            # Launch into pool — normal TODO tasks
            for task_id, agent_id in dispatch_plan:
                await db.assign_task(task_id, agent_id)
                await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)

                # Route to the correct repo's infrastructure
                task_row = await db.get_task(task_id)
                repo_id = getattr(task_row, "repo_id", "default") if task_row else "default"

                # Fallback: if repo_id is "default" but we're in workspace mode,
                # use the first non-excluded repo as the target
                if repo_id == "default" and len(self._repos) > 1 and "default" not in self._repos:
                    repo_id = next(iter(self._repos.keys()))
                    logger.warning(
                        "Task %s has repo_id='default' in workspace mode, falling back to '%s'",
                        task_id,
                        repo_id,
                    )

                try:
                    wt_mgr, mw, _branch = self._get_repo_infra(repo_id)
                except ForgeError:
                    wt_mgr, mw = worktree_mgr, merge_worker

                atask = asyncio.create_task(
                    self._safe_execute_task(
                        db,
                        runtime,
                        wt_mgr,
                        mw,
                        task_id,
                        agent_id,
                        pipeline_id=pipeline_id,
                        repo_id=repo_id,
                    ),
                    name=f"forge-task-{task_id}",
                )
                async with self._active_tasks_lock:
                    self._active_tasks[task_id] = atask
                # Invalidate task cache — new task dispatched
                self._cached_tasks = None

            # Dispatch in_review tasks to review-only path (resume scenario).
            # These tasks already have code in worktrees — skip agent, re-review + merge.
            remaining_slots = max(0, self._effective_max_agents - len(self._active_tasks))
            idle_agents_for_review = [a for a in agent_records if a.state == AgentState.IDLE.value]
            # Only dispatch up to remaining slots worth of in_review tasks
            for ir_task in in_review_tasks[:remaining_slots]:
                if not idle_agents_for_review:
                    break
                ir_agent = idle_agents_for_review.pop(0)
                await db.assign_task(ir_task.id, ir_agent.id)

                task_row = await db.get_task(ir_task.id)
                repo_id = getattr(task_row, "repo_id", "default") if task_row else "default"
                if repo_id == "default" and len(self._repos) > 1 and "default" not in self._repos:
                    repo_id = next(iter(self._repos.keys()))

                try:
                    wt_mgr, mw, _branch = self._get_repo_infra(repo_id)
                except ForgeError:
                    wt_mgr, mw = worktree_mgr, merge_worker

                atask = asyncio.create_task(
                    self._execute_task_review_only(
                        db,
                        mw,
                        wt_mgr,
                        ir_task.id,
                        ir_agent.id,
                        pipeline_id=pipeline_id,
                        repo_id=repo_id,
                    ),
                    name=f"forge-task-review-{ir_task.id}",
                )
                async with self._active_tasks_lock:
                    self._active_tasks[ir_task.id] = atask
                self._cached_tasks = None

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

        self._cleanup_answer_handler()
