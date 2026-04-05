"""Tests for ForgeDaemon: pause tracking, all_tasks_done event, question timeout checker."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon, _classify_pipeline_result, _detect_excluded_repos
from forge.core.errors import ForgeError
from forge.core.models import RepoConfig, TaskState

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
    t.repo_id = "default"
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
    monitor.take_snapshot = AsyncMock(return_value=snapshot)
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
        monitor.take_snapshot = AsyncMock(return_value=snapshot)
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
        monitor.take_snapshot = AsyncMock(return_value=snapshot)
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
        monitor.take_snapshot = AsyncMock(return_value=snapshot)
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
                    db,
                    MagicMock(),
                    MagicMock(),
                    MagicMock(),
                    monitor=MagicMock(
                        take_snapshot=AsyncMock(return_value=MagicMock()),
                        can_dispatch=MagicMock(return_value=True),
                    ),
                    pipeline_id="pipe-abc",
                )

        # paused_at should have been set with a timestamp (pause started)
        # and then cleared to None (resume detected)
        set_calls = db.set_pipeline_paused_at.call_args_list
        paused_at_values = [c.args[1] for c in set_calls]
        # At least one call with a timestamp (set when paused)
        assert any(v is not None for v in paused_at_values), (
            "paused_at should be set to a timestamp"
        )
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
        monitor.take_snapshot = AsyncMock(return_value=MagicMock())
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
        monitor.take_snapshot = AsyncMock(return_value=MagicMock())
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

    async def test_polls_answered_questions_while_waiting_for_input(self, tmp_path):
        """Execution loop should retry answered FORGE_QUESTION tasks without a restart."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        awaiting_tasks = [_make_task(TaskState.AWAITING_INPUT.value, "task-1")]
        done_tasks = [_make_task(TaskState.DONE.value, "task-1")]

        call_count = 0

        async def list_tasks_side_effect(pid):
            nonlocal call_count
            call_count += 1
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
        monitor.take_snapshot = AsyncMock(return_value=snapshot)
        monitor.can_dispatch = MagicMock(return_value=True)

        daemon._emit = AsyncMock()
        daemon._recover_answered_questions = AsyncMock()

        with patch("forge.core.daemon._print_status_table"):
            with patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]):
                await daemon._execution_loop(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, pipeline_id="pipe-abc"
                )

        assert daemon._recover_answered_questions.await_count >= 1
        daemon._recover_answered_questions.assert_any_await(db, "pipe-abc")


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
        db.get_task = AsyncMock(return_value=_make_task("error", "task-1"))
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
        db.get_task = AsyncMock(return_value=_make_task("error", "task-1"))
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
        db.get_task = AsyncMock(return_value=_make_task("error", "task-1"))
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
        db.get_task = AsyncMock(return_value=_make_task("error", "task-1"))
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

    async def test_retry_task_rejects_non_retryable_state(self, tmp_path):
        """retry_task should return early if task is not in error/cancelled state."""
        daemon = _make_daemon(tmp_path)

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=_make_task("in_progress", "task-1"))
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        await daemon.retry_task("task-1", db, "pipe-abc")

        # Should NOT have updated state or emitted events
        db.update_task_state.assert_not_called()
        daemon._emit.assert_not_called()

    async def test_retry_task_allows_cancelled_state(self, tmp_path):
        """retry_task should proceed for cancelled tasks."""
        daemon = _make_daemon(tmp_path)

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=_make_task("cancelled", "task-1"))
        db.log_event = AsyncMock()

        emitted: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        with patch("forge.core.daemon.WorktreeManager") as MockWM:
            MockWM.return_value.remove = MagicMock()
            await daemon.retry_task("task-1", db, "pipe-abc")

        db.update_task_state.assert_called_once_with("task-1", "todo")
        assert any(ev[0] == "task:state_changed" for ev in emitted)


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

        with (
            patch("forge.core.daemon.Database", return_value=mock_db) as MockDB,
            patch(
                "forge.core.paths.forge_db_url", return_value="sqlite+aiosqlite:///central/forge.db"
            ) as mock_url,
            patch.object(daemon, "plan", new_callable=AsyncMock) as mock_plan,
            patch.object(
                daemon, "generate_contracts", new_callable=AsyncMock, return_value=MagicMock()
            ),
            patch.object(daemon, "execute", new_callable=AsyncMock),
            patch("forge.core.daemon.check_budget", new_callable=AsyncMock),
        ):
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

        with (
            patch("forge.core.daemon.Database", return_value=mock_db),
            patch("forge.core.paths.forge_db_url", return_value="sqlite+aiosqlite:///test.db"),
            patch.object(daemon, "plan", new_callable=AsyncMock) as mock_plan,
            patch.object(
                daemon, "generate_contracts", new_callable=AsyncMock, return_value=MagicMock()
            ),
            patch.object(daemon, "execute", new_callable=AsyncMock),
            patch("forge.core.daemon.check_budget", new_callable=AsyncMock),
        ):
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

    def test_classify_all_blocked(self):
        """All tasks blocked with none done → error."""
        states = ["blocked", "blocked"]
        assert _classify_pipeline_result(states) == "error"

    def test_classify_blocked_and_done(self):
        """Mix of blocked and done → partial_success."""
        states = ["done", "done", "blocked"]
        assert _classify_pipeline_result(states) == "partial_success"

    def test_classify_blocked_done_cancelled(self):
        """Cancelled excluded; remaining blocked+done → partial_success."""
        states = ["done", "blocked", "cancelled"]
        assert _classify_pipeline_result(states) == "partial_success"


