"""Forge TUI Application — main entry point for the terminal UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import replace as _dc_replace

from textual.app import App
from textual.binding import Binding
from textual.widgets import Input, TextArea

from forge.core.async_utils import safe_create_task
from forge.core.models import RepoConfig, TaskState
from forge.tui.bus import TUI_EVENT_TYPES, EmbeddedSource, EventBus
from forge.tui.screens.dry_run import DryRunScreen
from forge.tui.screens.final_approval import FinalApprovalScreen
from forge.tui.screens.home import HomeScreen, PromptTextArea
from forge.tui.screens.pipeline import PhaseBanner, PipelineScreen
from forge.tui.screens.plan_approval import PlanApprovalScreen
from forge.tui.screens.review import ReviewScreen
from forge.tui.screens.settings import SettingsScreen
from forge.tui.screens.stats import StatsScreen
from forge.tui.state import TuiState
from forge.tui.theme import APP_CSS as _APP_CSS
from forge.tui.widgets.command_palette import CommandPalette, CommandPaletteAction, get_all_actions
from forge.tui.widgets.pipeline_list import PipelineList

logger = logging.getLogger("forge.tui.app")

_FAILED_PR_TASK_STATES = frozenset(
    {
        TaskState.ERROR.value,
        TaskState.BLOCKED.value,
        TaskState.CANCELLED.value,
    }
)


def _escape_markup(text: str) -> str:
    """Escape Rich markup characters in error messages to prevent MarkupError crashes.

    Pydantic/exception messages often contain [ ] = characters that Rich
    interprets as markup tags. This escapes them for safe display in
    Textual notifications/toasts.
    """
    return str(text).replace("[", "\\[")


def _recent_pipeline_pr_counts(row: dict) -> tuple[int, int]:
    """Return (pr_count, repo_count) for a recent pipeline DB row."""
    pr_url = row.get("pr_url")
    repos_json = row.get("repos_json")
    if not repos_json:
        return (1 if pr_url else 0), 1
    try:
        repos = json.loads(repos_json)
    except Exception:
        return (1 if pr_url else 0), 1
    if not isinstance(repos, list) or not repos:
        return (1 if pr_url else 0), 1
    repo_count = max(1, len(repos))
    pr_count = sum(1 for repo in repos if isinstance(repo, dict) and repo.get("pr_url"))
    if repo_count == 1 and not pr_count and pr_url:
        pr_count = 1
    return pr_count, repo_count


def _build_task_summaries(
    tasks_list: list[dict],
    error_history: dict[str, list[str]] | None = None,
    review_substatus: dict[str, str] | None = None,
    merge_substatus: dict[str, str] | None = None,
    review_gates: dict[str, dict] | None = None,
) -> list[dict]:
    """Convert raw TUI state task dicts into PR-ready summary dicts.

    Raw state dicts have 'files' as a list of paths and diff stats nested
    inside 'merge_result'.  This helper normalises them into the flat shape
    that generate_pr_body and FinalApprovalScreen expect.
    """
    error_history = error_history or {}
    review_substatus = review_substatus or {}
    merge_substatus = merge_substatus or {}
    review_gates = review_gates or {}

    summaries = []
    for t in tasks_list:
        tid = t.get("id", "")
        mr = t.get("merge_result") or {}
        summaries.append(
            {
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "implementation_summary": mr.get("implementation_summary", "")
                or t.get("implementation_summary", ""),
                "state": t.get("state", "done"),
                "repo_id": t.get("repo_id", "default"),
                "cost_usd": t.get("cost_usd", 0),
                "added": mr.get("linesAdded", 0) if mr.get("success") else 0,
                "removed": mr.get("linesRemoved", 0) if mr.get("success") else 0,
                "files": mr.get("filesChanged", 0) if mr.get("success") else 0,
                "file_list": t.get("files", []) if isinstance(t.get("files"), list) else [],
                "tests_passed": t.get("tests_passed", 0),
                "tests_total": t.get("tests_total", 0),
                "review": "passed" if t.get("state") == "done" else "failed",
                "error": t.get("error", ""),
                # New enrichment fields
                "retry_count": len(error_history.get(tid, [])),
                "blocked_reason": t.get("error", "") if t.get("state") == "blocked" else "",
                "review_substatus": review_substatus.get(tid, ""),
                "merge_substatus": merge_substatus.get(tid, ""),
                "review_gates": review_gates.get(tid, {}),
            }
        )
    return summaries


def _partition_pr_task_summaries(
    task_summaries: list[dict],
) -> tuple[list[dict], list[dict] | None]:
    """Split task summaries into completed tasks and terminal failed tasks for PR creation."""
    done_tasks = [t for t in task_summaries if t.get("state") == TaskState.DONE.value]
    failed_tasks = [t for t in task_summaries if t.get("state") in _FAILED_PR_TASK_STATES]
    return done_tasks, failed_tasks or None


def _is_multi_repo_configs(repos: list) -> bool:
    """Return True when the current app repo config represents a multi-repo workspace."""
    return len(repos) > 1 or (len(repos) == 1 and getattr(repos[0], "id", "default") != "default")


def _build_pipeline_repos_json(repos: list) -> str | None:
    """Serialize multi-repo config for DB persistence."""
    if not _is_multi_repo_configs(repos):
        return None

    data: list[dict[str, str]] = []
    for repo in repos:
        data.append(
            {
                "id": repo.id,
                "path": os.path.realpath(repo.path),
                "base_branch": repo.base_branch,
                "branch_name": "",
            }
        )
    return json.dumps(data)


def _resolve_multi_repo_pr_targets(
    *,
    repos_list: list[dict],
    current_repos: list,
    workspace_dir: str,
    fallback_base_branch: str,
    fallback_branch: str,
) -> tuple[dict[str, dict], dict[str, str], list[dict]]:
    """Resolve per-repo PR targets from persisted pipeline data and live repo config."""
    repo_cfg_by_id = {repo.id: repo for repo in current_repos}

    normalized_entries = list(repos_list)
    if len(normalized_entries) <= 1 and _is_multi_repo_configs(current_repos):
        normalized_entries = json.loads(_build_pipeline_repos_json(current_repos) or "[]")

    repos: dict[str, dict] = {}
    pipeline_branches: dict[str, str] = {}
    resolved_entries: list[dict] = []

    for entry in normalized_entries:
        repo_id = entry.get("id") or entry.get("repo_id", "default")
        repo_cfg = repo_cfg_by_id.get(repo_id)

        raw_path = entry.get("path") or entry.get("project_dir") or getattr(repo_cfg, "path", "")
        if not raw_path:
            raise ValueError(f"Missing path for repo '{repo_id}'")
        repo_path = (
            os.path.realpath(os.path.join(workspace_dir, raw_path))
            if not os.path.isabs(raw_path)
            else os.path.realpath(raw_path)
        )
        if not os.path.exists(os.path.join(repo_path, ".git")):
            raise ValueError(f"Repo '{repo_id}' path is not a git checkout: {repo_path}")

        base_branch = (
            entry.get("base_branch") or getattr(repo_cfg, "base_branch", "") or fallback_base_branch
        )
        branch_name = entry.get("branch_name") or entry.get("branch") or fallback_branch

        repos[repo_id] = {
            "project_dir": repo_path,
            "base_branch": base_branch,
        }
        pipeline_branches[repo_id] = branch_name

        resolved_entry = dict(entry)
        resolved_entry["id"] = repo_id
        resolved_entry["path"] = repo_path
        resolved_entry["base_branch"] = base_branch
        resolved_entry["branch_name"] = branch_name
        resolved_entries.append(resolved_entry)

    return repos, pipeline_branches, resolved_entries


async def detect_server(base_url: str = "http://localhost:8000", timeout: float = 0.1) -> bool:
    """Probe the Forge server health endpoint."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}/health")
            return resp.status_code == 200
    except Exception:
        return False


