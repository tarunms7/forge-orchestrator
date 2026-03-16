"""Tests for ForgeDaemon: pause tracking, all_tasks_done event, question timeout checker."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon, _classify_pipeline_result
from forge.core.models import TaskState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daemon(tmp_path, **settings_kwargs):
    settings = ForgeSettings(**settings_kwargs)
    return ForgeDaemon(project_dir=str(tmp_path), settings=settings)


def _make_task(state: str, task_id: str = "task-1") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.state = state
    # Fields required by _row_to_record / Scheduler
    t.title = f"Task {task_id}"
    t.description = "test task"
    t.files = []
    t.depends_on = []
    t.complexity = "medium"
    t.assigned_agent = None
    t.retry_count = 0
    return t


def _make_question(q_id: str, task_id: str, pipeline_id: str) -> MagicMock:
    q = MagicMock()
    q.id = q_id
    q.task_id = task_id
    q.pipeline_id = pipeline_id
    return q


# ---------------------------------------------------------------------------
# Tests for _check_question_timeouts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCheckQuestionTimeouts:
    """Unit tests for ForgeDaemon._check_question_timeouts."""

    async def test_auto_answers_expired_question(self, tmp_path):
        """Expired questions for this pipeline get answered with a default message."""
        daemon = _make_daemon(tmp_path, question_timeout=1800)
        pipeline_id = "pipe-abc"

        expired_q = _make_question("q-1", "task-1", pipeline_id)

        db = MagicMock()
        db.get_expired_questions = AsyncMock(return_value=[expired_q])
        db.answer_question = AsyncMock()
        db.log_event = AsyncMock()

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        await daemon._check_question_timeouts(db, pipeline_id)

        db.answer_question.assert_called_once_with(
            "q-1", "Proceed with your best judgment.", "timeout"
        )
        assert len(emitted) == 1
        event_type, payload = emitted[0]
        assert event_type == "task:auto_decided"
        assert payload["task_id"] == "task-1"
        assert payload["reason"] == "timeout"
        assert payload["question_id"] == "q-1"

    async def test_skips_questions_for_other_pipelines(self, tmp_path):
        """Questions belonging to other pipelines are not auto-answered."""
        daemon = _make_daemon(tmp_path, question_timeout=1800)
        pipeline_id = "pipe-abc"

        other_q = _make_question("q-2", "task-x", "pipe-other")

        db = MagicMock()
        db.get_expired_questions = AsyncMock(return_value=[other_q])
        db.answer_question = AsyncMock()
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        await daemon._check_question_timeouts(db, pipeline_id)

        db.answer_question.assert_not_called()
        daemon._emit.assert_not_called()

    async def test_no_expired_questions_is_a_noop(self, tmp_path):
        """When there are no expired questions, nothing is emitted or answered."""
        daemon = _make_daemon(tmp_path, question_timeout=1800)

        db = MagicMock()
        db.get_expired_questions = AsyncMock(return_value=[])
        db.answer_question = AsyncMock()

        daemon._emit = AsyncMock()

        await daemon._check_question_timeouts(db, "pipe-abc")

        db.answer_question.assert_not_called()
        daemon._emit.assert_not_called()

    async def test_db_error_does_not_propagate(self, tmp_path):
        """If db.get_expired_questions raises, the method swallows the error."""
        daemon = _make_daemon(tmp_path, question_timeout=1800)

        db = MagicMock()
        db.get_expired_questions = AsyncMock(side_effect=RuntimeError("DB down"))

        daemon._emit = AsyncMock()

        # Should not raise
        await daemon._check_question_timeouts(db, "pipe-abc")

    async def test_answer_error_does_not_abort_other_questions(self, tmp_path):
        """If answering one question fails, the method still processes remaining ones."""
        daemon = _make_daemon(tmp_path, question_timeout=1800)
        pipeline_id = "pipe-abc"

        q1 = _make_question("q-1", "task-1", pipeline_id)
        q2 = _make_question("q-2", "task-2", pipeline_id)

        db = MagicMock()
        db.get_expired_questions = AsyncMock(return_value=[q1, q2])
        db.answer_question = AsyncMock(side_effect=[RuntimeError("fail"), None])
        db.log_event = AsyncMock()

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        # Should not raise
        await daemon._check_question_timeouts(db, pipeline_id)

        # Second question should still be processed
        assert db.answer_question.call_count == 2
        # Only second one should have emitted (first failed before emit)
        assert len(emitted) == 1
        assert emitted[0][1]["question_id"] == "q-2"


# ---------------------------------------------------------------------------
# Tests for pipeline:all_tasks_done event in _execution_loop
# ---------------------------------------------------------------------------

def _make_minimal_execution_loop_mocks(tasks: list[MagicMock]):
    """Build the mocks needed for _execution_loop to run one iteration and exit."""
    db = MagicMock()
    db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)
    db.list_agents = AsyncMock(return_value=[])
    db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False, executor_token=None))
    db.log_event = AsyncMock()
    db.get_expired_questions = AsyncMock(return_value=[])
    db.update_pipeline_status = AsyncMock()
    db.set_executor_info = AsyncMock()
    db.clear_executor_info = AsyncMock()

    monitor = MagicMock()
    snapshot = MagicMock()
    monitor.take_snapshot = MagicMock(return_value=snapshot)
    monitor.can_dispatch = MagicMock(return_value=True)

    worktree_mgr = MagicMock()
    merge_worker = MagicMock()
    runtime = MagicMock()

    return db, monitor, worktree_mgr, merge_worker, runtime


@pytest.mark.asyncio
class TestAllTasksDoneEvent:
    """Tests that pipeline:all_tasks_done is emitted with correct stats."""

    async def test_emits_all_tasks_done_when_all_done(self, tmp_path):
        """When all tasks are DONE, emits all_tasks_done with correct summary."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        tasks = [
            _make_task(TaskState.DONE.value, "task-1"),
            _make_task(TaskState.DONE.value, "task-2"),
        ]

        db, monitor, worktree_mgr, merge_worker, runtime = _make_minimal_execution_loop_mocks(tasks)

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, runtime, worktree_mgr, merge_worker, monitor, pipeline_id="pipe-abc"
                )

        event_types = [e[0] for e in emitted]
        assert "pipeline:all_tasks_done" in event_types

        atd_payload = next(e[1] for e in emitted if e[0] == "pipeline:all_tasks_done")
        assert atd_payload["summary"]["done"] == 2
        assert atd_payload["summary"]["error"] == 0
        assert atd_payload["summary"]["cancelled"] == 0
        assert atd_payload["summary"]["total"] == 2

    async def test_emits_all_tasks_done_mixed_terminal_states(self, tmp_path):
        """Mixed done/error/cancelled tasks produces correct summary counts."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        tasks = [
            _make_task(TaskState.DONE.value, "task-1"),
            _make_task(TaskState.ERROR.value, "task-2"),
            _make_task(TaskState.CANCELLED.value, "task-3"),
        ]

        db, monitor, worktree_mgr, merge_worker, runtime = _make_minimal_execution_loop_mocks(tasks)

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, runtime, worktree_mgr, merge_worker, monitor, pipeline_id="pipe-abc"
                )

        atd_events = [e for e in emitted if e[0] == "pipeline:all_tasks_done"]
        assert len(atd_events) == 1
        summary = atd_events[0][1]["summary"]
        assert summary["done"] == 1
        assert summary["error"] == 1
        assert summary["cancelled"] == 1
        assert summary["total"] == 3

    async def test_no_all_tasks_done_without_pipeline_id(self, tmp_path):
        """pipeline:all_tasks_done is not emitted when pipeline_id is None."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        tasks = [_make_task(TaskState.DONE.value, "task-1")]

        db = MagicMock()
        db.list_tasks = AsyncMock(return_value=tasks)
        db.list_agents = AsyncMock(return_value=[])
        db.log_event = AsyncMock()

        monitor = MagicMock()
        snapshot = MagicMock()
        monitor.take_snapshot = MagicMock(return_value=snapshot)
        monitor.can_dispatch = MagicMock(return_value=True)

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, pipeline_id=None
                )

        event_types = [e[0] for e in emitted]
        assert "pipeline:all_tasks_done" not in event_types