class TestDetectExcludedRepos:
    """Tests for _detect_excluded_repos word-boundary matching."""

    def test_exact_match(self):
        result = _detect_excluded_repos("skip web", {"web"})
        assert result == {"web"}

    def test_no_substring_match(self):
        """'web' should NOT match 'webutils' (word boundary)."""
        result = _detect_excluded_repos("skip webutils", {"web"})
        assert result == set()

    def test_substring_repo_not_falsely_excluded(self):
        """Repo 'web' should NOT match inside 'webutils' (no word boundary)."""
        result = _detect_excluded_repos("ignore webutils", {"web"})
        assert result == set()

    def test_multiple_repos(self):
        result = _detect_excluded_repos("ignore web, skip api", {"web", "api", "core"})
        assert result == {"web", "api"}

    def test_no_exclude_keyword(self):
        result = _detect_excluded_repos("work on web", {"web"})
        assert result == set()


# ---------------------------------------------------------------------------
# Tests for planning question wiring in daemon.plan()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPlanningQuestionWiring:
    """Tests for on_question callback wiring in daemon.plan()."""

    async def test_unified_planner_receives_on_question(self, tmp_path):
        """When deep planning is used, UnifiedPlanner.run() should receive on_question callback."""
        daemon = _make_daemon(tmp_path, planning_mode="deep")

        db = MagicMock()
        db.get_pipeline = AsyncMock(return_value=MagicMock(template_config_json=None))
        db.create_task_question = AsyncMock(return_value=MagicMock(id="q-1"))
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        with (
            patch(
                "forge.core.daemon.gather_project_snapshot",
                return_value=MagicMock(
                    total_files=100,
                    format_for_planner=MagicMock(return_value="snapshot"),
                ),
            ),
            patch("forge.core.daemon.select_model", return_value="claude-sonnet-4-20250514"),
            patch("forge.core.daemon._should_use_deep_planning", return_value=True),
            patch("forge.core.planning.unified_planner.UnifiedPlanner") as MockPlanner,
        ):
            MockPlanner.return_value.run = AsyncMock(
                return_value=MagicMock(
                    task_graph=MagicMock(tasks=[]),
                    cost_breakdown={"planner": 0.0},
                    total_cost_usd=0.0,
                )
            )

            await daemon.plan("add auth", db, pipeline_id="pipe-1")

        # Verify UnifiedPlanner.run() was called with on_question
        run_kwargs = MockPlanner.return_value.run.call_args.kwargs
        assert "on_question" in run_kwargs
        assert run_kwargs["on_question"] is not None
        assert callable(run_kwargs["on_question"])

    async def test_planning_answer_listener_cleaned_up(self, tmp_path):
        """After plan() completes, planning:answer listener should be removed."""
        daemon = _make_daemon(tmp_path, planning_mode="deep")

        db = MagicMock()
        db.get_pipeline = AsyncMock(return_value=MagicMock(template_config_json=None))
        db.log_event = AsyncMock()

        daemon._emit = AsyncMock()

        with (
            patch(
                "forge.core.daemon.gather_project_snapshot",
                return_value=MagicMock(
                    total_files=100,
                    format_for_planner=MagicMock(return_value="snapshot"),
                ),
            ),
            patch("forge.core.daemon.select_model", return_value="claude-sonnet-4-20250514"),
            patch("forge.core.daemon._should_use_deep_planning", return_value=True),
            patch("forge.core.planning.unified_planner.UnifiedPlanner") as MockPlanner,
        ):
            MockPlanner.return_value.run = AsyncMock(
                return_value=MagicMock(
                    task_graph=MagicMock(tasks=[]),
                    cost_breakdown={"planner": 0.0},
                    total_cost_usd=0.0,
                )
            )

            await daemon.plan("add auth", db, pipeline_id="pipe-1")

        # planning:answer handlers should be cleaned up
        handlers = daemon._events._handlers.get("planning:answer", [])
        assert len(handlers) == 0


# ---------------------------------------------------------------------------
# Helper: build a mock CompletedProcess for async_subprocess
# ---------------------------------------------------------------------------