class ForgeApp(App):
    """Forge Terminal UI."""

    TITLE = "Forge"
    CSS = _APP_CSS

    BINDINGS = [
        Binding("1", "switch_home", "Home", show=True),
        Binding("2", "switch_pipeline", "Pipeline", show=True),
        Binding("3", "switch_review", "Review", show=True),
        Binding("4", "switch_settings", "Settings", show=True),
        Binding("5", "switch_stats", "Stats", show=True),
        Binding("q", "quit_app", "Quit"),
        Binding("s", "screenshot_export", "Screenshot", show=False),
        Binding("tab", "cycle_questions", "Next", show=False, priority=True),
        Binding("question_mark", "show_help", "Help", show=False),
        Binding("ctrl+p", "show_command_palette", "Command Palette", show=False),
        # Block dangerous system shortcuts that can cause unrecoverable state
        # (e.g. Cmd+K sending unintended messages during execution).
        # Binding("ctrl+k", "noop", show=False, priority=True),
        # Binding("ctrl+l", "noop", show=False, priority=True),
    ]

    def __init__(
        self,
        project_dir: str = ".",
        settings: object | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._project_dir = os.path.abspath(project_dir)
        self._settings = settings
        self._bus = EventBus()
        self._state = TuiState()
        self._source: EmbeddedSource | None = None
        self._daemon = None
        self._daemon_task: asyncio.Task | None = None
        self._pipeline_start_time: float | None = None
        self._elapsed_timer = None
        from forge.core.paths import forge_db_path

        self._db_path = forge_db_path()
        self._db = None
        self._repos: list = []
        self._graph = None
        self._pipeline_id = None
        self._final_approval_pushed = False
        self._cached_pipeline_branch: str = ""
        self._cached_base_branch: str = "main"
        self._force_quit: bool = False
        self._dry_run_mode: bool = False

    async def _init_db(self):
        """Initialize database connection."""
        from forge.core.paths import forge_db_url
        from forge.storage.db import Database

        try:
            self._db = Database(forge_db_url())
            await self._db.initialize()
        except Exception as e:
            logger.error("Failed to initialize database: %s", e, exc_info=True)
            self.notify(f"Database initialization failed: {_escape_markup(e)}", severity="error")
            self._db = None

    def _resolve_repos(self) -> list:
        """Resolve repos for HomeScreen display."""
        try:
            from forge.config.project_config import resolve_repos

            return resolve_repos(repo_flags=(), project_dir=self._project_dir)
        except Exception:
            logger.debug("Failed to resolve repos", exc_info=True)
            return []

    async def _load_recent_pipelines(self) -> list[dict]:
        """Load recent pipelines from DB for HomeScreen, enriched with task counts."""
        if not self._db:
            return []
        try:
            rows = await self._db.get_pipeline_list_with_counts(limit=10)
            recent: list[dict] = []
            for r in rows:
                pr_url = r.get("pr_url")
                pr_count, repo_count = _recent_pipeline_pr_counts(r)
                try:
                    pr_events = await self._db.list_events(
                        r["id"], event_type="pipeline:pr_created"
                    )
                except Exception:
                    pr_events = []
                if pr_events:
                    event_urls: list[str] = []
                    event_repo_ids: set[str] = set()
                    for evt in pr_events:
                        payload = evt.payload or {}
                        if not isinstance(payload, dict):
                            continue
                        url = payload.get("pr_url")
                        repo_id = payload.get("repo_id")
                        if url:
                            event_urls.append(url)
                        if repo_id:
                            event_repo_ids.add(repo_id)
                    unique_urls = list(dict.fromkeys(event_urls))
                    event_pr_count = len(event_repo_ids) if event_repo_ids else len(unique_urls)
                    if event_pr_count:
                        pr_count = max(pr_count, event_pr_count)
                        if repo_count == 1 and event_pr_count > 1:
                            repo_count = event_pr_count
                        if not pr_url and len(unique_urls) == 1:
                            pr_url = unique_urls[0]
                recent.append(
                    {
                        "id": r["id"],
                        "description": r.get("description") or "",
                        "status": r.get("status") or "unknown",
                        "created_at": r.get("created_at") or "",
                        "cost": r.get("cost") or 0.0,
                        "total_cost_usd": r.get("cost") or 0.0,
                        "project_dir": r.get("project_dir") or "",
                        "total_tasks": r.get("total_tasks", 0),
                        "tasks_done": r.get("tasks_done", 0),
                        "tasks_error": r.get("tasks_error", 0),
                        "pr_url": pr_url,
                        "pr_count": pr_count,
                        "repo_count": repo_count,
                    }
                )
            return recent
        except Exception:
            logger.debug("Failed to load pipeline history", exc_info=True)
            return []

    async def on_mount(self) -> None:
        """Initialize DB, push home screen, wire state changes."""
        await self._init_db()
        # Mount the command palette overlay at the app level so it's available on all screens
        # Extend default command palette actions with Stats screen entry
        _stats_action = CommandPaletteAction(
            name="Stats",
            description="View pipeline stats and metrics",
            shortcut="5",
            category="Navigation",
            callback_name="switch_stats",
        )
        palette_actions = get_all_actions()
        # Insert after Settings (index 3) in Navigation category
        palette_actions.insert(4, _stats_action)
        await self.mount(CommandPalette(actions=palette_actions))
        recent = await self._load_recent_pipelines()
        self._repos = self._resolve_repos()
        self.push_screen(
            HomeScreen(recent_pipelines=recent, repos=self._repos, project_dir=self._project_dir)
        )
        self._state_cb = self._on_state_change
        self._state.on_change(self._state_cb)

    async def on_unmount(self) -> None:
        """Clean up state change callback to prevent leaks."""
        self._state.remove_change_callback(self._state_cb)

    def _replace_state(self, state: TuiState) -> None:
        """Swap in a new TUI state while preserving app-level change callbacks."""
        callback = getattr(self, "_state_cb", None)
        current = getattr(self, "_state", None)
        if current is state:
            return
        if current is not None and callback is not None:
            current.remove_change_callback(callback)
        self._state = state
        if callback is not None:
            self._state.on_change(callback)

    def _queue_state_event(self, event_type: str, data: dict) -> None:
        """Queue state application onto the Textual UI loop.

        Embedded daemon tasks can emit events very quickly. Applying state
        synchronously inside the event callback starves the UI repaint loop
        and makes output appear in a burst at the end. Queueing the mutation
        back onto Textual's message pump keeps planner/agent/review streaming live.
        """
        try:
            self.call_next(self._state.apply_event, event_type, data)
        except Exception:
            self._state.apply_event(event_type, data)

    # Fields that change frequently and don't need a full screen refresh
    _HIGH_FREQ_FIELDS = frozenset(
        {"agent_output", "review_output", "cost", "elapsed", "followup_output"}
    )

    def _on_state_change(self, field: str) -> None:
        """Refresh current screen and auto-capture screenshots."""
        try:
            if field in self._HIGH_FREQ_FIELDS:
                self.call_after_refresh(self.screen.refresh)
            else:
                self.screen.refresh()
        except Exception:
            pass
        # Auto-capture at key moments
        if field == "phase":
            phase = self._state.phase
            if phase in ("planning", "executing", "complete"):
                self._auto_screenshot(phase)
            # Transition to FinalApprovalScreen when pipeline finishes
            if phase == "final_approval" and not self._final_approval_pushed:
                self._final_approval_pushed = True
                self._push_final_approval()
            elif phase == "partial_success" and not self._final_approval_pushed:
                self._final_approval_pushed = True
                self._push_final_approval(partial=True)

    def _auto_screenshot(self, label: str) -> None:
        """Automatically save a screenshot for README."""
        path = os.path.join(self._project_dir, "screenshots")
        os.makedirs(path, exist_ok=True)
        filename = os.path.join(path, f"forge-{label}.svg")
        try:
            self.save_screenshot(filename)
            logger.info("Auto-screenshot: %s", filename)
        except Exception:
            logger.debug("Auto-screenshot failed for %s", label)

    def _push_final_approval(self, partial: bool = False) -> None:
        """Build stats/tasks summary and push FinalApprovalScreen."""
        # Alert user the pipeline is done
        import sys

        sys.stdout.write("\a")  # terminal bell
        sys.stdout.flush()

        state = self._state
        # TUI notification banner
        status = "partially completed" if partial else "completed"
        self.notify(f"Pipeline {status}!", severity="information", timeout=10)
        tasks_list = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        total_questions = sum(len(v) for v in state.question_history.values())
        elapsed_secs = int(state.elapsed_seconds)
        elapsed_str = f"{elapsed_secs // 60}m {elapsed_secs % 60}s"

        # Aggregate diff stats from merge results (keys are camelCase from _get_diff_stats)
        total_added = 0
        total_removed = 0
        total_files = 0
        for t in tasks_list:
            mr = t.get("merge_result", {})
            if mr and mr.get("success"):
                total_added += mr.get("linesAdded", 0)
                total_removed += mr.get("linesRemoved", 0)
                total_files += mr.get("filesChanged", 0)

        # Calculate additional stats
        total_retries = sum(len(v) for v in state.error_history.values())
        blocked_count = sum(1 for t in tasks_list if t.get("state") == "blocked")
        skipped_count = sum(1 for t in tasks_list if t.get("state") == "cancelled")

        stats: dict = {
            "added": total_added,
            "removed": total_removed,
            "files": total_files,
            "elapsed": elapsed_str,
            "cost": state.total_cost_usd,
            "questions": total_questions,
            "total_retries": total_retries,
            "blocked_count": blocked_count,
            "skipped_count": skipped_count,
        }
        if state.is_multi_repo:
            stats["repo_count"] = len(state.repos)
            stats["task_count"] = len(tasks_list)
        task_summaries = _build_task_summaries(
            tasks_list,
            error_history=state.error_history,
            review_substatus=state.review_substatus,
            merge_substatus=state.merge_substatus,
            review_gates=state.review_gates,
        )
        # Get pipeline branch for diff viewing — use state cached value or
        # schedule async DB lookup (sync context, cannot await).
        pipeline_branch = self._cached_pipeline_branch or ""
        base_branch = self._cached_base_branch or "main"
        self.push_screen(
            FinalApprovalScreen(
                stats=stats,
                tasks=task_summaries,
                pipeline_branch=pipeline_branch,
                base_branch=base_branch,
                partial=partial,
                multi_repo=state.is_multi_repo,
                per_repo_pr_urls=dict(state.per_repo_pr_urls),
                repos=list(state.repos),
            )
        )
        # If no cached branch, fetch async and update the screen
        if not pipeline_branch:
            safe_create_task(self._resolve_pipeline_branch(), logger=logger, name="resolve-branch")

    async def _resolve_pipeline_branch(self) -> None:
        """Fetch pipeline branch from DB and update the FinalApprovalScreen."""
        branch = await self._get_pipeline_branch()
        if branch:
            self._cached_pipeline_branch = branch
            try:
                screen = self.screen
                if isinstance(screen, FinalApprovalScreen):
                    screen.show_pipeline_target(branch, self._cached_base_branch)
            except Exception:
                pass

    async def on_chat_thread_answer_submitted(self, event) -> None:
        """Write the user's answer to DB and update TUI state."""
        task_id = event.task_id
        answer = event.answer
        if not self._db or not self._pipeline_id:
            logger.warning("Cannot record answer: DB or pipeline_id not set")
            return

        emitter = getattr(self._daemon, "_events", None) if self._daemon else None

        def _has_live_handler(event_type: str) -> bool:
            if emitter is None:
                return False
            handlers = getattr(emitter, "_handlers", None)
            if isinstance(handlers, dict):
                return bool(handlers.get(event_type))
            return hasattr(emitter, "emit")

        # Planning questions use __planning__ sentinel
        is_planning = task_id == "__planning__"
        question_id: str | None = None

        try:
            if is_planning:
                current = self._state.pending_questions.get("__planning__", {})
                question_id = current.get("question_id")
                if question_id:
                    await self._db.answer_question(question_id, answer, "human")
                else:
                    pending = await self._db.get_pending_questions(self._pipeline_id)
                    for q in pending:
                        if q.task_id == task_id and q.answer is None:
                            question_id = q.id
                            await self._db.answer_question(q.id, answer, "human")
                            break
                if not question_id:
                    self.notify("That planning question is no longer pending.", severity="warning")
                    return
            else:
                pending = await self._db.get_pending_questions(self._pipeline_id)
                for q in pending:
                    if q.task_id == task_id and q.answer is None:
                        question_id = q.id
                        await self._db.answer_question(q.id, answer, "human")
                        break
                if not question_id:
                    self.notify("That task question is no longer pending.", severity="warning")
                    return
        except Exception as e:
            logger.error("Failed to record answer to DB", exc_info=True)
            self.notify(f"Failed to save answer: {_escape_markup(e)}", severity="error")
            return

        if is_planning:
            signaled_live_planner = False
            if _has_live_handler("planning:answer"):
                try:
                    await emitter.emit(
                        "planning:answer",
                        {
                            "question_id": question_id,
                            "answer": answer,
                        },
                    )
                    signaled_live_planner = True
                except Exception:
                    logger.error("Failed to emit planning:answer to daemon", exc_info=True)
            self._state.apply_event("planning:answer", {"answer": answer})
            if not signaled_live_planner:
                self.notify(
                    "Answer saved, but the live planner is no longer attached. Restart planning to continue.",
                    severity="warning",
                    timeout=8,
                )
        else:
            # Notify daemon to resume the task
            signaled_live_agent = False
            if _has_live_handler("task:answer"):
                try:
                    await emitter.emit(
                        "task:answer",
                        {
                            "task_id": task_id,
                            "answer": answer,
                            "pipeline_id": self._pipeline_id,
                        },
                    )
                    signaled_live_agent = True
                except Exception:
                    logger.error("Failed to emit task:answer to daemon", exc_info=True)
            if signaled_live_agent:
                self._state.apply_event("task:answer", {"task_id": task_id, "answer": answer})
            else:
                q = self._state.pending_questions.pop(task_id, None)
                if q:
                    history = self._state.question_history.setdefault(task_id, [])
                    history.append({"question": q, "answer": answer})
                self._state._notify("tasks")
                self.notify(
                    "Answer saved, but the live agent is not attached right now. Resume the pipeline if it stays paused.",
                    severity="warning",
                    timeout=8,
                )

    async def _restart_planning_from_scratch(
        self,
        *,
        description: str,
        base_branch: str,
        branch_name: str = "",
        project_dir: str | None = None,
        notify_message: str,
    ) -> None:
        """Start a fresh planning run when the previous planner session is unrecoverable."""
        if self._source:
            try:
                self._source.disconnect()
            except Exception:
                logger.debug("Failed to disconnect old event source", exc_info=True)
        self._source = None
        self._bus = EventBus()
        self._daemon = None
        self._daemon_task = None
        self._graph = None
        self._pipeline_id = None
        self._final_approval_pushed = False
        self._cached_pipeline_branch = ""
        self._cached_base_branch = base_branch or "main"

        if project_dir:
            self._project_dir = project_dir
            self._repos = self._resolve_repos()

        fresh_state = TuiState()
        fresh_state.base_branch = base_branch or "main"
        fresh_state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        self._replace_state(fresh_state)
        self.push_screen(PipelineScreen(self._state))
        self._daemon_task = asyncio.create_task(
            self._run_plan(
                description,
                base_branch=base_branch or "main",
                branch_name=branch_name or "",
            )
        )
        self._daemon_task.add_done_callback(self._on_daemon_done)
        self.notify(notify_message, severity="information")

    async def respond_to_integration_prompt(self, action: str) -> None:
        """Handle user response to an integration health check prompt.

        Emits the appropriate event so the daemon can resume.
        """
        if not self._daemon or not hasattr(self._daemon, "_events"):
            return

        prompt = self._state.integration_prompt
        if not prompt:
            return

        prompt_type = prompt.get("type", "post_merge")
        if prompt_type == "baseline":
            event_type = "integration:baseline_response"
        else:
            event_type = "integration:check_response"

        try:
            await self._daemon._events.emit(event_type, {"action": action})
        except Exception:
            logger.error("Failed to emit %s to daemon", event_type, exc_info=True)

        # Update local state immediately
        self._state.apply_event(event_type, {"action": action})

    async def on_chat_thread_interjection_submitted(self, event) -> None:
        """Create an interjection record for a running agent."""
        task_id = event.task_id
        message = event.message
        if not self._db or not self._pipeline_id:
            logger.warning("Cannot create interjection: DB or pipeline_id not set")
            return
        try:
            await self._db.create_interjection(
                task_id=task_id,
                pipeline_id=self._pipeline_id,
                message=message,
            )
            self._state.apply_event(
                "task:interjection",
                {
                    "task_id": task_id,
                    "message": message,
                },
            )
        except Exception:
            logger.error("Failed to create interjection", exc_info=True)

    async def on_final_approval_screen_create_pr(self, event) -> None:
        """User confirmed PR creation from FinalApprovalScreen."""
        from forge.tui.pr_creator import (
            auto_format_branch,
            create_pr,
            create_prs_multi_repo,
            generate_pr_body,
            push_branch,
        )

        self._state.apply_event("pipeline:pr_creating", {})

        # Use the pipeline branch stored in DB — NOT the user's current HEAD.
        # The daemon creates an isolated branch (e.g. forge/add-feature) and merges
        # task work into it.  _get_current_branch() returns the user's checkout
        # (usually main), which is wrong.
        branch = await self._get_pipeline_branch()
        if not branch:
            self._state.apply_event(
                "pipeline:pr_failed", {"error": "Could not determine pipeline branch"}
            )
            self.notify("PR creation failed: no pipeline branch found.", severity="error")
            return
        project_dir = self._project_dir

        # Build question history list for PR body
        all_questions: list[dict] = []
        for qlist in self._state.question_history.values():
            for qa in qlist:
                q_text = qa.get("question", {})
                if isinstance(q_text, dict):
                    q_text = q_text.get("question", "")
                all_questions.append({"question": q_text, "answer": qa.get("answer", "")})

        state = self._state
        elapsed_secs = int(state.elapsed_seconds)
        elapsed_str = f"{elapsed_secs // 60}m {elapsed_secs % 60}s"
        raw_tasks = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        task_summaries = _build_task_summaries(raw_tasks)
        done_tasks, failed_tasks = _partition_pr_task_summaries(task_summaries)

        # Determine the base branch for the PR target and detect multi-repo
        base_branch = "main"
        repos_list: list[dict] = []
        if self._db and self._pipeline_id:
            try:
                pipeline = await self._db.get_pipeline(self._pipeline_id)
                base_branch = getattr(pipeline, "base_branch", None) or "main"
                if pipeline:
                    repos_list = pipeline.get_repos()
            except Exception:
                pass

        # ── Multi-repo PR creation ──────────────────────────────────────
        if len(repos_list) > 1 or _is_multi_repo_configs(self._repos):
            try:
                repos, pipeline_branches, repos_list = _resolve_multi_repo_pr_targets(
                    repos_list=repos_list,
                    current_repos=self._repos,
                    workspace_dir=project_dir,
                    fallback_base_branch=base_branch,
                    fallback_branch=branch,
                )

                result = await create_prs_multi_repo(
                    task_summaries=task_summaries,
                    repos=repos,
                    pipeline_branches=pipeline_branches,
                    description=self._pipeline_description(),
                    elapsed_str=elapsed_str,
                    questions=all_questions,
                    failed_tasks=failed_tasks,
                )

                # Update repos_json with PR URLs
                for entry in repos_list:
                    rid = entry.get("id") or entry.get("repo_id", "default")
                    if rid in result.pr_urls:
                        entry["pr_url"] = result.pr_urls[rid]
                if self._db and self._pipeline_id:
                    try:
                        await self._db.update_pipeline_repos_json(
                            self._pipeline_id,
                            json.dumps(repos_list),
                        )
                    except Exception:
                        logger.warning("Failed to update repos_json with PR URLs", exc_info=True)

                # Show warnings for any failures
                for rid, err in result.failures.items():
                    self.notify(f"PR failed for {rid}: {err}", severity="warning")

                if result.pr_urls:
                    # Emit per-repo pr_created events with repo_id
                    for rid, url in result.pr_urls.items():
                        self._state.apply_event(
                            "pipeline:pr_created", {"pr_url": url, "repo_id": rid}
                        )
                        # Show PR URL on the FinalApprovalScreen per-repo
                        try:
                            screen = self.screen
                            if isinstance(screen, FinalApprovalScreen):
                                screen.show_pr_url(url, repo_id=rid)
                        except Exception:
                            pass
                    # Show all URLs in notification
                    url_lines = ", ".join(f"{rid}: {url}" for rid, url in result.pr_urls.items())
                    self.notify(f"PRs created: {url_lines}", severity="information")
                    # Start CI auto-fix for each repo's PR
                    from forge.tui.pr_creator import maybe_start_ci_fix

                    for rid, url in result.pr_urls.items():
                        repo_dir = repos[rid]["project_dir"]
                        repo_base = repos[rid].get("base_branch", "main")
                        repo_branch = pipeline_branches.get(rid, branch)
                        await maybe_start_ci_fix(
                            pr_url=url,
                            project_dir=repo_dir,
                            branch=repo_branch,
                            base_branch=repo_base,
                            pipeline_id=self._pipeline_id or "",
                            db=self._db,
                        )
                else:
                    self._state.apply_event(
                        "pipeline:pr_failed", {"error": "All repo PR creations failed"}
                    )
                    if self._db and self._pipeline_id:
                        try:
                            await self._db.update_pipeline_status(self._pipeline_id, "error")
                        except Exception:
                            pass
                    self.notify("PR creation failed for all repos.", severity="error")
            except Exception as e:
                logger.error("Multi-repo PR creation error: %s", e, exc_info=True)
                self._state.apply_event("pipeline:pr_failed", {"error": str(e)})
                if self._db and self._pipeline_id:
                    try:
                        await self._db.update_pipeline_status(self._pipeline_id, "error")
                    except Exception:
                        pass
                self.notify(f"PR creation error: {_escape_markup(e)}", severity="error")
            return

        # ── Single-repo PR creation (existing path) ────────────────────
        try:
            # Auto-format the branch before pushing (non-fatal if it fails)
            await auto_format_branch(project_dir, branch)

            pushed = await push_branch(project_dir, branch)
            if not pushed:
                self._state.apply_event("pipeline:pr_failed", {"error": "git push failed"})
                if self._db and self._pipeline_id:
                    try:
                        await self._db.update_pipeline_status(self._pipeline_id, "error")
                    except Exception:
                        pass
                self.notify("PR creation failed: could not push branch.", severity="error")
                return

            body = generate_pr_body(
                tasks=done_tasks,
                failed_tasks=failed_tasks,
                time=elapsed_str,
                cost=state.total_cost_usd,
                questions=all_questions,
            )
            pr_url = await create_pr(
                project_dir,
                title=f"Forge: {self._pipeline_description()}",
                body=body,
                base=base_branch,
                head=branch,
            )
            if pr_url:
                self._state.apply_event("pipeline:pr_created", {"pr_url": pr_url})
                if self._db and self._pipeline_id:
                    try:
                        await self._db.set_pipeline_pr_url(self._pipeline_id, pr_url)
                    except Exception:
                        logger.warning("Failed to persist pipeline PR URL", exc_info=True)
                # Show PR URL inline on the FinalApprovalScreen
                try:
                    screen = self.screen
                    if isinstance(screen, FinalApprovalScreen):
                        screen.show_pr_url(pr_url)
                except Exception:
                    self.notify(f"PR created: {pr_url}", severity="information")
                # Start CI auto-fix if enabled
                from forge.tui.pr_creator import maybe_start_ci_fix

                await maybe_start_ci_fix(
                    pr_url=pr_url,
                    project_dir=project_dir,
                    branch=branch,
                    base_branch=base_branch,
                    pipeline_id=self._pipeline_id or "",
                    db=self._db,
                )
            else:
                self._state.apply_event("pipeline:pr_failed", {"error": "gh pr create failed"})
                if self._db and self._pipeline_id:
                    try:
                        await self._db.update_pipeline_status(self._pipeline_id, "error")
                    except Exception:
                        pass
                self.notify("PR creation failed: check logs.", severity="error")
        except Exception as e:
            logger.error("PR creation error: %s", e, exc_info=True)
            self._state.apply_event("pipeline:pr_failed", {"error": str(e)})
            if self._db and self._pipeline_id:
                try:
                    await self._db.update_pipeline_status(self._pipeline_id, "error")
                except Exception:
                    pass
            self.notify(f"PR creation error: {_escape_markup(e)}", severity="error")

    async def on_final_approval_screen_follow_up(self, event) -> None:
        """User submitted a follow-up prompt from FinalApprovalScreen."""
        if not self._db or not self._pipeline_id:
            self.notify("No pipeline context for follow-up.", severity="error")
            return
        if not self._daemon or not self._graph:
            self.notify(
                "Cannot start follow-up: the live pipeline is no longer attached. Resume it from history first.",
                severity="error",
            )
            return

        prompt = event.prompt.strip()
        if not prompt:
            return

        try:
            tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
            followup_n = sum(1 for t in tasks if "-followup-" in t.id) + 1
            prefix = self._pipeline_id[:8]
            task_id = f"{prefix}-followup-{followup_n}"

            done_ids = [t.id for t in tasks if t.state == "done"]

            await self._db.create_task(
                id=task_id,
                title=prompt[:80],
                description=prompt,
                files=[],
                depends_on=done_ids,
                complexity="medium",
                pipeline_id=self._pipeline_id,
            )
            try:
                await self._db.update_pipeline_status(self._pipeline_id, "executing")
            except Exception:
                logger.warning("Failed to persist executing status for follow-up", exc_info=True)
        except Exception as e:
            logger.error("Failed to queue follow-up task", exc_info=True)
            self.notify(f"Failed to queue follow-up: {_escape_markup(e)}", severity="error")
            return

        self._state.tasks[task_id] = {
            "id": task_id,
            "title": prompt[:80],
            "description": prompt,
            "files": [],
            "depends_on": done_ids,
            "complexity": "medium",
            "state": "todo",
            "agent_cost": 0.0,
            "error": None,
            "repo": None,
        }
        if task_id not in self._state.task_order:
            self._state.task_order.append(task_id)
        self._state.selected_task_id = task_id
        self._state.error = None
        self._state._notify("tasks")
        self._state._notify("error")
        self._state.phase = "executing"
        self._state._notify("phase")
        self._final_approval_pushed = False

        while len(self.screen_stack) > 2:
            self.pop_screen()

        await self._resume_execution()
        self.notify("Follow-up queued and execution resumed.", severity="information")

    async def _reset_failed_tasks_for_retry(self, pipeline_id: str) -> int:
        """Reset failed tasks so the pipeline can resume on the same branch."""
        if not self._db:
            return 0

        tasks = await self._db.list_tasks_by_pipeline(pipeline_id)
        reset_ids: list[str] = []
        for task in tasks:
            if task.state == "error":
                await self._db.reset_task_for_human_retry(task.id)
                reset_ids.append(task.id)
            elif task.state == "blocked":
                await self._db.update_task_state(task.id, "todo")
                reset_ids.append(task.id)

        if reset_ids:
            for task_id in reset_ids:
                if task_id in self._state.tasks:
                    self._state.tasks[task_id]["state"] = "todo"
                    self._state.tasks[task_id].pop("error", None)
            self._state._notify("tasks")
        return len(reset_ids)

    async def _reset_interrupted_tasks_for_resume(self, pipeline_id: str) -> int:
        """Reset orphaned live tasks before resuming a dead local pipeline."""
        if not self._db:
            return 0

        tasks = await self._db.list_tasks_by_pipeline(pipeline_id)
        reset_ids: list[str] = []
        for task in tasks:
            if task.state in ("in_progress", "in_review", "merging", "cancelled"):
                await self._db.reset_task_for_resume(task.id)
                reset_ids.append(task.id)

        if reset_ids:
            for task_id in reset_ids:
                if task_id in self._state.tasks:
                    self._state.tasks[task_id]["state"] = "todo"
                    self._state.tasks[task_id].pop("error", None)
            self._state._notify("tasks")
        return len(reset_ids)

    async def _retry_failed_pipeline(self, *, push_pipeline_screen: bool = False) -> bool:
        """Retry failed tasks and re-enter normal pipeline execution."""
        if not self._db or not self._pipeline_id:
            self.notify("Cannot retry: missing pipeline context.", severity="error")
            return False
        if not self._daemon or not self._graph:
            self.notify("Cannot retry: missing execution context.", severity="error")
            return False

        reset_count = await self._reset_failed_tasks_for_retry(self._pipeline_id)
        if reset_count == 0:
            self.notify("No failed tasks to retry.", severity="warning")
            return False

        await self._db.update_pipeline_status(self._pipeline_id, "retrying")
        self._state.phase = "retrying"
        self._state._notify("phase")
        self._final_approval_pushed = False

        if push_pipeline_screen:
            self.push_screen(PipelineScreen(self._state))
        else:
            while len(self.screen_stack) > 2:
                self.pop_screen()

        await self._resume_execution()
        self.notify(
            f"Retrying {reset_count} failed task{'s' if reset_count != 1 else ''}.",
            severity="information",
        )
        return True

    async def on_final_approval_screen_rerun(self, event) -> None:
        """User wants to retry failed tasks."""
        try:
            await self._retry_failed_pipeline(push_pipeline_screen=False)
        except Exception as e:
            logger.error("Failed to rerun pipeline from final approval", exc_info=True)
            self.notify(f"Failed to rerun pipeline: {_escape_markup(e)}", severity="error")

    async def on_final_approval_screen_skip_failed(self, event) -> None:
        """User wants to skip failed tasks and finish."""
        if not self._db or not self._pipeline_id:
            return

        try:
            tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
            cancelled_ids: list[str] = []
            for t in tasks:
                if t.state in ("error", "blocked"):
                    await self._db.update_task_state(t.id, "cancelled")
                    cancelled_ids.append(t.id)
            await self._db.update_pipeline_status(self._pipeline_id, "complete")
        except Exception as e:
            logger.error("Failed to skip failed tasks", exc_info=True)
            self.notify(f"Failed to finish pipeline: {_escape_markup(e)}", severity="error")
            return

        for task_id in cancelled_ids:
            if task_id in self._state.tasks:
                self._state.tasks[task_id]["state"] = "cancelled"
                self._state.tasks[task_id].pop("error", None)
                self._state.pending_questions.pop(task_id, None)
        self._state.error = None
        self._state._notify("tasks")
        self._state._notify("error")
        self._state.phase = "final_approval"
        self._state._notify("phase")

        self._final_approval_pushed = False
        while len(self.screen_stack) > 2:
            self.pop_screen()
        self._push_final_approval()
        if cancelled_ids:
            self.notify(
                f"Skipped {len(cancelled_ids)} failed task{'s' if len(cancelled_ids) != 1 else ''}.",
                severity="information",
            )

    async def _resume_execution(self) -> None:
        """Re-enter the daemon execution loop for remaining TODO tasks."""
        if not self._daemon or not self._graph or not self._db:
            self.notify("Cannot resume: missing context.", severity="error")
            return

        self._daemon_task = safe_create_task(
            self._daemon.execute(self._graph, self._db, pipeline_id=self._pipeline_id, resume=True),
            logger=logger,
            name="resume-execution",
        )
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def _get_pipeline_branch(self) -> str | None:
        """Return the pipeline branch name from DB (the branch where task work was merged)."""
        if self._db and self._pipeline_id:
            try:
                pipeline = await self._db.get_pipeline(self._pipeline_id)
                branch = getattr(pipeline, "branch_name", None) if pipeline else None
                if branch:
                    return branch
            except Exception:
                logger.debug("Could not read pipeline branch from DB", exc_info=True)
        # Fallback: detect from git (only correct if user is on the pipeline branch)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                cwd=self._project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                name = stdout.decode().strip()
                if name != "main" and name != "master":
                    return name
        except Exception:
            logger.debug("Could not detect git branch", exc_info=True)
        return None

    def _pipeline_description(self) -> str:
        """Return a short description for the PR title."""
        # Use the pipeline description stored in state if available
        if self._state.tasks:
            first = next(iter(self._state.task_order), None)
            if first and first in self._state.tasks:
                return self._state.tasks[first].get("title", "automated pipeline")
        return "automated pipeline"

    def action_cycle_questions(self) -> None:
        """Tab: cycle through tasks with pending questions."""
        state = self._state
        pending_task_ids = [
            task_id
            for task_id in state.pending_questions
            if task_id != "__planning__" or state.phase in ("planning", "planned")
        ]
        if not pending_task_ids:
            return
        current = state.selected_task_id
        if current in pending_task_ids:
            idx = (pending_task_ids.index(current) + 1) % len(pending_task_ids)
        else:
            idx = 0
        state.selected_task_id = pending_task_ids[idx]
        state._notify("tasks")

    def action_show_help(self) -> None:
        """?: show a brief help notification."""
        self.notify(
            "1-4: screens | j/k: tasks | o/c/d/r: views | Tab: next question | Ctrl+P: palette | q: quit",
            title="Forge Keybindings",
            timeout=8,
        )

    def action_show_command_palette(self) -> None:
        """Ctrl+P: toggle the command palette overlay."""
        try:
            palette = self.query_one(CommandPalette)
            if palette.is_open:
                palette.close()
            else:
                palette.open()
        except Exception:
            logger.debug("Command palette not mounted", exc_info=True)

    async def on_command_palette_action_selected(
        self, event: CommandPalette.ActionSelected
    ) -> None:
        """Execute the action selected from the command palette."""
        action = event.action
        callback_name = action.callback_name
        if not callback_name:
            return
        method_name = f"action_{callback_name}"
        method = getattr(self, method_name, None)
        if method is None:
            # Try screen-level action
            try:
                method = getattr(self.screen, method_name, None)
            except Exception:
                pass
        if method:
            try:
                import inspect

                if inspect.iscoroutinefunction(method):
                    await method()
                else:
                    method()
            except Exception as e:
                logger.error(
                    "Command palette action %s failed: %s", callback_name, e, exc_info=True
                )
                self.notify(f"Action failed: {_escape_markup(e)}", severity="error")
        else:
            self.notify(f"Action '{action.name}' not available", severity="warning")

    async def on_home_screen_task_submitted(self, event: HomeScreen.TaskSubmitted) -> None:
        """User submitted a task from HomeScreen."""
        # Guard against double-submit
        if self._daemon_task and not self._daemon_task.done():
            self.notify("A pipeline is already running", severity="warning")
            return
        task = event.task
        base_branch = getattr(event, "base_branch", "main") or "main"
        branch_name = getattr(event, "branch_name", "") or ""
        per_repo = getattr(event, "per_repo_base_branches", None)

        # Apply per-repo base branch overrides from the selectors
        if per_repo and self._repos:
            updated: list = []
            for rc in self._repos:
                override = per_repo.get(rc.id)
                if override and override != rc.base_branch:
                    updated.append(_dc_replace(rc, base_branch=override))
                else:
                    updated.append(rc)
            self._repos = updated

        logger.info("Task submitted: %s (base: %s, branch: %s)", task, base_branch, branch_name)
        self._state.base_branch = base_branch
        self._state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        pipeline_screen = PipelineScreen(self._state)
        self.push_screen(pipeline_screen)
        # CRITICAL: Use create_task, NOT await — planning is a long LLM call
        # that would block the Textual event loop and freeze the UI.
        # Store as _daemon_task so quit handler can cancel it gracefully.
        self._daemon_task = asyncio.create_task(
            self._run_plan(task, base_branch=base_branch, branch_name=branch_name)
        )
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def _run_plan(self, task: str, base_branch: str = "main", branch_name: str = "") -> None:
        """Run planning phase only, then show plan for approval."""
        import uuid

        from forge.config.project_config import ProjectConfig, apply_project_config
        from forge.config.settings import ForgeSettings
        from forge.core.daemon import ForgeDaemon
        from forge.core.events import EventEmitter
        from forge.core.preflight import run_preflight
        from forge.core.provider_config import (
            build_provider_config_snapshot,
            build_provider_registry,
            resolve_pipeline_models,
        )

        settings = self._settings or ForgeSettings()
        project_config = ProjectConfig.load(self._project_dir)
        apply_project_config(settings, project_config)
        registry = build_provider_registry(settings, project_config)
        config_issues = project_config.validate(registry)
        if config_issues:
            self._state.apply_event(
                "pipeline:error",
                {"error": "Project config invalid:\n" + "\n".join(config_issues)},
            )
            logger.error("Project config validation failed: %s", "; ".join(config_issues))
            return

        # Pre-flight checks: catch issues before wasting time on planning
        repos_dict = {rc.id: rc for rc in self._repos} if self._repos else None
        preflight = await run_preflight(
            self._project_dir,
            base_branch=base_branch,
            repos=repos_dict,
            registry=registry,
            resolved_models=resolve_pipeline_models(
                settings,
                registry,
                strategy=settings.model_strategy,
            ),
        )
        if not preflight.passed:
            errors = "\n".join(
                f"  ✗ {e.name}: {e.message}" + (f"\n    Fix: {e.fix_hint}" if e.fix_hint else "")
                for e in preflight.errors
            )
            self._state.apply_event(
                "pipeline:error",
                {"error": f"Pre-flight checks failed:\n{errors}"},
            )
            logger.error("Pre-flight failed: %s", preflight.summary())
            return
        if preflight.warnings:
            for w in preflight.warnings:
                logger.info("Pre-flight warning: %s — %s", w.name, w.message)

        emitter = EventEmitter()
        self._bus = EventBus()
        self._source = EmbeddedSource(emitter, self._bus)
        self._source.connect()

        for evt_type in TUI_EVENT_TYPES:

            async def _handler(data, _type=evt_type):
                self._queue_state_event(_type, data)

            self._bus.subscribe(evt_type, _handler)

        # Use self._repos which includes per-repo base branch overrides
        # from the branch selectors. Do NOT re-resolve from disk — that
        # would discard the user's branch selections.
        repos = self._repos
        self._daemon = ForgeDaemon(
            self._project_dir,
            settings=settings,
            event_emitter=emitter,
            repos=repos if len(repos) > 1 or (repos and repos[0].id != "default") else None,
        )

        self._pipeline_id = str(uuid.uuid4())
        if not self._db:
            self._state.apply_event("pipeline:error", {"error": "Database not initialized"})
            return
        await self._db.create_pipeline(
            id=self._pipeline_id,
            description=task,
            project_dir=self._project_dir,
            model_strategy=settings.model_strategy,
            budget_limit_usd=settings.budget_limit_usd,
            base_branch=base_branch,
            branch_name=branch_name if branch_name else None,
            project_path=self._project_dir,
            project_name=os.path.basename(self._project_dir),
            repos_json=_build_pipeline_repos_json(repos),
            provider_config=json.dumps(
                build_provider_config_snapshot(
                    settings,
                    registry,
                    strategy=settings.model_strategy,
                )
            ),
        )

        self._pipeline_start_time = asyncio.get_running_loop().time()
        self._elapsed_timer = self.set_interval(1.0, self._tick_elapsed)

        try:
            self._graph = await self._daemon.plan(
                task,
                self._db,
                pipeline_id=self._pipeline_id,
                deep_plan=True,
            )
            plan_tasks = [
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
                for t in self._graph.tasks
            ]
            if self._dry_run_mode:
                from forge.core.cost_estimator import estimate_pipeline_cost

                settings = self._settings or ForgeSettings()
                cost_estimate_result = await estimate_pipeline_cost(
                    len(self._graph.tasks),
                    strategy=settings.model_strategy,
                    overrides=settings.build_routing_overrides(),
                    registry=getattr(self._daemon, "_registry", None),
                )
                cost_estimate = {"estimated_cost": cost_estimate_result.total_cost_usd}
                model_assignments = {
                    t.id: str(
                        self._daemon._select_model(
                            "agent",
                            t.complexity.value if hasattr(t.complexity, "value") else t.complexity,
                        )
                    )
                    for t in self._graph.tasks
                }
                self.push_screen(
                    DryRunScreen(
                        plan_tasks, cost_estimate=cost_estimate, model_assignments=model_assignments
                    )
                )
            else:
                self.push_screen(PlanApprovalScreen(plan_tasks))
        except asyncio.CancelledError:
            logger.info("Planning was cancelled")
            self._state.apply_event("pipeline:error", {"error": "Planning cancelled"})
        except RuntimeError as e:
            # SDK cancel scope errors during shutdown — not a real failure
            if "cancel scope" in str(e):
                logger.info("Planning interrupted during shutdown: %s", e)
            else:
                logger.error("Planning failed: %s", e, exc_info=True)
                self._state.apply_event("pipeline:error", {"error": str(e)})
        except Exception as e:
            logger.error("Planning failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})

    async def on_plan_approval_screen_plan_approved(self, event) -> None:
        """User approved the plan — persist status + start contract generation + execution."""
        self.pop_screen()  # Remove PlanApprovalScreen, back to PipelineScreen
        await self._persist_planned_graph(getattr(event, "tasks", None))
        # Launch contracts + execution as a single background task so the
        # TUI event loop stays responsive and can show progress.
        self._daemon_task = asyncio.create_task(self._run_contracts_and_execute())
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def on_dry_run_screen_plan_approved(self, event) -> None:
        """User approved dry-run plan — start full execution."""
        self.pop_screen()  # Remove DryRunScreen, back to PipelineScreen
        await self._persist_planned_graph(getattr(event, "tasks", None))
        self._daemon_task = asyncio.create_task(self._run_contracts_and_execute())
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def on_dry_run_screen_plan_cancelled(self, event) -> None:
        """User exited dry-run plan review — save the plan and return home."""
        self.pop_screen()  # Remove DryRunScreen
        self.pop_screen()  # Remove PipelineScreen, back to HomeScreen
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if self._source:
            self._source.disconnect()
        await self._persist_planned_graph(getattr(event, "tasks", None))
        self._replace_state(TuiState())
        self._daemon = None
        self._graph = None
        self.notify("Plan saved. Resume it from history with Shift+R.", severity="information")

    async def _run_contracts_and_execute(self) -> None:
        """Generate contracts, run countdown, then execute.

        This coroutine stays alive through the entire lifecycle so that
        _daemon_task remains active (quit guards, elapsed timer all work).
        """
        try:
            self._state.apply_event(
                "pipeline:phase_changed",
                {"phase": "contracts"},
            )
            # Persist status so resume knows we're in contract generation
            if self._db and self._pipeline_id:
                await self._db.update_pipeline_status(self._pipeline_id, "contracts")
            self._daemon._contracts = await self._daemon.generate_contracts(
                self._graph,
                self._db,
                self._pipeline_id,
            )
        except Exception as e:
            logger.error("Contract generation failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})
            return

        # Contracts done — run launch countdown (keeps this coroutine alive)
        self._state.apply_event(
            "pipeline:phase_changed",
            {"phase": "countdown"},
        )
        try:
            pipeline_screen = self.query_one(PipelineScreen)
            banner = pipeline_screen.query_one(PhaseBanner)
            # Create event that the countdown message handler will set
            self._countdown_done = asyncio.Event()
            banner.start_countdown(5)
            # Wait for countdown to complete (or cancellation)
            await self._countdown_done.wait()
        except asyncio.CancelledError:
            raise  # Let cancellation propagate cleanly
        except Exception:
            logger.debug("Could not run countdown, proceeding to execute", exc_info=True)

        # Execute — phase transitions to "executing" inside _run_execute
        await self._run_execute()

    def on_phase_banner_countdown_complete(self, event: PhaseBanner.CountdownComplete) -> None:
        """Countdown finished — unblock _run_contracts_and_execute."""
        # Guard: only proceed if we're still in countdown phase
        if self._state.phase != "countdown":
            return
        countdown_done = getattr(self, "_countdown_done", None)
        if countdown_done is not None:
            countdown_done.set()

    async def on_plan_approval_screen_plan_cancelled(self, event) -> None:
        """User exited plan review — save the plan and return to HomeScreen."""
        self.pop_screen()  # Remove PlanApprovalScreen
        self.pop_screen()  # Remove PipelineScreen, back to HomeScreen
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if self._source:
            self._source.disconnect()
        await self._persist_planned_graph(getattr(event, "tasks", None))
        self._daemon = None
        self._graph = None
        self.notify("Plan saved. Resume it from history with Shift+R.", severity="information")

    async def _persist_planned_graph(self, tasks: list[dict] | None = None) -> None:
        """Persist the current plan so the pipeline can be resumed without replanning."""
        if tasks:
            from forge.core.models import TaskDefinition, TaskGraph

            conventions = getattr(self._graph, "conventions", None) if self._graph else None
            integration_hints = (
                getattr(self._graph, "integration_hints", None) if self._graph else None
            )
            self._graph = TaskGraph(
                tasks=[TaskDefinition.model_validate(task) for task in tasks],
                conventions=conventions,
                integration_hints=integration_hints,
            )

        if self._db and self._pipeline_id and self._graph:
            try:
                await self._db.set_pipeline_plan(self._pipeline_id, self._graph.model_dump_json())
            except Exception:
                logger.debug("Failed to persist planned graph", exc_info=True)

    async def _run_execute(self) -> None:
        """Execute the approved plan."""
        # Resolve and cache the pipeline branch so diff views can use it
        try:
            branch = await self._get_pipeline_branch()
            if branch:
                self._cached_pipeline_branch = branch
                self._state.pipeline_branch = branch
        except Exception:
            logger.debug("Could not resolve pipeline branch for state", exc_info=True)
        # Cache the base branch for FinalApprovalScreen's behind-main check
        try:
            if self._db and self._pipeline_id:
                pipeline = await self._db.get_pipeline(self._pipeline_id)
                self._cached_base_branch = getattr(pipeline, "base_branch", None) or "main"
                self._state.base_branch = self._cached_base_branch
        except Exception:
            logger.debug("Could not resolve base branch", exc_info=True)
        try:
            await self._daemon.execute(
                self._graph,
                self._db,
                pipeline_id=self._pipeline_id,
            )
        except Exception as e:
            logger.error("Execution failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})

    def _on_daemon_done(self, task: asyncio.Task) -> None:
        self._force_quit = False  # Reset so next pipeline gets the warning on first q press
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if not task.cancelled() and task.exception():
            error = task.exception()
            logger.error("Daemon crashed: %s", error)
            self._state.apply_event("pipeline:error", {"error": str(error)})
            self.notify(f"Pipeline failed: {_escape_markup(error)}", severity="error", timeout=10)

    def _tick_elapsed(self) -> None:
        if self._pipeline_start_time:
            self._state.elapsed_seconds = (
                asyncio.get_running_loop().time() - self._pipeline_start_time
            )
            self._state._notify("elapsed")

    def action_reset_for_new_task(self) -> None:
        """Clean up all pipeline state and return to HomeScreen for a fresh task."""
        # Stop elapsed timer
        if self._elapsed_timer:
            self._elapsed_timer.stop()
            self._elapsed_timer = None
        # Disconnect event source
        if self._source:
            self._source.disconnect()
            self._source = None
        # Clear pipeline-specific flags
        self._final_approval_pushed = False
        self._daemon = None
        self._daemon_task = None
        self._graph = None
        self._pipeline_id = None
        self._cached_pipeline_branch = ""
        self._cached_base_branch = "main"
        self._pipeline_start_time = None
        # Reset TUI state (tasks, output, costs, etc.)
        self._state.reset()
        # Pop all pushed screens (keep only the default screen at index 0)
        while len(self.screen_stack) > 1:
            self.pop_screen()
        # Push a fresh HomeScreen with recent pipelines
        safe_create_task(self._push_fresh_home(), logger=logger, name="push-fresh-home")

    async def _push_fresh_home(self) -> None:
        """Load recent pipelines and push a fresh HomeScreen."""
        recent = await self._load_recent_pipelines()
        self._repos = self._resolve_repos()
        home = HomeScreen(recent_pipelines=recent, repos=self._repos, project_dir=self._project_dir)
        self.push_screen(home)
        # Focus the prompt input
        try:
            prompt = home.query_one(PromptTextArea)
            prompt.focus()
        except Exception:
            pass

    def _is_input_focused(self) -> bool:
        """Check if a text input widget has focus (typing guard)."""
        try:
            return bool(self.focused and isinstance(self.focused, (TextArea, Input)))
        except Exception:
            return False

    def _is_modal_screen(self) -> bool:
        """Check if the active screen is a modal that shouldn't be switched away from."""
        try:
            return isinstance(self.screen, (PlanApprovalScreen, FinalApprovalScreen, DryRunScreen))
        except Exception:
            return False

    def action_switch_home(self) -> None:
        if self._is_input_focused() or self._is_modal_screen():
            return
        while len(self.screen_stack) > 1:
            self.pop_screen()
        # Push a fresh HomeScreen
        safe_create_task(self._push_fresh_home(), logger=logger, name="switch-home")

    def action_switch_pipeline(self) -> None:
        if self._is_input_focused() or self._is_modal_screen():
            return
        self.push_screen(PipelineScreen(self._state))

    def action_switch_review(self) -> None:
        if self._is_input_focused() or self._is_modal_screen():
            return
        self.push_screen(ReviewScreen(self._state))

    def action_switch_settings(self) -> None:
        if self._is_input_focused() or self._is_modal_screen():
            return
        self.push_screen(SettingsScreen(self._settings))

    def action_switch_stats(self) -> None:
        if self._is_input_focused() or self._is_modal_screen():
            return
        self.push_screen(StatsScreen(db=self._db))

    def action_quit_app(self) -> None:
        if self._daemon_task and not self._daemon_task.done():
            if self._force_quit:
                safe_create_task(self._graceful_quit(), logger=logger, name="graceful-quit")
            else:
                self.notify(
                    "Pipeline running. Press q again to quit (tasks will be saved).",
                    severity="warning",
                )
                self._force_quit = True
        else:
            self.exit()

    async def _graceful_quit(self) -> None:
        """Gracefully shut down: cancel daemon, reset stuck tasks, mark interrupted."""
        if self._daemon_task and not self._daemon_task.done():
            self._daemon_task.cancel()
            try:
                await self._daemon_task
            except (asyncio.CancelledError, RuntimeError, Exception):
                # RuntimeError from SDK cancel scope mismatch during shutdown
                pass

        if self._db and self._pipeline_id:
            # Smart reset: preserve task states where possible to minimize
            # rework on resume. Only reset states where session context is lost.
            _SMART_RESET = {
                "in_progress": "todo",  # agent session lost
                "awaiting_input": "todo",  # question context lost with agent
                "in_review": "in_review",  # code in worktree, review can re-run
                "awaiting_approval": "in_review",  # approval context lost, re-review
                "merging": "in_review",  # merge interrupted, re-review + re-merge
            }
            tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
            for t in tasks:
                new_state = _SMART_RESET.get(t.state)
                if new_state is not None and new_state != t.state:
                    await self._db.update_task_state(t.id, new_state)

            prefix = self._pipeline_id[:8]
            agents = await self._db.list_agents(prefix=prefix)
            for a in agents:
                if a.state != "idle":
                    await self._db.release_agent(a.id)

            # Store which TUI phase the user was in when they quit
            await self._db.set_pipeline_quit_phase(self._pipeline_id, self._state.phase)
            await self._db.update_pipeline_status(self._pipeline_id, "interrupted")
            await self._db.clear_executor_info(self._pipeline_id)

            # Re-fetch tasks after reset so the summary reflects current state
            tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)

            try:
                await self._daemon._emit(
                    "pipeline:interrupted",
                    {
                        "summary": {t.id: t.state for t in tasks},
                    },
                    db=self._db,
                    pipeline_id=self._pipeline_id,
                )
            except Exception:
                pass

        self.exit()

    def action_clear_input(self) -> None:
        """Clear the currently focused text input widget."""
        focused = self.focused
        if focused is None:
            return
        if isinstance(focused, PromptTextArea):
            focused.action_clear_input()
            return
        # Check for FollowUpTextArea (import lazily to avoid circular imports)
        from forge.tui.widgets.followup_input import FollowUpTextArea

        if isinstance(focused, FollowUpTextArea):
            focused.action_clear_input()
            return

    def action_screenshot_export(self) -> None:
        path = os.path.join(self._project_dir, "screenshots")
        os.makedirs(path, exist_ok=True)
        filename = os.path.join(path, f"forge-{self._state.phase}.svg")
        self.save_screenshot(filename)
        self.notify(f"Screenshot saved: {filename}")

    async def _setup_daemon_for_resume(self, pipeline) -> None:
        """Set up daemon, event bus, and event subscriptions for pipeline resume."""
        from forge.config.project_config import ProjectConfig, apply_project_config
        from forge.config.settings import ForgeSettings
        from forge.core.daemon import ForgeDaemon
        from forge.core.events import EventEmitter

        settings = self._settings or ForgeSettings()
        project_config = ProjectConfig.load(pipeline.project_dir)
        apply_project_config(settings, project_config)
        emitter = EventEmitter()
        self._bus = EventBus()
        self._source = EmbeddedSource(emitter, self._bus)
        self._source.connect()

        for evt_type in TUI_EVENT_TYPES:

            async def _handler(data, _type=evt_type):
                self._queue_state_event(_type, data)

            self._bus.subscribe(evt_type, _handler)

        # Resume must rebuild the original repo config from the persisted pipeline
        # metadata; otherwise multi-repo workspaces fall back to a bogus single
        # "default" repo rooted at the workspace directory.
        self._project_dir = pipeline.project_dir
        repos: list[RepoConfig] = []
        try:
            for entry in pipeline.get_repos():
                repo_id = entry.get("id") or entry.get("repo_id", "default")
                raw_path = entry.get("path") or entry.get("project_dir") or pipeline.project_dir
                repo_path = (
                    os.path.realpath(os.path.join(pipeline.project_dir, raw_path))
                    if raw_path and not os.path.isabs(raw_path)
                    else os.path.realpath(raw_path or pipeline.project_dir)
                )
                base_branch = entry.get("base_branch") or getattr(pipeline, "base_branch", None) or ""
                repos.append(RepoConfig(id=repo_id, path=repo_path, base_branch=base_branch))
        except Exception:
            logger.warning(
                "Failed to reconstruct repo config for resumed pipeline %s",
                getattr(pipeline, "id", "<unknown>"),
                exc_info=True,
            )
            repos = []

        if repos:
            self._repos = repos

        self._daemon = ForgeDaemon(
            project_dir=pipeline.project_dir,
            settings=settings,
            event_emitter=emitter,
            repos=repos if len(repos) > 1 or (repos and repos[0].id != "default") else None,
        )

    def _check_orphan_executor(self, executor_pid: int | None) -> bool:
        """Check if an executor PID is still alive. Returns True if alive."""
        if not executor_pid:
            return False
        try:
            os.kill(executor_pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    async def _replay_state_for_pipeline(self, pipeline) -> None:
        """Replay DB events into a fresh TuiState and set pipeline context."""
        state = TuiState()
        state.base_branch = getattr(pipeline, "base_branch", None) or "main"
        events = await self._db.list_events(pipeline.id)
        for evt in events:
            state.apply_event(evt.event_type, evt.payload or {})
        try:
            pending = await self._db.get_pending_questions(pipeline.id)
            pending_ids = {q.task_id for q in pending if getattr(q, "task_id", None)}
            state.pending_questions = {
                task_id: question
                for task_id, question in state.pending_questions.items()
                if task_id in pending_ids
            }
        except Exception:
            logger.debug("Failed to reconcile pending questions for pipeline %s", pipeline.id)
        self._replace_state(state)
        self._pipeline_id = pipeline.id
        self._pipeline_start_time = time.time()
        self._cached_base_branch = getattr(pipeline, "base_branch", None) or "main"
        self._cached_pipeline_branch = getattr(pipeline, "branch_name", None) or ""

    def _load_task_graph(self, pipeline) -> bool:
        """Load TaskGraph from pipeline JSON. Returns True if successful."""
        if not pipeline.task_graph_json:
            return False
        from forge.core.models import TaskGraph

        self._graph = TaskGraph.model_validate_json(pipeline.task_graph_json)
        return True

    async def on_pipeline_list_selected(self, event: PipelineList.Selected) -> None:
        """User pressed Enter on a pipeline — always opens read-only view."""
        pipeline_id = event.pipeline_id
        if not self._db:
            self.notify("Database not available", severity="error")
            return

        try:
            pipeline = await self._db.get_pipeline(pipeline_id)
            if not pipeline:
                self.notify("Pipeline not found", severity="error")
                return

            events = await self._db.list_events(pipeline_id)
            replay_state = TuiState()
            replay_state.base_branch = getattr(pipeline, "base_branch", None) or "main"
            replay_state._replay_date = pipeline.created_at or ""
            for evt in events:
                replay_state.apply_event(evt.event_type, evt.payload or {})
            self.push_screen(PipelineScreen(replay_state, read_only=True))

        except Exception as e:
            logger.error("Failed to load pipeline history: %s", e, exc_info=True)
            self.notify(f"Failed to load pipeline: {_escape_markup(e)}", severity="error")

    async def on_pipeline_list_resume_requested(self, event: PipelineList.ResumeRequested) -> None:
        """User pressed Shift+R on a resumable pipeline — resume/retry it."""
        pipeline_id = event.pipeline_id
        if not self._db:
            self.notify("Database not available", severity="error")
            return

        try:
            ctx = await self._db.get_pipeline_resume_context(pipeline_id)
            if not ctx:
                self.notify("Pipeline not found", severity="error")
                return

            pipeline = await self._db.get_pipeline(pipeline_id)
            if not pipeline:
                self.notify("Pipeline not found", severity="error")
                return

            status = ctx["status"]

            # Validate project_dir exists for resumable statuses
            if status not in ("complete", "cancelled") and ctx["project_dir"]:
                if not os.path.isdir(ctx["project_dir"]):
                    self.notify(
                        f"Project directory no longer exists: {ctx['project_dir']}",
                        severity="error",
                    )
                    return

            # ── planning: restart from scratch (planner session is unrecoverable) ──
            if status == "planning":
                await self._restart_planning_from_scratch(
                    description=ctx["description"],
                    base_branch=ctx["base_branch"] or "main",
                    branch_name=ctx["branch_name"] or "",
                    project_dir=ctx["project_dir"],
                    notify_message=f"Restarting planning for: {ctx['description']}",
                )
                return

            # ── planned: show plan approval ──
            if status == "planned":
                if not ctx["task_graph_json"]:
                    self.notify("No plan found for this pipeline", severity="error")
                    return
                await self._replay_state_for_pipeline(pipeline)
                await self._setup_daemon_for_resume(pipeline)
                self._load_task_graph(pipeline)
                import json as _json

                graph_data = _json.loads(ctx["task_graph_json"])
                raw_tasks = graph_data.get("tasks", [])
                if isinstance(raw_tasks, dict):
                    tasks_iter = [{"id": tid, **task_data} for tid, task_data in raw_tasks.items()]
                elif isinstance(raw_tasks, list):
                    tasks_iter = raw_tasks
                else:
                    tasks_iter = []
                plan_tasks = [
                    {
                        "id": t.get("id", ""),
                        "title": t.get("title", ""),
                        "description": t.get("description", ""),
                        "files": t.get("files", []),
                        "depends_on": t.get("depends_on", []),
                        "complexity": t.get("complexity", "medium"),
                    }
                    for t in tasks_iter
                    if t.get("id")
                ]
                if plan_tasks:
                    self.push_screen(PipelineScreen(self._state))
                    self.push_screen(PlanApprovalScreen(plan_tasks))
                else:
                    self.notify("No tasks found in plan", severity="error")
                return

            # ── contracts / countdown: resume contract gen or skip to execute ──
            if status in ("contracts", "countdown"):
                if not ctx["task_graph_json"]:
                    self.notify("No plan found — cannot resume", severity="error")
                    return
                await self._replay_state_for_pipeline(pipeline)
                await self._setup_daemon_for_resume(pipeline)
                self._load_task_graph(pipeline)
                self.push_screen(PipelineScreen(self._state))

                if ctx["contracts_json"]:
                    self._daemon._contracts = None
                    self._daemon_task = safe_create_task(
                        self._resume_execution(),
                        logger=logger,
                        name="resume-execute-after-contracts",
                    )
                else:
                    self._daemon_task = asyncio.create_task(self._run_contracts_and_execute())
                self._daemon_task.add_done_callback(self._on_daemon_done)
                self.notify(
                    f"Resuming pipeline: {ctx['description']}",
                    severity="information",
                )
                return

            # ── executing / retrying: check for orphan, then resume ──
            if status in ("executing", "retrying"):
                if self._check_orphan_executor(ctx["executor_pid"]):
                    self.notify(
                        "Pipeline may be running in another process "
                        f"(PID {ctx['executor_pid']}). Close it first.",
                        severity="warning",
                    )
                    return
                status = "interrupted"

            if status == "interrupted" and ctx.get("quit_phase") == "planning":
                await self._restart_planning_from_scratch(
                    description=ctx["description"],
                    base_branch=ctx["base_branch"] or "main",
                    branch_name=ctx["branch_name"] or "",
                    project_dir=ctx["project_dir"],
                    notify_message=(
                        "Planning was interrupted earlier — restarting it from scratch."
                    ),
                )
                return

            # ── interrupted: resume execution ──
            if status == "interrupted":
                await self._replay_state_for_pipeline(pipeline)
                await self._setup_daemon_for_resume(pipeline)
                if not self._load_task_graph(pipeline):
                    self.notify("No task graph — cannot resume", severity="error")
                    return
                self.push_screen(PipelineScreen(self._state))

                tasks = await self._db.list_tasks_by_pipeline(pipeline_id)
                reset_count = await self._reset_interrupted_tasks_for_resume(pipeline_id)
                await self._db.update_pipeline_status(pipeline_id, "executing")
                await self._resume_execution()
                done = sum(1 for t in tasks if t.state == "done")
                recovered = (
                    f" Recovered {reset_count} interrupted task{'s' if reset_count != 1 else ''}."
                    if reset_count
                    else ""
                )
                self.notify(
                    f"Resumed pipeline — {done}/{len(tasks)} tasks done.{recovered}",
                    severity="information",
                )
                return

            # ── complete without PR: let user create one ──
            if status == "complete":
                await self._replay_state_for_pipeline(pipeline)
                if not ctx["pr_url"]:
                    await self._setup_daemon_for_resume(pipeline)
                    self._load_task_graph(pipeline)
                    self.push_screen(PipelineScreen(self._state))
                    self._final_approval_pushed = True
                    self._push_final_approval(partial=False)
                    return
                self.notify("Pipeline already complete with PR", severity="information")
                return

            # ── error / partial_success: show final approval for retry ──
            if status in ("error", "partial_success"):
                await self._replay_state_for_pipeline(pipeline)
                await self._setup_daemon_for_resume(pipeline)
                if not self._load_task_graph(pipeline):
                    self.notify("No task graph — cannot retry pipeline", severity="error")
                    return
                resumed = await self._retry_failed_pipeline(push_pipeline_screen=True)
                if not resumed:
                    self.push_screen(PipelineScreen(self._state))
                    self._final_approval_pushed = True
                    self._push_final_approval(partial=True)
                return

            self.notify(f"Cannot resume pipeline with status: {status}", severity="warning")

        except Exception as e:
            logger.error("Failed to resume pipeline: %s", e, exc_info=True)
            self.notify(f"Failed to resume pipeline: {_escape_markup(e)}", severity="error")
