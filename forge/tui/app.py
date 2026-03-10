"""Forge TUI Application — main entry point for the terminal UI."""

from __future__ import annotations

import asyncio
import logging
import os

from textual.app import App
from textual.binding import Binding

from forge.tui.bus import EventBus, EmbeddedSource, TUI_EVENT_TYPES
from forge.tui.state import TuiState
from forge.tui.screens.home import HomeScreen
from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.screens.plan_approval import PlanApprovalScreen
from forge.tui.screens.review import ReviewScreen
from forge.tui.screens.settings import SettingsScreen

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
                }
                for p in pipelines[:10]
            ]
        except Exception:
            logger.debug("Failed to load pipeline history", exc_info=True)
            return []

    async def on_mount(self) -> None:
        """Initialize DB, push home screen, wire state changes."""
        await self._init_db()
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
        """User approved the plan — start execution."""
        self.pop_screen()  # Remove PlanApprovalScreen, back to PipelineScreen
        try:
            self._daemon._contracts = await self._daemon.generate_contracts(
                self._graph, self._db, self._pipeline_id,
            )
            self._daemon_task = asyncio.create_task(self._run_execute())
            self._daemon_task.add_done_callback(self._on_daemon_done)
        except Exception as e:
            logger.error("Contract generation failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})

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

    def action_screenshot_export(self) -> None:
        path = os.path.join(self._project_dir, "screenshots")
        os.makedirs(path, exist_ok=True)
        filename = os.path.join(path, f"forge-{self._state.phase}.svg")
        self.save_screenshot(filename)
        self.notify(f"Screenshot saved: {filename}")