def _mock_completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Tests for _preflight_checks (async_subprocess mocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPreflightChecks:
    """Verify _preflight_checks uses async_subprocess correctly."""

    async def test_all_checks_pass(self, tmp_path):
        """Happy path: valid git repo, has commits, has remote, gh authed."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.update_pipeline_status = AsyncMock()
        db.log_event = AsyncMock()
        daemon._emit = AsyncMock()

        async_sub = AsyncMock(
            side_effect=[
                _mock_completed(0, "true\n"),  # git rev-parse --is-inside-work-tree
                _mock_completed(0, "abc123\n"),  # git rev-parse HEAD
                _mock_completed(0, "origin\n"),  # git remote
                _mock_completed(0, ""),  # gh auth status
            ]
        )

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value="/usr/bin/gh"),
        ):
            result = await daemon._preflight_checks(str(tmp_path), db, "pipe-1")

        assert result is True
        assert async_sub.call_count == 4
        db.update_pipeline_status.assert_not_called()

    async def test_not_a_git_repo_fails(self, tmp_path):
        """Non-git directory causes preflight failure."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.update_pipeline_status = AsyncMock()
        db.log_event = AsyncMock()
        daemon._emit = AsyncMock()

        async_sub = AsyncMock(
            side_effect=[
                _mock_completed(
                    128, "", "fatal: not a git repo"
                ),  # git rev-parse --is-inside-work-tree
                _mock_completed(0, "abc123\n"),  # git rev-parse HEAD
                _mock_completed(0, "origin\n"),  # git remote
            ]
        )

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value=None),
        ):
            result = await daemon._preflight_checks(str(tmp_path), db, "pipe-1")

        assert result is False
        db.update_pipeline_status.assert_called_once_with("pipe-1", "error")
        daemon._emit.assert_called()

    async def test_empty_repo_creates_initial_commit(self, tmp_path):
        """Empty repo (no HEAD) triggers initial commit via async_subprocess."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.update_pipeline_status = AsyncMock()
        db.log_event = AsyncMock()
        daemon._emit = AsyncMock()

        async_sub = AsyncMock(
            side_effect=[
                _mock_completed(0, "true\n"),  # git rev-parse --is-inside-work-tree
                _mock_completed(128, "", "fatal"),  # git rev-parse HEAD — no commits
                _mock_completed(0, ""),  # git commit --allow-empty
                _mock_completed(0, "origin\n"),  # git remote
            ]
        )

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value=None),
        ):
            result = await daemon._preflight_checks(str(tmp_path), db, "pipe-1")

        assert result is True
        # Third call should be the commit
        commit_call = async_sub.call_args_list[2]
        assert commit_call.args[0][0] == "git"
        assert "--allow-empty" in commit_call.args[0]

    async def test_no_remote_is_warning_not_failure(self, tmp_path):
        """Missing git remote produces a warning but preflight still passes."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.update_pipeline_status = AsyncMock()
        db.log_event = AsyncMock()
        daemon._emit = AsyncMock()

        async_sub = AsyncMock(
            side_effect=[
                _mock_completed(0, "true\n"),  # git rev-parse --is-inside-work-tree
                _mock_completed(0, "abc123\n"),  # git rev-parse HEAD
                _mock_completed(0, ""),  # git remote — empty stdout
            ]
        )

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value=None),
        ):
            result = await daemon._preflight_checks(str(tmp_path), db, "pipe-1")

        assert result is True

    async def test_gh_not_authed_is_warning_not_failure(self, tmp_path):
        """gh CLI auth failure produces a warning but preflight still passes."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.update_pipeline_status = AsyncMock()
        db.log_event = AsyncMock()
        daemon._emit = AsyncMock()

        async_sub = AsyncMock(
            side_effect=[
                _mock_completed(0, "true\n"),  # git rev-parse --is-inside-work-tree
                _mock_completed(0, "abc123\n"),  # git rev-parse HEAD
                _mock_completed(0, "origin\n"),  # git remote
                _mock_completed(1, "", "not logged in"),  # gh auth status — fails
            ]
        )

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value="/usr/bin/gh"),
        ):
            result = await daemon._preflight_checks(str(tmp_path), db, "pipe-1")

        assert result is True


# ---------------------------------------------------------------------------
# Tests for execute() branch creation (async_subprocess mocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteBranchCreation:
    """Verify execute() creates pipeline branches via async_subprocess."""

    async def test_creates_branch_on_fresh_run(self, tmp_path):
        """On a fresh (non-resume) run, execute() creates the pipeline branch."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        # Use AsyncMock as spec_set-free base so ANY db.method() is awaitable
        db = AsyncMock()
        db.get_pipeline = AsyncMock(
            return_value=MagicMock(
                paused=False,
                executor_token=None,
                base_branch="main",
                branch_name=None,
                description="test pipeline",
            )
        )
        db.list_tasks_by_pipeline = AsyncMock(
            return_value=[
                _make_task(TaskState.DONE.value, "task-1"),
            ]
        )
        db.list_agents = AsyncMock(return_value=[])
        db.get_expired_questions = AsyncMock(return_value=[])

        async_sub = AsyncMock(
            side_effect=[
                # _preflight_checks calls (5 max)
                _mock_completed(0, "true\n"),  # git rev-parse --is-inside-work-tree
                _mock_completed(0, "abc123\n"),  # git rev-parse HEAD
                _mock_completed(0, "origin\n"),  # git remote
                _mock_completed(0),  # gh auth status
            ]
        )

        daemon._emit = AsyncMock()
        create_branches_mock = AsyncMock()

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value="/usr/bin/gh"),
            patch(
                "forge.core.daemon._get_current_branch", new_callable=AsyncMock, return_value="main"
            ),
            patch(
                "forge.core.daemon._generate_branch_name",
                new_callable=AsyncMock,
                return_value="forge/test-branch",
            ),
            patch("forge.core.daemon._print_status_table"),
            patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]),
            patch("forge.core.daemon.WorktreeManager"),
            patch("forge.core.daemon.MergeWorker"),
            patch("forge.core.daemon.ResourceMonitor") as MockMon,
            patch.object(daemon, "_init_repos", new_callable=AsyncMock),
            patch.object(daemon, "_create_pipeline_branches", create_branches_mock),
        ):
            MockMon.return_value.take_snapshot = AsyncMock(return_value=MagicMock())
            MockMon.return_value.can_dispatch = MagicMock(return_value=True)

            plan = MagicMock(tasks=[_make_task(TaskState.TODO.value, "task-1")])
            await daemon.execute(plan, db, "pipe-1", resume=False)

        # Verify _create_pipeline_branches was called for branch creation
        create_branches_mock.assert_called_once()

    async def test_resume_verifies_branch_exists(self, tmp_path):
        """On resume, execute() checks if the pipeline branch exists."""
        daemon = _make_daemon(tmp_path, pipeline_timeout_seconds=0, scheduler_poll_interval=0.01)

        db = MagicMock()
        db.initialize = AsyncMock()
        db = AsyncMock()
        db.get_pipeline = AsyncMock(
            return_value=MagicMock(
                paused=False,
                executor_token=None,
                base_branch="main",
                branch_name="forge/existing-branch",
                description="test pipeline",
            )
        )
        db.list_tasks_by_pipeline = AsyncMock(
            return_value=[
                _make_task(TaskState.DONE.value, "task-1"),
            ]
        )
        db.list_agents = AsyncMock(return_value=[])
        db.get_expired_questions = AsyncMock(return_value=[])

        async_sub = AsyncMock(
            side_effect=[
                # _preflight_checks calls
                _mock_completed(0, "true\n"),
                _mock_completed(0, "abc123\n"),
                _mock_completed(0, "origin\n"),
                _mock_completed(0),
                # execute() branch verification (resume path)
                _mock_completed(0, "abc123\n"),  # git rev-parse --verify — branch exists
            ]
        )

        daemon._emit = AsyncMock()

        with (
            patch("forge.core.daemon.async_subprocess", async_sub),
            patch("forge.core.daemon.shutil.which", return_value="/usr/bin/gh"),
            patch(
                "forge.core.daemon._get_current_branch", new_callable=AsyncMock, return_value="main"
            ),
            patch("forge.core.daemon._print_status_table"),
            patch("forge.core.daemon.Scheduler.dispatch_plan", return_value=[]),
            patch("forge.core.daemon.WorktreeManager"),
            patch("forge.core.daemon.MergeWorker"),
            patch("forge.core.daemon.ResourceMonitor") as MockMon,
            patch.object(daemon, "_init_repos", new_callable=AsyncMock),
        ):
            MockMon.return_value.take_snapshot = AsyncMock(return_value=MagicMock())
            MockMon.return_value.can_dispatch = MagicMock(return_value=True)

            plan = MagicMock(tasks=[_make_task(TaskState.TODO.value, "task-1")])
            await daemon.execute(plan, db, "pipe-1", resume=True)

        # Should have called rev-parse --verify for the branch check
        verify_calls = [
            c for c in async_sub.call_args_list if len(c.args[0]) >= 3 and "--verify" in c.args[0]
        ]
        assert len(verify_calls) == 1


