"""Tests for resume pipeline features in ForgeApp."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.tui.app import ForgeApp


# ── Helpers ────────────────────────────────────────────────────────────


def _make_task(id: str, state: str) -> SimpleNamespace:
    return SimpleNamespace(id=id, state=state)


def _make_pipeline(
    id: str = "pipe-1",
    status: str = "complete",
    task_graph_json: str | None = None,
    contracts_json: str | None = None,
    pr_url: str | None = None,
    project_dir: str = "/tmp/test-project",
    base_branch: str = "main",
    branch_name: str | None = None,
    description: str = "test pipeline",
    executor_pid: int | None = None,
    quit_phase: str | None = None,
    created_at: str = "2026-03-31T10:00:00Z",
    total_cost_usd: float = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        status=status,
        task_graph_json=task_graph_json,
        contracts_json=contracts_json,
        pr_url=pr_url,
        project_dir=project_dir,
        base_branch=base_branch,
        branch_name=branch_name,
        description=description,
        executor_pid=executor_pid,
        quit_phase=quit_phase,
        created_at=created_at,
        total_cost_usd=total_cost_usd,
    )


def _make_resume_context(
    status: str = "complete",
    quit_phase: str | None = None,
    task_graph_json: str | None = None,
    contracts_json: str | None = None,
    pr_url: str | None = None,
    project_dir: str = "/tmp/test-project",
    base_branch: str = "main",
    branch_name: str | None = None,
    description: str = "test pipeline",
    executor_pid: int | None = None,
    total_tasks: int = 0,
    tasks_done: int = 0,
    tasks_error: int = 0,
    tasks_in_review: int = 0,
    tasks_blocked: int = 0,
) -> dict:
    return {
        "status": status,
        "quit_phase": quit_phase,
        "task_graph_json": task_graph_json,
        "contracts_json": contracts_json,
        "pr_url": pr_url,
        "project_dir": project_dir,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "description": description,
        "executor_pid": executor_pid,
        "total_tasks": total_tasks,
        "tasks_done": tasks_done,
        "tasks_error": tasks_error,
        "tasks_in_review": tasks_in_review,
        "tasks_blocked": tasks_blocked,
    }


SAMPLE_TASK_GRAPH_JSON = '{"tasks": {"t1": {"id": "t1", "title": "Task 1", "description": "Do thing", "files": ["a.py"], "depends_on": [], "complexity": "medium"}}}'

# Use a project_dir that actually exists for resume routing tests
EXISTING_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Part A: Smart graceful quit ────────────────────────────────────────


class TestSmartGracefulQuit:
    """Test that _graceful_quit applies smart reset mapping."""

    @pytest.mark.asyncio
    async def test_in_review_stays_in_review(self):
        """in_review tasks should keep their state (code in worktree)."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        tasks = [_make_task("t1", "in_review")]
        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
        db.update_task_state = AsyncMock()
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        # in_review stays in_review — no update_task_state call needed
        db.update_task_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_merging_resets_to_in_review(self):
        """merging tasks should be reset to in_review."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        tasks = [_make_task("t1", "merging")]
        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
        db.update_task_state = AsyncMock()
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        db.update_task_state.assert_called_once_with("t1", "in_review")

    @pytest.mark.asyncio
    async def test_awaiting_approval_resets_to_in_review(self):
        """awaiting_approval tasks should be reset to in_review."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        tasks = [_make_task("t1", "awaiting_approval")]
        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
        db.update_task_state = AsyncMock()
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        db.update_task_state.assert_called_once_with("t1", "in_review")

    @pytest.mark.asyncio
    async def test_in_progress_resets_to_todo(self):
        """in_progress tasks should be reset to todo (agent session lost)."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        tasks = [_make_task("t1", "in_progress")]
        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
        db.update_task_state = AsyncMock()
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        db.update_task_state.assert_called_once_with("t1", "todo")

    @pytest.mark.asyncio
    async def test_awaiting_input_resets_to_todo(self):
        """awaiting_input tasks should be reset to todo."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        tasks = [_make_task("t1", "awaiting_input")]
        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
        db.update_task_state = AsyncMock()
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        db.update_task_state.assert_called_once_with("t1", "todo")

    @pytest.mark.asyncio
    async def test_quit_phase_is_stored(self):
        """Graceful quit should store the current TUI phase."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=[])
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        db.set_pipeline_quit_phase.assert_called_once_with("pipe-1", "executing")

    @pytest.mark.asyncio
    async def test_mixed_states_smart_reset(self):
        """Multiple tasks with different states get the right reset."""
        app = ForgeApp.__new__(ForgeApp)
        app._daemon_task = None
        app._pipeline_id = "pipe-1"
        app._state = MagicMock(phase="executing")
        app._daemon = MagicMock(_emit=AsyncMock())

        tasks = [
            _make_task("t1", "in_progress"),
            _make_task("t2", "in_review"),
            _make_task("t3", "merging"),
            _make_task("t4", "done"),
            _make_task("t5", "awaiting_input"),
        ]
        db = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
        db.update_task_state = AsyncMock()
        db.list_agents = AsyncMock(return_value=[])
        db.set_pipeline_quit_phase = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.clear_executor_info = AsyncMock()
        app._db = db
        app.exit = MagicMock()

        await app._graceful_quit()

        calls = {c.args for c in db.update_task_state.call_args_list}
        assert ("t1", "todo") in calls       # in_progress → todo
        assert ("t3", "in_review") in calls   # merging → in_review
        assert ("t5", "todo") in calls        # awaiting_input → todo
        # in_review stays as-is, done is terminal — neither should be updated
        assert not any(c[0] == "t2" for c in calls)
        assert not any(c[0] == "t4" for c in calls)


# ── Part B: Resume router ─────────────────────────────────────────────


class TestCheckOrphanExecutor:
    def test_no_pid_returns_false(self):
        app = ForgeApp.__new__(ForgeApp)
        assert app._check_orphan_executor(None) is False

    def test_dead_pid_returns_false(self):
        app = ForgeApp.__new__(ForgeApp)
        # PID 99999999 almost certainly doesn't exist
        assert app._check_orphan_executor(99999999) is False

    def test_alive_pid_returns_true(self):
        app = ForgeApp.__new__(ForgeApp)
        # Current process PID is always alive
        assert app._check_orphan_executor(os.getpid()) is True


class TestResumeRouter:
    """Test that on_pipeline_list_selected routes to correct screens by status."""

    def _make_app(self):
        app = ForgeApp.__new__(ForgeApp)
        app._db = AsyncMock()
        app._settings = None
        app._state = MagicMock(phase="idle")
        app._bus = MagicMock()
        app._source = None
        app._daemon = None
        app._daemon_task = None
        app._graph = None
        app._pipeline_id = None
        app._pipeline_start_time = None
        app._final_approval_pushed = False
        app._cached_pipeline_branch = ""
        app._cached_base_branch = "main"
        app._project_dir = "/tmp/test"
        app._elapsed_timer = None
        app.notify = MagicMock()
        app.push_screen = MagicMock()
        app.pop_screen = MagicMock()
        app._push_final_approval = MagicMock()
        app._resume_execution = AsyncMock()
        app._setup_daemon_for_resume = AsyncMock()
        app._replay_state_for_pipeline = AsyncMock()
        app._load_task_graph = MagicMock(return_value=True)
        return app

    def _make_event(self, pipeline_id: str = "pipe-1"):
        evt = MagicMock()
        evt.pipeline_id = pipeline_id
        return evt

    @pytest.mark.asyncio
    async def test_planned_creates_daemon(self):
        """BUG FIX: 'planned' status should call _setup_daemon_for_resume."""
        app = self._make_app()
        pipeline = _make_pipeline(status="planned", task_graph_json=SAMPLE_TASK_GRAPH_JSON, project_dir=EXISTING_PROJECT_DIR)
        ctx = _make_resume_context(status="planned", task_graph_json=SAMPLE_TASK_GRAPH_JSON, project_dir=EXISTING_PROJECT_DIR)
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app._setup_daemon_for_resume.assert_called_once_with(pipeline)
        # Should push PipelineScreen + PlanApprovalScreen
        assert app.push_screen.call_count == 2

    @pytest.mark.asyncio
    async def test_contracts_skips_generation_when_contracts_exist(self):
        """contracts status with existing contracts_json should skip to execution."""
        app = self._make_app()
        pipeline = _make_pipeline(
            status="contracts",
            task_graph_json=SAMPLE_TASK_GRAPH_JSON,
            contracts_json='{"api_contracts": []}',
            project_dir=EXISTING_PROJECT_DIR,
        )
        ctx = _make_resume_context(
            status="contracts",
            task_graph_json=SAMPLE_TASK_GRAPH_JSON,
            contracts_json='{"api_contracts": []}',
            project_dir=EXISTING_PROJECT_DIR,
        )
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        # Mock safe_create_task to capture what gets called
        with patch("forge.tui.app.safe_create_task") as mock_sct:
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            mock_sct.return_value = mock_task
            await app.on_pipeline_list_selected(self._make_event())

        app._setup_daemon_for_resume.assert_called_once()
        # Should push PipelineScreen
        assert app.push_screen.call_count == 1

    @pytest.mark.asyncio
    async def test_complete_without_pr_shows_interactive_final_approval(self):
        """complete with no pr_url should push interactive FinalApprovalScreen."""
        app = self._make_app()
        pipeline = _make_pipeline(
            status="complete",
            task_graph_json=SAMPLE_TASK_GRAPH_JSON,
            pr_url=None,
        )
        ctx = _make_resume_context(status="complete", pr_url=None, task_graph_json=SAMPLE_TASK_GRAPH_JSON)
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app._setup_daemon_for_resume.assert_called_once()
        app._push_final_approval.assert_called_once_with(partial=False)
        assert app._final_approval_pushed is True

    @pytest.mark.asyncio
    async def test_complete_with_pr_shows_readonly(self):
        """complete with pr_url should show read-only replay."""
        app = self._make_app()
        pipeline = _make_pipeline(
            status="complete",
            pr_url="https://github.com/org/repo/pull/42",
        )
        ctx = _make_resume_context(
            status="complete",
            pr_url="https://github.com/org/repo/pull/42",
        )
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)
        app._db.list_events = AsyncMock(return_value=[])

        await app.on_pipeline_list_selected(self._make_event())

        # Should push read-only PipelineScreen
        assert app.push_screen.call_count == 1
        # Should NOT set up daemon
        app._setup_daemon_for_resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_executing_with_orphan_executor_warns(self):
        """executing with alive executor PID should warn and not resume."""
        app = self._make_app()
        ctx = _make_resume_context(
            status="executing",
            executor_pid=os.getpid(),  # current process = alive
            task_graph_json=SAMPLE_TASK_GRAPH_JSON,
            project_dir=EXISTING_PROJECT_DIR,
        )
        pipeline = _make_pipeline(status="executing", executor_pid=os.getpid(), project_dir=EXISTING_PROJECT_DIR)
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app.notify.assert_called_once()
        assert "running in another process" in str(app.notify.call_args)
        app._setup_daemon_for_resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_interrupted_resumes_execution(self):
        """interrupted status should resume execution."""
        app = self._make_app()
        pipeline = _make_pipeline(
            status="interrupted",
            task_graph_json=SAMPLE_TASK_GRAPH_JSON,
            project_dir=EXISTING_PROJECT_DIR,
        )
        ctx = _make_resume_context(
            status="interrupted",
            task_graph_json=SAMPLE_TASK_GRAPH_JSON,
            total_tasks=3,
            tasks_done=1,
            project_dir=EXISTING_PROJECT_DIR,
        )
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)
        app._db.list_tasks_by_pipeline = AsyncMock(
            return_value=[_make_task("t1", "done"), _make_task("t2", "todo")]
        )
        app._db.update_pipeline_status = AsyncMock()

        await app.on_pipeline_list_selected(self._make_event())

        app._setup_daemon_for_resume.assert_called_once()
        app._resume_execution.assert_called_once()
        app._db.update_pipeline_status.assert_called_with("pipe-1", "executing")

    @pytest.mark.asyncio
    async def test_error_shows_final_approval_partial(self):
        """error status should push FinalApprovalScreen with partial=True."""
        app = self._make_app()
        pipeline = _make_pipeline(status="error", task_graph_json=SAMPLE_TASK_GRAPH_JSON, project_dir=EXISTING_PROJECT_DIR)
        ctx = _make_resume_context(status="error", task_graph_json=SAMPLE_TASK_GRAPH_JSON, project_dir=EXISTING_PROJECT_DIR)
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app._push_final_approval.assert_called_once_with(partial=True)

    @pytest.mark.asyncio
    async def test_partial_success_shows_final_approval(self):
        """partial_success should push FinalApprovalScreen with partial=True."""
        app = self._make_app()
        pipeline = _make_pipeline(status="partial_success", task_graph_json=SAMPLE_TASK_GRAPH_JSON, project_dir=EXISTING_PROJECT_DIR)
        ctx = _make_resume_context(status="partial_success", task_graph_json=SAMPLE_TASK_GRAPH_JSON, project_dir=EXISTING_PROJECT_DIR)
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app._push_final_approval.assert_called_once_with(partial=True)

    @pytest.mark.asyncio
    async def test_cancelled_shows_readonly(self):
        """cancelled status should show read-only replay."""
        app = self._make_app()
        pipeline = _make_pipeline(status="cancelled")
        ctx = _make_resume_context(status="cancelled")
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)
        app._db.list_events = AsyncMock(return_value=[])

        await app.on_pipeline_list_selected(self._make_event())

        assert app.push_screen.call_count == 1
        app._setup_daemon_for_resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_project_dir_shows_error(self):
        """Resumable pipeline with missing project_dir should error."""
        app = self._make_app()
        ctx = _make_resume_context(
            status="interrupted",
            project_dir="/nonexistent/path/that/does/not/exist",
        )
        pipeline = _make_pipeline(status="interrupted", project_dir="/nonexistent/path")
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app.notify.assert_called_once()
        assert "no longer exists" in str(app.notify.call_args)

    @pytest.mark.asyncio
    async def test_planned_without_task_graph_shows_error(self):
        """planned status without task_graph_json should show error."""
        app = self._make_app()
        pipeline = _make_pipeline(status="planned", task_graph_json=None, project_dir=EXISTING_PROJECT_DIR)
        ctx = _make_resume_context(status="planned", task_graph_json=None, project_dir=EXISTING_PROJECT_DIR)
        app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
        app._db.get_pipeline = AsyncMock(return_value=pipeline)

        await app.on_pipeline_list_selected(self._make_event())

        app.notify.assert_called_once()
        assert "No plan found" in str(app.notify.call_args)


# ── Part C: Status persistence ─────────────────────────────────────────


class TestStatusPersistence:
    @pytest.mark.asyncio
    async def test_plan_approved_persists_planned_status(self):
        """on_plan_approval_screen_plan_approved should persist plan to DB."""
        app = ForgeApp.__new__(ForgeApp)
        app._pipeline_id = "pipe-1"
        app._daemon_task = None
        app._state = MagicMock(phase="planned")
        app.pop_screen = MagicMock()

        graph = MagicMock()
        graph.model_dump_json.return_value = SAMPLE_TASK_GRAPH_JSON
        app._graph = graph

        db = AsyncMock()
        db.set_pipeline_plan = AsyncMock()
        app._db = db

        # Mock _run_contracts_and_execute to avoid running it
        with patch.object(app, "_run_contracts_and_execute", new_callable=AsyncMock):
            # Create a proper event
            event = MagicMock()
            await app.on_plan_approval_screen_plan_approved(event)

        db.set_pipeline_plan.assert_called_once_with("pipe-1", SAMPLE_TASK_GRAPH_JSON)


# ── Part D: Enriched pipeline list ─────────────────────────────────────


class TestLoadRecentPipelines:
    @pytest.mark.asyncio
    async def test_returns_enriched_data(self):
        """_load_recent_pipelines should return enriched pipeline dicts with task counts."""
        app = ForgeApp.__new__(ForgeApp)
        db = AsyncMock()
        db.get_pipeline_list_with_counts = AsyncMock(
            return_value=[
                {
                    "id": "abc-123",
                    "description": "Add JWT auth",
                    "status": "complete",
                    "created_at": "2026-03-31T10:00:00Z",
                    "cost": 1.25,
                    "total_tasks": 5,
                    "tasks_done": 5,
                    "tasks_error": 0,
                    "pr_url": "https://github.com/org/repo/pull/42",
                    "project_dir": "/Users/dev/myproject",
                },
                {
                    "id": "def-456",
                    "description": "Fix validation",
                    "status": "interrupted",
                    "created_at": "2026-03-30T14:00:00Z",
                    "cost": 0.45,
                    "total_tasks": 3,
                    "tasks_done": 1,
                    "tasks_error": 0,
                    "pr_url": None,
                    "project_dir": "/Users/dev/myproject",
                },
            ]
        )
        app._db = db

        result = await app._load_recent_pipelines()

        assert len(result) == 2

        # First pipeline — complete with PR
        p1 = result[0]
        assert p1["id"] == "abc-123"
        assert p1["description"] == "Add JWT auth"
        assert p1["status"] == "complete"
        assert p1["total_tasks"] == 5
        assert p1["tasks_done"] == 5
        assert p1["tasks_error"] == 0
        assert p1["pr_url"] == "https://github.com/org/repo/pull/42"
        assert p1["cost"] == 1.25
        assert p1["total_cost_usd"] == 1.25

        # Second pipeline — interrupted, no PR
        p2 = result[1]
        assert p2["status"] == "interrupted"
        assert p2["total_tasks"] == 3
        assert p2["tasks_done"] == 1
        assert p2["pr_url"] is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_db(self):
        """_load_recent_pipelines should return [] when DB is not available."""
        app = ForgeApp.__new__(ForgeApp)
        app._db = None

        result = await app._load_recent_pipelines()
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_missing_fields_gracefully(self):
        """_load_recent_pipelines should handle None/missing fields with defaults."""
        app = ForgeApp.__new__(ForgeApp)
        db = AsyncMock()
        db.get_pipeline_list_with_counts = AsyncMock(
            return_value=[
                {
                    "id": "x",
                    "description": None,
                    "status": None,
                    "created_at": None,
                    "cost": None,
                    "total_tasks": 0,
                    "tasks_done": 0,
                    "tasks_error": 0,
                    "pr_url": None,
                    "project_dir": None,
                }
            ]
        )
        app._db = db

        result = await app._load_recent_pipelines()
        p = result[0]
        assert p["description"] == ""
        assert p["status"] == "unknown"
        assert p["created_at"] == ""
        assert p["cost"] == 0.0
        assert p["total_cost_usd"] == 0.0
        assert p["project_dir"] == ""
