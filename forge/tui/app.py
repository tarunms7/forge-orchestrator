"""Forge TUI Application — main entry point for the terminal UI."""

from __future__ import annotations

import asyncio
import logging
import os
import time


def _escape_markup(text: str) -> str:
    """Escape Rich markup characters in error messages to prevent MarkupError crashes.

    Pydantic/exception messages often contain [ ] = characters that Rich
    interprets as markup tags. This escapes them for safe display in
    Textual notifications/toasts.
    """
    return str(text).replace("[", "\\[")

from textual.app import App
from textual.binding import Binding
from textual.widgets import TextArea, Input

from forge.tui.bus import EventBus, EmbeddedSource, TUI_EVENT_TYPES
from forge.tui.state import TuiState
from forge.tui.screens.home import HomeScreen, PromptTextArea
from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.screens.plan_approval import PlanApprovalScreen
from forge.tui.screens.review import ReviewScreen
from forge.tui.screens.settings import SettingsScreen
from forge.tui.screens.final_approval import FinalApprovalScreen
from forge.tui.widgets.pipeline_list import PipelineList
from forge.tui.widgets.command_palette import CommandPalette

logger = logging.getLogger("forge.tui.app")


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
    CSS = """
    Screen {
        background: #0d1117;
        color: #c9d1d9;
    }
    """

    BINDINGS = [
        Binding("1", "switch_home", "Home", show=True),
        Binding("2", "switch_pipeline", "Pipeline", show=True),
        Binding("3", "switch_review", "Review", show=True),
        Binding("4", "switch_settings", "Settings", show=True),
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
        self._graph = None
        self._pipeline_id = None
        self._final_approval_pushed = False

    async def _init_db(self):
        """Initialize database connection."""
        from forge.storage.db import Database
        from forge.core.paths import forge_db_url
        try:
            self._db = Database(forge_db_url())
            await self._db.initialize()
        except Exception as e:
            logger.error("Failed to initialize database: %s", e, exc_info=True)
            self.notify(f"Database initialization failed: {_escape_markup(e)}", severity="error")
            self._db = None

    async def _load_recent_pipelines(self) -> list[dict]:
        """Load recent pipelines from DB for HomeScreen."""
        if not self._db:
            return []
        try:
            pipelines = await self._db.list_pipelines()
            return [
                {
                    "id": p.id,
                    "description": p.description or "",
                    "status": p.status or "unknown",
                    "created_at": p.created_at or "",
                    "cost": p.total_cost_usd or 0.0,
                    "total_cost_usd": p.total_cost_usd or 0.0,
                    "task_count": 0,
                    "project_dir": p.project_dir or "",
                }
                for p in pipelines[:10]
            ]
        except Exception:
            logger.debug("Failed to load pipeline history", exc_info=True)
            return []

    async def on_mount(self) -> None:
        """Initialize DB, push home screen, wire state changes."""
        await self._init_db()
        # Mount the command palette overlay at the app level so it's available on all screens
        await self.mount(CommandPalette())
        recent = await self._load_recent_pipelines()
        self.push_screen(HomeScreen(recent_pipelines=recent))
        self._state.on_change(self._on_state_change)

    def _on_state_change(self, field: str) -> None:
        """Refresh current screen and auto-capture screenshots."""
        try:
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
        state = self._state
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

        stats = {
            "added": total_added,
            "removed": total_removed,
            "files": total_files,
            "elapsed": elapsed_str,
            "cost": state.total_cost_usd,
            "questions": total_questions,
        }
        task_summaries = [
            {
                "title": t.get("title", ""),
                "state": t.get("state", "done"),
                "added": t.get("merge_result", {}).get("linesAdded", 0),
                "removed": t.get("merge_result", {}).get("linesRemoved", 0),
                "files": t.get("merge_result", {}).get("filesChanged", 0),
                "tests_passed": t.get("tests_passed", 0),
                "tests_total": t.get("tests_total", 0),
                "review": "passed" if t.get("state") == "done" else "failed",
                "error": t.get("error", ""),
            }
            for t in tasks_list
        ]
        # Get pipeline branch for diff viewing — use state cached value or
        # schedule async DB lookup (sync context, cannot await).
        pipeline_branch = getattr(self, "_cached_pipeline_branch", "") or ""
        base_branch = getattr(self, "_cached_base_branch", "main") or "main"
        self.push_screen(FinalApprovalScreen(
            stats=stats, tasks=task_summaries, pipeline_branch=pipeline_branch,
            base_branch=base_branch, partial=partial,
        ))
        # If no cached branch, fetch async and update the screen
        if not pipeline_branch:
            asyncio.create_task(self._resolve_pipeline_branch())

    async def _resolve_pipeline_branch(self) -> None:
        """Fetch pipeline branch from DB and update the FinalApprovalScreen."""
        branch = await self._get_pipeline_branch()
        if branch:
            self._cached_pipeline_branch = branch
            try:
                screen = self.screen
                if isinstance(screen, FinalApprovalScreen):
                    screen._pipeline_branch = branch
            except Exception:
                pass

    async def on_chat_thread_answer_submitted(self, event) -> None:
        """Write the user's answer to DB and update TUI state."""
        task_id = event.task_id
        answer = event.answer
        if not self._db or not self._pipeline_id:
            logger.warning("Cannot record answer: DB or pipeline_id not set")
            return

        # Planning questions use __planning__ sentinel
        is_planning = task_id == "__planning__"

        try:
            pending = await self._db.get_pending_questions(self._pipeline_id)
            for q in pending:
                if q.task_id == task_id and q.answer is None:
                    await self._db.answer_question(q.id, answer, "human")
                    # Emit the right event type for planning questions
                    if is_planning and self._daemon and hasattr(self._daemon, '_events'):
                        await self._daemon._events.emit("planning:answer", {
                            "question_id": q.id,
                            "answer": answer,
                        })
                    break
        except Exception:
            logger.error("Failed to record answer to DB", exc_info=True)

        if is_planning:
            self._state.apply_event("planning:answer", {"answer": answer})
        else:
            self._state.apply_event("task:answer", {"task_id": task_id, "answer": answer})
            # Notify daemon to resume the task
            if self._daemon and hasattr(self._daemon, '_events'):
                try:
                    await self._daemon._events.emit("task:answer", {
                        "task_id": task_id,
                        "answer": answer,
                        "pipeline_id": self._pipeline_id,
                    })
                except Exception:
                    logger.error("Failed to emit task:answer to daemon", exc_info=True)

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
            self._state.apply_event("task:interjection", {
                "task_id": task_id,
                "message": message,
            })
        except Exception:
            logger.error("Failed to create interjection", exc_info=True)

    async def on_final_approval_screen_create_pr(self, event) -> None:
        """User confirmed PR creation from FinalApprovalScreen."""
        from forge.tui.pr_creator import push_branch, create_pr, generate_pr_body

        self._state.apply_event("pipeline:pr_creating", {})

        # Use the pipeline branch stored in DB — NOT the user's current HEAD.
        # The daemon creates an isolated branch (e.g. forge/add-feature) and merges
        # task work into it.  _get_current_branch() returns the user's checkout
        # (usually main), which is wrong.
        branch = await self._get_pipeline_branch()
        if not branch:
            self._state.apply_event("pipeline:pr_failed", {"error": "Could not determine pipeline branch"})
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
        tasks_list = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]

        # Determine the base branch for the PR target
        base_branch = "main"
        if self._db and self._pipeline_id:
            try:
                pipeline = await self._db.get_pipeline(self._pipeline_id)
                base_branch = getattr(pipeline, "base_branch", None) or "main"
            except Exception:
                pass

        try:
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
                tasks=tasks_list,
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
                # Show PR URL inline on the FinalApprovalScreen
                try:
                    screen = self.screen
                    if isinstance(screen, FinalApprovalScreen):
                        screen.show_pr_url(pr_url)
                except Exception:
                    self.notify(f"PR created: {pr_url}", severity="information")
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

        prompt = event.prompt
        if not prompt.strip():
            return

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

        self._state.phase = "executing"
        self._state._notify("phase")
        self._final_approval_pushed = False

        while len(self.screen_stack) > 2:
            self.pop_screen()

        await self._resume_execution()

    async def on_final_approval_screen_rerun(self, event) -> None:
        """User wants to retry failed tasks."""
        if not self._db or not self._pipeline_id:
            return

        tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
        reset_count = 0
        for t in tasks:
            if t.state in ("error", "blocked"):
                await self._db.update_task_state(t.id, "todo")
                reset_count += 1

        if reset_count == 0:
            self.notify("No failed tasks to retry.", severity="warning")
            return

        await self._db.update_pipeline_status(self._pipeline_id, "retrying")
        self._state.phase = "retrying"
        self._state._notify("phase")
        self._final_approval_pushed = False

        while len(self.screen_stack) > 2:
            self.pop_screen()

        await self._resume_execution()

    async def on_final_approval_screen_skip_failed(self, event) -> None:
        """User wants to skip failed tasks and finish."""
        if not self._db or not self._pipeline_id:
            return

        tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
        for t in tasks:
            if t.state in ("error", "blocked"):
                await self._db.update_task_state(t.id, "cancelled")

        await self._db.update_pipeline_status(self._pipeline_id, "complete")
        self._state.phase = "final_approval"
        self._state._notify("phase")

        self._final_approval_pushed = False
        while len(self.screen_stack) > 2:
            self.pop_screen()
        self._push_final_approval()

    async def _resume_execution(self) -> None:
        """Re-enter the daemon execution loop for remaining TODO tasks."""
        if not self._daemon or not self._graph or not self._db:
            self.notify("Cannot resume: missing context.", severity="error")
            return

        self._daemon_task = asyncio.create_task(
            self._daemon.execute(self._graph, self._db, pipeline_id=self._pipeline_id, resume=True)
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
                "git", "rev-parse", "--abbrev-ref", "HEAD",
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
        pending_task_ids = list(state.pending_questions.keys())
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

    async def on_command_palette_action_selected(self, event: CommandPalette.ActionSelected) -> None:
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
                logger.error("Command palette action %s failed: %s", callback_name, e, exc_info=True)
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
        from forge.core.events import EventEmitter
        from forge.core.daemon import ForgeDaemon
        from forge.config.settings import ForgeSettings

        settings = self._settings or ForgeSettings()
        emitter = EventEmitter()
        self._bus = EventBus()
        self._source = EmbeddedSource(emitter, self._bus)
        self._source.connect()

        for evt_type in TUI_EVENT_TYPES:
            async def _handler(data, _type=evt_type):
                self._state.apply_event(_type, data)
            self._bus.subscribe(evt_type, _handler)

        self._daemon = ForgeDaemon(
            self._project_dir,
            settings=settings,
            event_emitter=emitter,
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
        )

        self._pipeline_start_time = asyncio.get_event_loop().time()
        self._elapsed_timer = self.set_interval(1.0, self._tick_elapsed)

        try:
            self._graph = await self._daemon.plan(
                task, self._db, pipeline_id=self._pipeline_id,
                deep_plan=True,
            )
            plan_tasks = [
                {"id": t.id, "title": t.title, "description": t.description,
                 "files": t.files, "depends_on": t.depends_on,
                 "complexity": t.complexity.value}
                for t in self._graph.tasks
            ]
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
        """User approved the plan — start contract generation + execution."""
        self.pop_screen()  # Remove PlanApprovalScreen, back to PipelineScreen
        # Launch contracts + execution as a single background task so the
        # TUI event loop stays responsive and can show progress.
        self._daemon_task = asyncio.create_task(self._run_contracts_and_execute())
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def _run_contracts_and_execute(self) -> None:
        """Generate contracts then execute — runs as background task."""
        try:
            self._state.apply_event(
                "pipeline:phase_changed", {"phase": "contracts"},
            )
            self._daemon._contracts = await self._daemon.generate_contracts(
                self._graph, self._db, self._pipeline_id,
            )
        except Exception as e:
            logger.error("Contract generation failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})
            return
        await self._run_execute()

    async def on_plan_approval_screen_plan_cancelled(self, event) -> None:
        """User cancelled the plan — clean up and return to HomeScreen."""
        self.pop_screen()  # Remove PlanApprovalScreen
        self.pop_screen()  # Remove PipelineScreen, back to HomeScreen
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if self._source:
            self._source.disconnect()
        if self._db and self._pipeline_id:
            try:
                await self._db.update_pipeline_status(self._pipeline_id, "cancelled")
            except Exception:
                logger.debug("Failed to update cancelled pipeline status", exc_info=True)
        self._daemon = None
        self._graph = None
        self.notify("Plan cancelled.", severity="warning")

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
                self._graph, self._db, pipeline_id=self._pipeline_id,
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
            self._state.elapsed_seconds = asyncio.get_event_loop().time() - self._pipeline_start_time
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
        asyncio.create_task(self._push_fresh_home())

    async def _push_fresh_home(self) -> None:
        """Load recent pipelines and push a fresh HomeScreen."""
        recent = await self._load_recent_pipelines()
        home = HomeScreen(recent_pipelines=recent)
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
            return isinstance(self.screen, (PlanApprovalScreen, FinalApprovalScreen))
        except Exception:
            return False

    def action_switch_home(self) -> None:
        if self._is_input_focused() or self._is_modal_screen():
            return
        while len(self.screen_stack) > 1:
            self.pop_screen()
        # Push a fresh HomeScreen
        asyncio.create_task(self._push_fresh_home())

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

    def action_quit_app(self) -> None:
        if self._daemon_task and not self._daemon_task.done():
            if getattr(self, "_force_quit", False):
                asyncio.create_task(self._graceful_quit())
            else:
                self.notify("Pipeline running. Press q again to quit (tasks will be saved).", severity="warning")
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
            tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
            non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
            for t in tasks:
                if t.state in non_terminal:
                    await self._db.update_task_state(t.id, "todo")

            prefix = self._pipeline_id[:8]
            agents = await self._db.list_agents(prefix=prefix)
            for a in agents:
                if a.state != "idle":
                    await self._db.release_agent(a.id)

            await self._db.update_pipeline_status(self._pipeline_id, "interrupted")
            await self._db.clear_executor_info(self._pipeline_id)

            # Re-fetch tasks after reset so the summary reflects current state
            tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)

            try:
                await self._daemon._emit("pipeline:interrupted", {
                    "summary": {t.id: t.state for t in tasks},
                }, db=self._db, pipeline_id=self._pipeline_id)
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

    async def on_pipeline_list_selected(self, event: PipelineList.Selected) -> None:
        """User selected a pipeline from the history list — replay it."""
        pipeline_id = event.pipeline_id
        if not self._db:
            self.notify("Database not available", severity="error")
            return

        try:
            pipeline = await self._db.get_pipeline(pipeline_id)
            if not pipeline:
                self.notify("Pipeline not found", severity="error")
                return

            # Resume a planned pipeline — show plan approval screen
            if pipeline.status == "planned" and pipeline.task_graph_json:
                import json
                graph_data = json.loads(pipeline.task_graph_json)
                tasks_dict = graph_data.get("tasks", {})
                plan_tasks = [
                    {"id": tid, "title": t.get("title", ""), "description": t.get("description", ""),
                     "files": t.get("files", []), "depends_on": t.get("depends_on", []),
                     "complexity": t.get("complexity", "medium")}
                    for tid, t in tasks_dict.items()
                ]
                if plan_tasks:
                    # Set up state for this pipeline
                    self._pipeline_id = pipeline_id
                    self._state = TuiState()
                    self._state.base_branch = getattr(pipeline, "base_branch", None) or "main"
                    # Replay events to restore state
                    events = await self._db.list_events(pipeline_id)
                    for evt in events:
                        self._state.apply_event(evt.event_type, evt.payload or {})
                    # Push pipeline screen then plan approval
                    pipeline_screen = PipelineScreen(self._state)
                    self.push_screen(pipeline_screen)
                    from forge.tui.screens.plan_approval import PlanApprovalScreen
                    self.push_screen(PlanApprovalScreen(plan_tasks))
                    return

            if pipeline.status in ("interrupted", "partial_success"):
                events = await self._db.list_events(pipeline_id)
                state = TuiState()
                state.base_branch = getattr(pipeline, "base_branch", None) or "main"
                for evt in events:
                    state.apply_event(evt.event_type, evt.payload or {})

                self._state = state
                self._pipeline_id = pipeline_id
                self._pipeline_start_time = time.time()

                graph_json = pipeline.task_graph_json
                if graph_json:
                    import json
                    from forge.core.models import TaskGraph
                    self._graph = TaskGraph.model_validate_json(graph_json)

                from forge.core.daemon import ForgeDaemon
                from forge.core.events import EventEmitter
                from forge.tui.bus import EventBus, EmbeddedSource, TUI_EVENT_TYPES
                from forge.config.settings import ForgeSettings

                settings = self._settings or ForgeSettings()
                emitter = EventEmitter()
                self._bus = EventBus()
                self._source = EmbeddedSource(emitter, self._bus)
                self._source.connect()

                for evt_type in TUI_EVENT_TYPES:
                    async def _handler(data, _type=evt_type):
                        self._state.apply_event(_type, data)
                    self._bus.subscribe(evt_type, _handler)

                self._daemon = ForgeDaemon(
                    project_dir=pipeline.project_dir,
                    settings=settings,
                    event_emitter=emitter,
                )

                self.push_screen(PipelineScreen(state))

                if pipeline.status == "interrupted":
                    tasks = await self._db.list_tasks_by_pipeline(pipeline_id)
                    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
                    for t in tasks:
                        if t.state in non_terminal:
                            await self._db.update_task_state(t.id, "todo")
                    tasks = await self._db.list_tasks_by_pipeline(pipeline_id)

                    await self._db.update_pipeline_status(pipeline_id, "executing")
                    await self._resume_execution()
                    self.notify(f"Resumed pipeline — {sum(1 for t in tasks if t.state == 'done')}/{len(tasks)} tasks done", severity="information")

                elif pipeline.status == "partial_success":
                    self._final_approval_pushed = True
                    self._push_final_approval(partial=True)

                return

            # Load events and replay into a fresh TuiState (read-only history)
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