# ---------------------------------------------------------------------------
# Helpers for multi-repo tests
# ---------------------------------------------------------------------------


def _init_git_repo(path, branch: str = "main") -> None:
    """Initialize a real git repo at *path* with one commit."""
    subprocess.run(["git", "init", "-b", branch], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(path), check=True, capture_output=True
    )
    # Create initial commit so HEAD is valid
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Chunk 1: Multi-Repo Init Tests
# ---------------------------------------------------------------------------


class TestDaemonMultiRepoInit:
    """Tests for ForgeDaemon.__init__ with repos parameter."""

    def test_daemon_init_single_repo_default(self, tmp_path):
        """Without repos param, daemon creates single 'default' repo entry."""
        daemon = _make_daemon(tmp_path)
        assert hasattr(daemon, "_repos")
        assert "default" in daemon._repos
        assert daemon._repos["default"].id == "default"
        assert daemon._repos["default"].path == str(tmp_path)
        assert daemon._repos["default"].base_branch == ""  # resolved async later

    def test_daemon_init_with_repos(self, tmp_path):
        """With repos param, daemon stores each RepoConfig keyed by id."""
        repo_a = RepoConfig(id="backend", path=str(tmp_path / "back"), base_branch="main")
        repo_b = RepoConfig(id="frontend", path=str(tmp_path / "front"), base_branch="develop")

        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=[repo_a, repo_b],
        )

        assert len(daemon._repos) == 2
        assert daemon._repos["backend"] is repo_a
        assert daemon._repos["frontend"] is repo_b

    def test_daemon_init_workspace_dir_alias(self, tmp_path):
        """_workspace_dir is set to the same value as _project_dir."""
        daemon = _make_daemon(tmp_path)
        assert daemon._workspace_dir == daemon._project_dir
        assert daemon._workspace_dir == str(tmp_path)


