"""Forge TUI Application — main entry point for the terminal UI."""

from __future__ import annotations

import asyncio
import logging
import os

from textual.app import App
from textual.binding import Binding

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
        self._db_path = os.path.join(self._project_dir, ".forge", "forge.db")
        self._db = None
        self._graph = None
        self._pipeline_id = None
        self._final_approval_pushed = False

    async def _init_db(self):
        """Initialize database connection."""
        from forge.storage.db import Database
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = Database(f"sqlite+aiosqlite:///{self._db_path}")
        await self._db.initialize()

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

    def _push_final_approval(self) -> None:
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
                "added": t.get("merge_result", {}).get("linesAdded", 0),
                "removed": t.get("merge_result", {}).get("linesRemoved", 0),
                "tests_passed": t.get("tests_passed", 0),
                "tests_total": t.get("tests_total", 0),
                "review": "passed" if t.get("state") == "done" else "failed",
            }
            for t in tasks_list
        ]
        # Get pipeline branch for diff viewing — use state cached value or
        # schedule async DB lookup (sync context, cannot await).
        pipeline_branch = getattr(self, "_cached_pipeline_branch", "") or ""
        self.push_screen(FinalApprovalScreen(
            stats=stats, tasks=task_summaries, pipeline_branch=pipeline_branch,
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
        try:
            pending = await self._db.get_pending_questions(self._pipeline_id)
            for q in pending:
                if q.task_id == task_id and q.answer is None:
                    await self._db.answer_question(q.id, answer, "human")
                    break
        except Exception:
            logger.error("Failed to record answer to DB", exc_info=True)
        self._state.apply_event("task:answer", {"task_id": task_id, "answer": answer})
        # The daemon's execution loop detects the answered question and resumes the task.

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
                self.notify("PR creation failed: check logs.", severity="error")
        except Exception as e:
            logger.error("PR creation error: %s", e, exc_info=True)
            self._state.apply_event("pipeline:pr_failed", {"error": str(e)})
            self.notify(f"PR creation error: {e}", severity="error")

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
                self.notify(f"Action failed: {e}", severity="error")
        else:
            self.notify(f"Action '{action.name}' not available", severity="warning")

    async def on_home_screen_task_submitted(self, event: HomeScreen.TaskSubmitted) -> None:
        """User submitted a task from HomeScreen."""
        task = event.task
        logger.info("Task submitted: %s", task)
        self._state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        pipeline_screen = PipelineScreen(self._state)
        self.push_screen(pipeline_screen)
        # CRITICAL: Use create_task, NOT await — planning is a long LLM call
        # that would block the Textual event loop and freeze the UI.
        asyncio.create_task(self._run_plan(task))

    async def _run_plan(self, task: str) -> None:
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
            description=task[:200],
            project_dir=self._project_dir,
            model_strategy=settings.model_strategy,
            budget_limit_usd=settings.budget_limit_usd,
        )

        self._pipeline_start_time = asyncio.get_event_loop().time()
        self._elapsed_timer = self.set_interval(1.0, self._tick_elapsed)

        try:
            self._graph = await self._daemon.plan(
                task, self._db, pipeline_id=self._pipeline_id,
            )
            plan_tasks = [
                {"id": t.id, "title": t.title, "description": t.description,
                 "files": t.files, "depends_on": t.depends_on,
                 "complexity": t.complexity.value}
                for t in self._graph.tasks
            ]
            self.push_screen(PlanApprovalScreen(plan_tasks))
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
        try:
            await self._daemon.execute(
                self._graph, self._db, pipeline_id=self._pipeline_id,
            )
        except Exception as e:
            logger.error("Execution failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})

    def _on_daemon_done(self, task: asyncio.Task) -> None:
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if not task.cancelled() and task.exception():
            logger.error("Daemon crashed: %s", task.exception())

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
        self._pipeline_start_time = None
        # Reset TUI state (tasks, output, costs, etc.)
        self._state.reset()
        # Pop all screens back to HomeScreen
        while len(self.screen_stack) > 1:
            self.pop_screen()

    def action_switch_home(self) -> None:
        while len(self.screen_stack) > 1:
            self.pop_screen()

    def action_switch_pipeline(self) -> None:
        self.push_screen(PipelineScreen(self._state))

    def action_switch_review(self) -> None:
        self.push_screen(ReviewScreen(self._state))

    def action_switch_settings(self) -> None:
        self.push_screen(SettingsScreen(self._settings))

    def action_quit_app(self) -> None:
        if self._daemon_task and not self._daemon_task.done():
            self.notify("Pipeline running. Press q again to force quit.", severity="warning")
            # Replace binding to force quit on second press
            self._force_quit = getattr(self, "_force_quit", False)
            if self._force_quit:
                self.exit()
            self._force_quit = True
        else:
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

            # Load events and replay into a fresh TuiState
            events = await self._db.list_events(pipeline_id)
            replay_state = TuiState()
            replay_state._replay_date = pipeline.created_at or ""

            for evt in events:
                replay_state.apply_event(evt.event_type, evt.payload or {})

            # Push PipelineScreen in read-only mode
            self.push_screen(PipelineScreen(replay_state, read_only=True))

        except Exception as e:
            logger.error("Failed to load pipeline history: %s", e, exc_info=True)
            self.notify(f"Failed to load pipeline: {e}", severity="error")