# ---------------------------------------------------------------------------
# Tests for pipeline pause tracking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPipelinePauseTracking:
    """Tests for pause tracking when all active tasks are awaiting_input."""

    async def test_emits_pipeline_paused_when_all_awaiting_input(self, tmp_path):
        """When all non-terminal tasks are awaiting_input, pipeline:paused is emitted."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        # Two tasks awaiting_input; after one iteration they become DONE to allow exit
        awaiting_tasks = [
            _make_task(TaskState.AWAITING_INPUT.value, "task-1"),
            _make_task(TaskState.AWAITING_INPUT.value, "task-2"),
        ]
        done_tasks = [
            _make_task(TaskState.DONE.value, "task-1"),
            _make_task(TaskState.DONE.value, "task-2"),
        ]

        call_count = 0

        async def list_tasks_side_effect(pid):
            nonlocal call_count
            call_count += 1
            # First call: awaiting_input; second call: done
            return awaiting_tasks if call_count == 1 else done_tasks

        db = MagicMock()
        db.list_tasks_by_pipeline = AsyncMock(side_effect=list_tasks_side_effect)
        db.list_agents = AsyncMock(return_value=[])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False, executor_token=None))
        db.log_event = AsyncMock()
        db.get_expired_questions = AsyncMock(return_value=[])
        db.set_pipeline_paused_at = AsyncMock()
        db.add_pipeline_paused_duration = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.set_executor_info = AsyncMock()
        db.clear_executor_info = AsyncMock()

        monitor = MagicMock()
        snapshot = MagicMock()
        monitor.take_snapshot = MagicMock(return_value=snapshot)
        monitor.can_dispatch = MagicMock(return_value=True)

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, pipeline_id="pipe-abc"
                )

        event_types = [e[0] for e in emitted]
        assert "pipeline:paused" in event_types

        pause_event = next(e[1] for e in emitted if e[0] == "pipeline:paused")
        assert pause_event["reason"] == "awaiting_input"
        assert pause_event["task_count"] == 2

        # paused_at should be set
        db.set_pipeline_paused_at.assert_called()

    async def test_pause_not_emitted_twice_for_same_pause_window(self, tmp_path):
        """pipeline:paused is only emitted once per continuous pause window."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.001)

        awaiting_tasks = [_make_task(TaskState.AWAITING_INPUT.value, "task-1")]
        done_tasks = [_make_task(TaskState.DONE.value, "task-1")]

        call_count = 0

        async def list_tasks_side_effect(pid):
            nonlocal call_count
            call_count += 1
            # First two calls: awaiting_input; then done
            return awaiting_tasks if call_count <= 2 else done_tasks

        db = MagicMock()
        db.list_tasks_by_pipeline = AsyncMock(side_effect=list_tasks_side_effect)
        db.list_agents = AsyncMock(return_value=[])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False, executor_token=None))
        db.log_event = AsyncMock()
        db.get_expired_questions = AsyncMock(return_value=[])
        db.set_pipeline_paused_at = AsyncMock()
        db.add_pipeline_paused_duration = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.set_executor_info = AsyncMock()
        db.clear_executor_info = AsyncMock()

        monitor = MagicMock()
        snapshot = MagicMock()
        monitor.take_snapshot = MagicMock(return_value=snapshot)
        monitor.can_dispatch = MagicMock(return_value=True)

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, pipeline_id="pipe-abc"
                )

        pause_events = [e for e in emitted if e[0] == "pipeline:paused"]
        assert len(pause_events) == 1

    async def test_resume_clears_paused_at_and_adds_duration(self, tmp_path):
        """When tasks resume from awaiting_input, paused_at is cleared and duration is accumulated.

        Scenario:
          Iteration 1: all tasks are awaiting_input → pause window starts
          Iteration 2: task transitions to in_progress → pause window ends (resume detected)
          Iteration 3: task is done → exit
        """
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.001)

        awaiting_tasks = [_make_task(TaskState.AWAITING_INPUT.value, "task-1")]
        # Task resumes to in_progress (answered question, restarted)
        inprogress_tasks = [_make_task(TaskState.IN_PROGRESS.value, "task-1")]
        done_tasks = [_make_task(TaskState.DONE.value, "task-1")]

        call_count = 0

        async def list_tasks_side_effect(pid):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return awaiting_tasks
            elif call_count == 2:
                return inprogress_tasks
            else:
                return done_tasks

        db = MagicMock()
        db.list_tasks_by_pipeline = AsyncMock(side_effect=list_tasks_side_effect)
        db.list_agents = AsyncMock(return_value=[])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False, executor_token=None))
        db.log_event = AsyncMock()
        db.get_expired_questions = AsyncMock(return_value=[])
        db.set_pipeline_paused_at = AsyncMock()
        db.add_pipeline_paused_duration = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.set_executor_info = AsyncMock()
        db.clear_executor_info = AsyncMock()

        daemon._emit = AsyncMock()

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor=MagicMock(
                        take_snapshot=MagicMock(return_value=MagicMock()),
                        can_dispatch=MagicMock(return_value=True),
                    ),
                    pipeline_id="pipe-abc"
                )

        # paused_at should have been set with a timestamp (pause started)
        # and then cleared to None (resume detected)
        set_calls = db.set_pipeline_paused_at.call_args_list
        paused_at_values = [c.args[1] for c in set_calls]
        # At least one call with a timestamp (set when paused)
        assert any(v is not None for v in paused_at_values), "paused_at should be set to a timestamp"
        # At least one call with None (cleared when resumed or on exit)
        assert None in paused_at_values, "paused_at should be cleared to None on resume"

        # Duration should have been added (either at resume point or at exit)
        db.add_pipeline_paused_duration.assert_called()
        duration_arg = db.add_pipeline_paused_duration.call_args.args[1]
        assert duration_arg >= 0  # non-negative elapsed time

    async def test_no_pause_tracking_without_pipeline_id(self, tmp_path):
        """Pause tracking is not active when pipeline_id is None."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        tasks = [_make_task(TaskState.DONE.value, "task-1")]

        db = MagicMock()
        db.list_tasks = AsyncMock(return_value=tasks)
        db.list_agents = AsyncMock(return_value=[])
        db.log_event = AsyncMock()
        db.set_pipeline_paused_at = AsyncMock()
        db.add_pipeline_paused_duration = AsyncMock()

        monitor = MagicMock()
        monitor.take_snapshot = MagicMock(return_value=MagicMock())
        monitor.can_dispatch = MagicMock(return_value=True)

        daemon._emit = AsyncMock()

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, pipeline_id=None
                )

        db.set_pipeline_paused_at.assert_not_called()
        db.add_pipeline_paused_duration.assert_not_called()

    async def test_no_pause_emitted_when_some_tasks_still_running(self, tmp_path):
        """pipeline:paused is NOT emitted when only some tasks are awaiting_input."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.001)

        # One awaiting_input, one in_progress — should NOT trigger pause
        mixed_tasks = [
            _make_task(TaskState.AWAITING_INPUT.value, "task-1"),
            _make_task(TaskState.IN_PROGRESS.value, "task-2"),
        ]
        done_tasks = [
            _make_task(TaskState.DONE.value, "task-1"),
            _make_task(TaskState.DONE.value, "task-2"),
        ]

        call_count = 0

        async def list_tasks_side_effect(pid):
            nonlocal call_count
            call_count += 1
            return mixed_tasks if call_count == 1 else done_tasks

        db = MagicMock()
        db.list_tasks_by_pipeline = AsyncMock(side_effect=list_tasks_side_effect)
        db.list_agents = AsyncMock(return_value=[])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False, executor_token=None))
        db.log_event = AsyncMock()
        db.get_expired_questions = AsyncMock(return_value=[])
        db.set_pipeline_paused_at = AsyncMock()
        db.add_pipeline_paused_duration = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.set_executor_info = AsyncMock()
        db.clear_executor_info = AsyncMock()

        monitor = MagicMock()
        monitor.take_snapshot = MagicMock(return_value=MagicMock())
        monitor.can_dispatch = MagicMock(return_value=True)

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, pipeline_id="pipe-abc"
                )

        pause_events = [e for e in emitted if e[0] == "pipeline:paused"]
        assert len(pause_events) == 0
        db.set_pipeline_paused_at.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for retry_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRetryTask:
    """Tests for ForgeDaemon.retry_task."""

    async def test_retry_task_resets_state_to_todo(self, tmp_path):
        """retry_task should update task state to 'todo' in DB."""
        daemon = _make_daemon(tmp_path)

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.log_event = AsyncMock()

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon.WorktreeManager") as MockWM:
            MockWM.return_value.remove = MagicMock()
            await daemon.retry_task("task-1", db, "pipe-abc")

        db.update_task_state.assert_called_once_with("task-1", "todo")

    async def test_retry_task_emits_state_changed(self, tmp_path):
        """retry_task should emit task:state_changed with state='todo'."""
        daemon = _make_daemon(tmp_path)

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.log_event = AsyncMock()

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon.WorktreeManager") as MockWM:
            MockWM.return_value.remove = MagicMock()
            await daemon.retry_task("task-1", db, "pipe-abc")

        assert len(emitted) == 1
        event_type, payload = emitted[0]
        assert event_type == "task:state_changed"
        assert payload["task_id"] == "task-1"
        assert payload["state"] == "todo"

    async def test_retry_task_clears_worktree(self, tmp_path):
        """retry_task should attempt to remove the worktree for the task."""
        daemon = _make_daemon(tmp_path)

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        with patch("forge.core.daemon.WorktreeManager") as MockWM:
            mock_wm_instance = MockWM.return_value
            mock_wm_instance.remove = MagicMock()
            await daemon.retry_task("task-1", db, "pipe-abc")
            mock_wm_instance.remove.assert_called_once_with("task-1")

    async def test_retry_task_worktree_remove_failure_does_not_raise(self, tmp_path):
        """If worktree removal fails, retry_task should not raise."""
        daemon = _make_daemon(tmp_path)

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        with patch("forge.core.daemon.WorktreeManager") as MockWM:
            mock_wm_instance = MockWM.return_value
            mock_wm_instance.remove = MagicMock(side_effect=RuntimeError("no worktree"))
            # Should not raise
            await daemon.retry_task("task-1", db, "pipe-abc")

        # State should still be reset
        db.update_task_state.assert_called_once_with("task-1", "todo")
        daemon._emit.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for run() using central DB path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRunCentralDB:
    """Tests that run() uses forge_db_url() instead of per-project DB."""

    async def test_run_uses_forge_db_url(self, tmp_path):
        """run() should call forge_db_url() to get the DB URL."""
        daemon = _make_daemon(tmp_path)

        mock_db = MagicMock()
        mock_db.initialize = AsyncMock()
        mock_db.create_pipeline = AsyncMock()
        mock_db.close = AsyncMock()

        with patch("forge.core.daemon.Database", return_value=mock_db) as MockDB, \
             patch("forge.core.paths.forge_db_url", return_value="sqlite+aiosqlite:///central/forge.db") as mock_url, \
             patch.object(daemon, "plan", new_callable=AsyncMock) as mock_plan, \
             patch.object(daemon, "generate_contracts", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(daemon, "execute", new_callable=AsyncMock), \
             patch("forge.core.daemon.check_budget", new_callable=AsyncMock):
            mock_plan.return_value = MagicMock(tasks=[])
            await daemon.run("test task")

        mock_url.assert_called_once()
        MockDB.assert_called_once_with("sqlite+aiosqlite:///central/forge.db")

    async def test_run_passes_project_path_to_create_pipeline(self, tmp_path):
        """run() should pass project_path and project_name to create_pipeline."""
        daemon = _make_daemon(tmp_path)

        mock_db = MagicMock()
        mock_db.initialize = AsyncMock()
        mock_db.create_pipeline = AsyncMock()
        mock_db.close = AsyncMock()

        with patch("forge.core.daemon.Database", return_value=mock_db), \
             patch("forge.core.paths.forge_db_url", return_value="sqlite+aiosqlite:///test.db"), \
             patch.object(daemon, "plan", new_callable=AsyncMock) as mock_plan, \
             patch.object(daemon, "generate_contracts", new_callable=AsyncMock, return_value=MagicMock()), \
             patch.object(daemon, "execute", new_callable=AsyncMock), \
             patch("forge.core.daemon.check_budget", new_callable=AsyncMock):
            mock_plan.return_value = MagicMock(tasks=[])
            await daemon.run("test task")

        call_kwargs = mock_db.create_pipeline.call_args.kwargs
        assert call_kwargs["project_path"] == str(tmp_path)
        assert call_kwargs["project_name"] == os.path.basename(str(tmp_path))


class TestClassifyPipelineResult:
    def test_classify_all_done(self):
        states = ["done", "done", "done"]
        assert _classify_pipeline_result(states) == "complete"

    def test_classify_all_error(self):
        states = ["error", "error"]
        assert _classify_pipeline_result(states) == "error"

    def test_classify_mixed(self):
        states = ["done", "done", "error", "blocked"]
        assert _classify_pipeline_result(states) == "partial_success"

    def test_classify_with_cancelled_excluded(self):
        states = ["done", "done", "cancelled"]
        assert _classify_pipeline_result(states) == "complete"

    def test_classify_done_and_blocked(self):
        states = ["done", "blocked"]
        assert _classify_pipeline_result(states) == "partial_success"

    def test_classify_all_cancelled(self):
        states = ["cancelled", "cancelled"]
        assert _classify_pipeline_result(states) == "complete"


# ---------------------------------------------------------------------------
# Tests for planning question wiring in daemon.plan()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPlanningQuestionWiring:
    """Tests for on_question callback wiring in daemon.plan()."""

    async def test_planning_pipeline_receives_on_question(self, tmp_path):
        """When deep planning is used, PlanningPipeline should receive on_question callback."""
        daemon = _make_daemon(tmp_path, planning_mode="deep")

        db = MagicMock()
        db.get_pipeline = AsyncMock(return_value=MagicMock(template_config_json=None))
        db.create_task_question = AsyncMock(return_value=MagicMock(id="q-1"))
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        with patch("forge.core.daemon.gather_project_snapshot", return_value=MagicMock(
            total_files=100,
            format_for_planner=MagicMock(return_value="snapshot"),
        )), \
             patch("forge.core.daemon.select_model", return_value="claude-sonnet-4-20250514"), \
             patch("forge.core.daemon._should_use_deep_planning", return_value=True), \
             patch("forge.core.planning.pipeline.PlanningPipeline") as MockPipeline, \
             patch("forge.core.planning.scout.Scout"), \
             patch("forge.core.planning.architect.Architect"), \
             patch("forge.core.planning.detailer.DetailerFactory"):

            MockPipeline.return_value.run = AsyncMock(return_value=MagicMock(
                task_graph=MagicMock(tasks=[]),
                codebase_map=None,
                cost_breakdown={},
                total_cost_usd=0.0,
            ))

            graph = await daemon.plan("add auth", db, pipeline_id="pipe-1")

        # Verify PlanningPipeline was constructed with on_question
        init_kwargs = MockPipeline.call_args.kwargs
        assert "on_question" in init_kwargs
        assert init_kwargs["on_question"] is not None
        assert callable(init_kwargs["on_question"])

    async def test_planning_answer_listener_cleaned_up(self, tmp_path):
        """After plan() completes, planning:answer listener should be removed."""
        daemon = _make_daemon(tmp_path, planning_mode="deep")

        db = MagicMock()
        db.get_pipeline = AsyncMock(return_value=MagicMock(template_config_json=None))
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        with patch("forge.core.daemon.gather_project_snapshot", return_value=MagicMock(
            total_files=100,
            format_for_planner=MagicMock(return_value="snapshot"),
        )), \
             patch("forge.core.daemon.select_model", return_value="claude-sonnet-4-20250514"), \
             patch("forge.core.daemon._should_use_deep_planning", return_value=True), \
             patch("forge.core.planning.pipeline.PlanningPipeline") as MockPipeline, \
             patch("forge.core.planning.scout.Scout"), \
             patch("forge.core.planning.architect.Architect"), \
             patch("forge.core.planning.detailer.DetailerFactory"):

            MockPipeline.return_value.run = AsyncMock(return_value=MagicMock(
                task_graph=MagicMock(tasks=[]),
                codebase_map=None,
                cost_breakdown={},
                total_cost_usd=0.0,
            ))

            await daemon.plan("add auth", db, pipeline_id="pipe-1")

        # planning:answer handlers should be cleaned up
        handlers = daemon._events._handlers.get("planning:answer", [])
        assert len(handlers) == 0