@pytest.mark.asyncio
class TestInitRepos:
    """Tests for ForgeDaemon._init_repos() async method."""

    async def test_init_repos_resolves_default_single_repo(self, tmp_path):
        """For single default repo with empty base_branch, resolves via _get_current_branch."""
        _init_git_repo(tmp_path, "main")
        daemon = _make_daemon(tmp_path)

        assert daemon._repos["default"].base_branch == ""

        with patch(
            "forge.core.daemon._get_current_branch", new_callable=AsyncMock, return_value="main"
        ):
            await daemon._init_repos()

        assert daemon._repos["default"].base_branch == "main"

    async def test_init_repos_resolves_base_branch(self, tmp_path):
        """For multi-repo with empty base_branch, resolves each repo's branch."""
        back_dir = tmp_path / "back"
        front_dir = tmp_path / "front"
        back_dir.mkdir()
        front_dir.mkdir()
        _init_git_repo(back_dir, "main")
        _init_git_repo(front_dir, "develop")

        repos = [
            RepoConfig(id="backend", path=str(back_dir), base_branch=""),
            RepoConfig(id="frontend", path=str(front_dir), base_branch="develop"),
        ]

        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )

        async def mock_get_branch(repo_path):
            if "back" in repo_path:
                return "main"
            return "develop"

        with patch("forge.core.daemon._get_current_branch", side_effect=mock_get_branch):
            await daemon._init_repos()

        assert daemon._repos["backend"].base_branch == "main"
        assert daemon._repos["frontend"].base_branch == "develop"

    async def test_init_repos_staged_changes_raises(self, tmp_path):
        """If a repo has staged changes, _init_repos raises ForgeError."""
        _init_git_repo(tmp_path, "main")
        # Create and stage a file (but don't commit)
        (tmp_path / "dirty.txt").write_text("dirty")
        subprocess.run(
            ["git", "add", "dirty.txt"], cwd=str(tmp_path), check=True, capture_output=True
        )

        daemon = _make_daemon(tmp_path)

        with pytest.raises(ForgeError, match="staged"):
            await daemon._init_repos()

    async def test_init_repos_skips_already_resolved(self, tmp_path):
        """If base_branch is already set, _init_repos does not overwrite it."""
        _init_git_repo(tmp_path, "main")
        repos = [RepoConfig(id="backend", path=str(tmp_path), base_branch="release")]

        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )

        # Should not call _get_current_branch for this repo
        with patch("forge.core.daemon._get_current_branch", new_callable=AsyncMock) as mock_branch:
            await daemon._init_repos()

        mock_branch.assert_not_called()
        assert daemon._repos["backend"].base_branch == "release"


# ---------------------------------------------------------------------------
# Chunk 2: Per-Repo Infrastructure Tests
# ---------------------------------------------------------------------------


