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
        pipeline_screen = PipelineScreen(self._state)
        self.push_screen(pipeline_screen)
        await self._start_pipeline(task)

    async def _start_pipeline(self, task: str) -> None:
        """Launch the daemon and start executing the task."""
        from forge.core.events import EventEmitter
        from forge.core.daemon import ForgeDaemon
        from forge.config.settings import ForgeSettings

        settings = self._settings or ForgeSettings()
        emitter = EventEmitter()
        self._bus = EventBus()
        self._source = EmbeddedSource(emitter, self._bus)
        self._source.connect()

        # Wire bus events to state
        for evt_type in TUI_EVENT_TYPES:
            async def _handler(data, _type=evt_type):
                self._state.apply_event(_type, data)
            self._bus.subscribe(evt_type, _handler)

        self._daemon = ForgeDaemon(
            self._project_dir,
            settings=settings,
            event_emitter=emitter,
        )

        self._pipeline_start_time = asyncio.get_event_loop().time()
        self._elapsed_timer = self.set_interval(1.0, self._tick_elapsed)

        self._daemon_task = asyncio.create_task(self._run_daemon(task))
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def _run_daemon(self, task: str) -> None:
        """Run the daemon pipeline."""
        try:
            await self._daemon.run(task)
        except Exception as e:
            logger.error("Daemon failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})

    def _on_daemon_done(self, task: asyncio.Task) -> None:
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if not task.cancelled() and task.exception():
            logger.error("Daemon crashed: %s", task.exception())

    def _tick_elapsed(self) -> None:
        if self._pipeline_start_time:
            self._state.elapsed_seconds = asyncio.get_event_loop().time() - self._pipeline_start_time

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