class TestDaemonPerRepoInfra:
    """Tests for _setup_per_repo_infra and _create_pipeline_branches."""

    def test_setup_per_repo_infra_single_repo(self, tmp_path):
        """Single default repo creates flat worktree layout."""
        daemon = _make_daemon(tmp_path)
        # Manually set base_branch so it's valid
        daemon._repos["default"] = RepoConfig(
            id="default",
            path=str(tmp_path),
            base_branch="main",
        )

        daemon._setup_per_repo_infra("forge/pipeline-pipe-abc")

        assert "default" in daemon._worktree_managers
        assert "default" in daemon._merge_workers
        assert "default" in daemon._pipeline_branches
        assert daemon._pipeline_branches["default"] == "forge/pipeline-pipe-abc"

    def test_setup_per_repo_infra_multi_repo(self, tmp_path):
        """Multi-repo creates nested worktree layout per repo_id."""
        back_dir = tmp_path / "back"
        front_dir = tmp_path / "front"
        back_dir.mkdir()
        front_dir.mkdir()

        repos = [
            RepoConfig(id="backend", path=str(back_dir), base_branch="main"),
            RepoConfig(id="frontend", path=str(front_dir), base_branch="develop"),
        ]

        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )

        with patch("forge.core.daemon.WorktreeManager"), patch("forge.core.daemon.MergeWorker"):
            daemon._setup_per_repo_infra("forge/pipeline-pipe-xyz")

        assert "backend" in daemon._worktree_managers
        assert "frontend" in daemon._worktree_managers
        assert "backend" in daemon._merge_workers
        assert "frontend" in daemon._merge_workers

    def test_daemon_init_with_repos_creates_managers(self, tmp_path):
        """Alias test: verifying per-repo infra dict structure."""
        daemon = _make_daemon(tmp_path)
        daemon._repos["default"] = RepoConfig(
            id="default",
            path=str(tmp_path),
            base_branch="main",
        )
        daemon._setup_per_repo_infra("forge/pipeline-pipe-123")

        assert isinstance(daemon._worktree_managers, dict)
        assert isinstance(daemon._merge_workers, dict)
        assert isinstance(daemon._pipeline_branches, dict)

    def test_pipeline_branches_created_per_repo(self, tmp_path):
        """Each repo gets its own pipeline branch name."""
        repos = [
            RepoConfig(id="backend", path=str(tmp_path / "b"), base_branch="main"),
            RepoConfig(id="frontend", path=str(tmp_path / "f"), base_branch="develop"),
        ]
        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )

        with patch("forge.core.daemon.WorktreeManager"), patch("forge.core.daemon.MergeWorker"):
            daemon._setup_per_repo_infra("forge/pipeline-abc12345")

        assert daemon._pipeline_branches["backend"] == "forge/pipeline-abc12345"
        assert daemon._pipeline_branches["frontend"] == "forge/pipeline-abc12345"


# ---------------------------------------------------------------------------
# Chunk 2: Worktree Path Tests
# ---------------------------------------------------------------------------


class TestWorktreePath:
    """Tests for ForgeDaemon._worktree_path."""

    def test_worktree_path_single_repo(self, tmp_path):
        """Single default repo uses flat layout: .forge/worktrees/<task_id>."""
        daemon = _make_daemon(tmp_path)

        result = daemon._worktree_path("default", "task-1")
        expected = os.path.join(str(tmp_path), ".forge", "worktrees", "task-1")
        assert result == expected

    def test_worktree_path_multi_repo(self, tmp_path):
        """Multi-repo creates worktrees inside each repo's own .forge/ directory."""
        repos = [
            RepoConfig(id="backend", path=str(tmp_path / "b"), base_branch="main"),
            RepoConfig(id="frontend", path=str(tmp_path / "f"), base_branch="develop"),
        ]
        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )

        result = daemon._worktree_path("backend", "task-1")
        expected = os.path.join(str(tmp_path / "b"), ".forge", "worktrees", "task-1")
        assert result == expected


# ---------------------------------------------------------------------------
# Chunk 4: Task Dispatch Routing Tests
# ---------------------------------------------------------------------------


class TestDispatchTaskRouting:
    """Tests for _get_repo_infra routing."""

    def test_dispatch_task_routes_to_correct_repo(self, tmp_path):
        """_get_repo_infra returns the correct infrastructure tuple."""
        daemon = _make_daemon(tmp_path)
        daemon._repos["default"] = RepoConfig(
            id="default",
            path=str(tmp_path),
            base_branch="main",
        )
        daemon._setup_per_repo_infra("forge/pipeline-pipe-abc")

        wt_mgr, merge_worker, branch = daemon._get_repo_infra("default")

        assert wt_mgr is daemon._worktree_managers["default"]
        assert merge_worker is daemon._merge_workers["default"]
        assert branch == "forge/pipeline-pipe-abc"

    def test_dispatch_task_unknown_repo_raises(self, tmp_path):
        """_get_repo_infra raises ForgeError for unknown repo_id."""
        daemon = _make_daemon(tmp_path)
        daemon._repos["default"] = RepoConfig(
            id="default",
            path=str(tmp_path),
            base_branch="main",
        )
        daemon._setup_per_repo_infra("forge/pipeline-pipe-abc")

        with pytest.raises(ForgeError, match="Unknown repo"):
            daemon._get_repo_infra("nonexistent")


# ---------------------------------------------------------------------------
# Chunk 5: Allowed Dirs & Repos JSON Tests
# ---------------------------------------------------------------------------


class TestAllowedDirs:
    """Tests for _build_allowed_dirs."""

    def test_allowed_dirs_union(self, tmp_path):
        """_build_allowed_dirs returns union of settings.allowed_dirs + repo paths."""
        extra_dir = str(tmp_path / "extra")
        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(allowed_dirs=[extra_dir]),
        )

        dirs = daemon._build_allowed_dirs()

        assert str(tmp_path) in dirs
        assert extra_dir in dirs

    def test_allowed_dirs_multi_repo(self, tmp_path):
        """Multi-repo includes all repo paths."""
        back_dir = str(tmp_path / "back")
        front_dir = str(tmp_path / "front")

        repos = [
            RepoConfig(id="backend", path=back_dir, base_branch="main"),
            RepoConfig(id="frontend", path=front_dir, base_branch="develop"),
        ]
        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )

        dirs = daemon._build_allowed_dirs()
        assert back_dir in dirs
        assert front_dir in dirs


class TestReposJsonStorage:
    """Tests for _build_repos_json."""

    def test_repos_json_single_repo_returns_none(self, tmp_path):
        """Single-repo returns None (no need to store)."""
        daemon = _make_daemon(tmp_path)
        daemon._repos["default"] = RepoConfig(
            id="default",
            path=str(tmp_path),
            base_branch="main",
        )
        daemon._pipeline_branches = {"default": "forge/pipeline-abc"}

        result = daemon._build_repos_json()
        assert result is None

    def test_repos_json_stored_in_pipeline(self, tmp_path):
        """Multi-repo returns JSON string with repo info."""
        import json

        repos = [
            RepoConfig(id="backend", path=str(tmp_path / "b"), base_branch="main"),
            RepoConfig(id="frontend", path=str(tmp_path / "f"), base_branch="develop"),
        ]
        daemon = ForgeDaemon(
            project_dir=str(tmp_path),
            settings=ForgeSettings(),
            repos=repos,
        )
        daemon._pipeline_branches = {
            "backend": "forge/pipeline-abc",
            "frontend": "forge/pipeline-abc",
        }

        result = daemon._build_repos_json()
        assert result is not None
        data = json.loads(result)
        assert isinstance(data, list)
        by_id = {entry["id"]: entry for entry in data}
        assert "backend" in by_id
        assert "frontend" in by_id
        assert by_id["backend"]["path"] == str(tmp_path / "b")


# ---------------------------------------------------------------------------
# Tests for _preflight_checks with multi-repo workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_checks_multi_repo(tmp_path):
    """Daemon preflight passes for a multi-repo workspace (plain wrapper dir)."""
    import subprocess

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon
    from forge.core.models import RepoConfig

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    repos = []
    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"], cwd=str(repo_dir), capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )
        repos.append(RepoConfig(id=name, path=str(repo_dir), base_branch="main"))

    daemon = ForgeDaemon(str(tmp_path), settings=ForgeSettings(), repos=repos)

    # Mock DB
    from unittest.mock import AsyncMock

    db = AsyncMock()
    db.update_pipeline_status = AsyncMock()
    db.log_event = AsyncMock()

    result = await daemon._preflight_checks(str(tmp_path), db, "test-pipeline")
    assert result is True, "Preflight should pass for multi-repo workspace"


# ---------------------------------------------------------------------------
# Tests for resume-aware execution: status persistence and contract restoration
# ---------------------------------------------------------------------------


def _make_task_def(task_id="task-1"):
    """Create a minimal valid TaskDefinition dict for TaskGraph construction."""
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "description": "test",
        "files": ["main.py"],
    }


@pytest.mark.asyncio
class TestGenerateContractsPersistsStatus:
    """generate_contracts() persists 'contracts' status to DB."""

    async def test_no_hints_skips_status(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()
        daemon._snapshot = MagicMock()
        daemon._snapshot.format_for_planner.return_value = ""
        daemon._strategy = "balanced"
        daemon._settings = ForgeSettings()

        from forge.core.models import TaskGraph

        graph = TaskGraph(tasks=[_make_task_def()], integration_hints=[])

        db = AsyncMock()
        db.log_event = AsyncMock()

        # No hints → early return, no status change
        await daemon.generate_contracts(graph, db, "pipe-1")
        db.update_pipeline_status.assert_not_called()

    async def test_contracts_status_persisted_with_hints(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()
        daemon._snapshot = MagicMock()
        daemon._snapshot.format_for_planner.return_value = ""
        daemon._strategy = "balanced"
        daemon._settings = ForgeSettings(contracts_required=False)

        from forge.core.models import TaskGraph

        hint = {
            "producer_task_id": "task-1",
            "consumer_task_ids": ["task-2"],
            "interface_type": "api_endpoint",
            "description": "test",
        }
        graph = TaskGraph(tasks=[_make_task_def()], integration_hints=[hint])

        db = AsyncMock()
        db.log_event = AsyncMock()
        db.add_pipeline_cost = AsyncMock()
        db.set_pipeline_contracts = AsyncMock()

        with (
            patch("forge.core.daemon.ContractBuilder") as MockBuilder,
            patch("forge.core.daemon.ContractBuilderLLM") as MockBuilderLLM,
        ):
            from forge.core.contracts import ContractSet

            # Ensure _last_sdk_result is None so cost-tracking code is skipped
            MockBuilderLLM.return_value._last_sdk_result = None
            mock_builder = MockBuilder.return_value
            mock_builder.build = AsyncMock(return_value=ContractSet())
            await daemon.generate_contracts(graph, db, "pipe-1")

        db.update_pipeline_status.assert_called_once_with("pipe-1", "contracts")


@pytest.mark.asyncio
class TestExecutePersistsStatus:
    """execute() persists 'executing' status to DB."""

    async def test_executing_status_persisted(self, tmp_path):
        daemon = _make_daemon(tmp_path)

        db = AsyncMock()
        db.log_event = AsyncMock()
        db.update_pipeline_status = AsyncMock()

        # We only need to verify the status is set early.
        # Raise after that to short-circuit the rest of execute().
        daemon._emit = AsyncMock()
        daemon._init_repos = AsyncMock()
        daemon._preflight_checks = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="Pre-flight checks failed"):
            from forge.core.models import TaskGraph

            graph = TaskGraph(tasks=[_make_task_def()])
            await daemon.execute(graph, db, "pipe-1")

        db.update_pipeline_status.assert_called_with("pipe-1", "executing")


@pytest.mark.asyncio
class TestResumeContractRestoration:
    """On resume, execute() restores contracts from DB and recalculates agents."""

    async def test_contract_restoration_from_db(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()
        daemon._init_repos = AsyncMock()
        # Pre-flight must PASS so the resume block (contract restoration + agent scaling)
        # is reached before execution stops.
        daemon._preflight_checks = AsyncMock(return_value=True)

        db = AsyncMock()
        db.log_event = AsyncMock()
        db.update_pipeline_status = AsyncMock()

        # Simulate stored contracts JSON
        from forge.core.contracts import ContractSet

        contracts = ContractSet()
        db.get_pipeline_contracts = AsyncMock(return_value=contracts.model_dump_json())

        # Simulate tasks for agent re-scaling
        t1 = _make_task("todo", "task-1")
        t2 = _make_task("in_review", "task-2")
        t3 = _make_task("done", "task-3")
        db.list_tasks_by_pipeline = AsyncMock(return_value=[t1, t2, t3])

        # Fail after the resume block by raising on db.get_pipeline
        db.get_pipeline = AsyncMock(side_effect=RuntimeError("stop after resume"))

        from forge.core.models import TaskGraph

        graph = TaskGraph(tasks=[_make_task_def()])

        with pytest.raises(RuntimeError, match="stop after resume"):
            await daemon.execute(graph, db, "pipe-1", resume=True)

        # Contracts were restored
        assert isinstance(daemon._contracts, ContractSet)
        # Agent scaling: 2 remaining (todo + in_review), capped by max_agents
        assert daemon._effective_max_agents == min(2, daemon._settings.max_agents)

    async def test_contract_restoration_with_malformed_json(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()
        daemon._init_repos = AsyncMock()
        # Pre-flight must PASS so the resume block (contract restoration + agent scaling)
        # is reached before execution stops.
        daemon._preflight_checks = AsyncMock(return_value=True)

        db = AsyncMock()
        db.log_event = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.get_pipeline_contracts = AsyncMock(return_value="{bad json")
        db.list_tasks_by_pipeline = AsyncMock(return_value=[])

        # Fail after the resume block by raising on db.get_pipeline
        db.get_pipeline = AsyncMock(side_effect=RuntimeError("stop after resume"))

        from forge.core.contracts import ContractSet
        from forge.core.models import TaskGraph

        graph = TaskGraph(tasks=[_make_task_def()])

        with pytest.raises(RuntimeError, match="stop after resume"):
            await daemon.execute(graph, db, "pipe-1", resume=True)

        # Should fall back to empty ContractSet
        assert isinstance(daemon._contracts, ContractSet)

    async def test_agent_rescaling_counts_remaining(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()
        daemon._init_repos = AsyncMock()
        # Pre-flight must PASS so the resume block (contract restoration + agent scaling)
        # is reached before execution stops.
        daemon._preflight_checks = AsyncMock(return_value=True)

        db = AsyncMock()
        db.log_event = AsyncMock()
        db.update_pipeline_status = AsyncMock()
        db.get_pipeline_contracts = AsyncMock(return_value=None)

        # 3 remaining: 1 todo + 1 in_review + 1 blocked
        tasks = [
            _make_task("todo", "t1"),
            _make_task("in_review", "t2"),
            _make_task("blocked", "t3"),
            _make_task("done", "t4"),
            _make_task("error", "t5"),
        ]
        db.list_tasks_by_pipeline = AsyncMock(return_value=tasks)

        # Fail after the resume block by raising on db.get_pipeline
        db.get_pipeline = AsyncMock(side_effect=RuntimeError("stop after resume"))

        from forge.core.models import TaskGraph

        graph = TaskGraph(tasks=[_make_task_def()])

        with pytest.raises(RuntimeError, match="stop after resume"):
            await daemon.execute(graph, db, "pipe-1", resume=True)

        assert daemon._effective_max_agents == min(3, daemon._settings.max_agents)
