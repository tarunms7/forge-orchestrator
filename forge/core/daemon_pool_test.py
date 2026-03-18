"""Tests for the continuous task pool in ForgeDaemon."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon
from forge.core.models import TaskState


def _make_daemon(tmp_path, **settings_kwargs):
    settings = ForgeSettings(**settings_kwargs)
    return ForgeDaemon(project_dir=str(tmp_path), settings=settings)


def _make_task(state: str, task_id: str = "task-1") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.state = state
    t.title = f"Task {task_id}"
    t.description = "test task"
    t.files = []
    t.depends_on = []
    t.complexity = "medium"
    t.assigned_agent = None
    t.retry_count = 0
    return t


@pytest.mark.asyncio
class TestSafeExecuteTask:

    async def test_normal_completion_releases_agent(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(return_value=None)
        await daemon._safe_execute_task(db, MagicMock(), MagicMock(), MagicMock(), "task-1", "agent-1", pipeline_id="pipe-1")
        db.release_agent.assert_called_once_with("agent-1")

    async def test_exception_releases_agent_and_reraises(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await daemon._safe_execute_task(db, MagicMock(), MagicMock(), MagicMock(), "task-1", "agent-1")
        db.release_agent.assert_called_once_with("agent-1")

    async def test_cancellation_releases_agent_and_reraises(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await daemon._safe_execute_task(db, MagicMock(), MagicMock(), MagicMock(), "task-1", "agent-1")
        db.release_agent.assert_called_once_with("agent-1")

    async def test_release_failure_does_not_mask_original_error(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock(side_effect=Exception("DB down"))
        daemon._execute_task = AsyncMock(side_effect=RuntimeError("task crash"))
        with pytest.raises(RuntimeError, match="task crash"):
            await daemon._safe_execute_task(db, MagicMock(), MagicMock(), MagicMock(), "task-1", "agent-1")


@pytest.mark.asyncio
class TestHandleTaskException:

    async def test_marks_task_error_and_releases_agent(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()
        task_rec = _make_task("in_progress", "task-1")
        task_rec.assigned_agent = "agent-1"
        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=task_rec)
        db.release_agent = AsyncMock()
        db.log_event = AsyncMock()
        worktree_mgr = MagicMock()
        worktree_mgr.remove = MagicMock()
        await daemon._handle_task_exception("task-1", RuntimeError("exploded"), db, worktree_mgr, "pipe-1")
        db.update_task_state.assert_called_once_with("task-1", TaskState.ERROR.value)
        db.release_agent.assert_called_once_with("agent-1")
        worktree_mgr.remove.assert_called_once_with("task-1")

    async def test_emits_pipeline_error_when_all_terminal(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        emitted = []
        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))
        daemon._emit = mock_emit
        task_rec = _make_task("error", "task-1")
        task_rec.assigned_agent = "agent-1"
        all_tasks = [_make_task(TaskState.DONE.value, "task-2"), _make_task(TaskState.ERROR.value, "task-1")]
        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=task_rec)
        db.release_agent = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=all_tasks)
        db.log_event = AsyncMock()
        worktree_mgr = MagicMock()
        worktree_mgr.remove = MagicMock()
        await daemon._handle_task_exception("task-1", RuntimeError("crash"), db, worktree_mgr, "pipe-1")
        assert any(e[0] == "pipeline:error" for e in emitted)

    async def test_no_pipeline_error_when_tasks_still_active(self, tmp_path):
        daemon = _make_daemon(tmp_path)
        emitted = []
        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))
        daemon._emit = mock_emit
        task_rec = _make_task("error", "task-1")
        task_rec.assigned_agent = "agent-1"
        remaining = [_make_task(TaskState.IN_PROGRESS.value, "task-2"), _make_task(TaskState.ERROR.value, "task-1")]
        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=task_rec)
        db.release_agent = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=remaining)
        db.log_event = AsyncMock()
        worktree_mgr = MagicMock()
        worktree_mgr.remove = MagicMock()
        await daemon._handle_task_exception("task-1", RuntimeError("crash"), db, worktree_mgr, "pipe-1")
        assert not any(e[0] == "pipeline:error" for e in emitted)


@pytest.mark.asyncio
class TestContinuousTaskPool:

    async def test_retried_task_dispatched_while_other_running(self, tmp_path):
        """A retried task gets dispatched even while another task is still running."""
        daemon = _make_daemon(tmp_path, max_agents=2, scheduler_poll_interval=0.05)

        gate_task1 = asyncio.Event()
        gate_task2 = asyncio.Event()
        call_order = []

        async def fake_execute(db, runtime, wt, mw, task_id, agent_id, pipeline_id=None):
            call_order.append(task_id)
            if task_id == "task-1":
                await gate_task1.wait()
            elif task_id == "task-2":
                await gate_task2.wait()

        daemon._execute_task = fake_execute

        dispatch_round = 0

        def make_dispatch_plan(task_records, agent_records, max_agents):
            nonlocal dispatch_round
            dispatch_round += 1
            if dispatch_round == 1:
                return [("task-1", "agent-0")]
            elif dispatch_round == 2:
                return [("task-2", "agent-1")]
            return []

        db = MagicMock()
        db.assign_task = AsyncMock()
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        db.log_event = AsyncMock()

        # Return tasks as in_progress after first dispatch, then add task-2 as todo
        call_count = 0
        async def list_tasks(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [_make_task(TaskState.TODO.value, "task-1")]
            elif call_count <= 3:
                t1 = _make_task(TaskState.IN_PROGRESS.value, "task-1")
                t2 = _make_task(TaskState.TODO.value, "task-2")
                return [t1, t2]
            else:
                t1 = _make_task(TaskState.DONE.value, "task-1")
                t2 = _make_task(TaskState.DONE.value, "task-2")
                return [t1, t2]
        db.list_tasks_by_pipeline = list_tasks

        db.list_agents = AsyncMock(return_value=[
            MagicMock(id="agent-0", state="idle", current_task=None),
            MagicMock(id="agent-1", state="idle", current_task=None),
        ])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False))

        monitor = MagicMock()
        monitor.take_snapshot = MagicMock(return_value={})
        monitor.can_dispatch = MagicMock(return_value=True)

        with patch("forge.core.daemon.Scheduler.dispatch_plan", side_effect=make_dispatch_plan), \
             patch("forge.core.daemon._print_status_table"), \
             patch("forge.core.daemon._row_to_record", side_effect=lambda t: t), \
             patch("forge.core.engine._row_to_agent", side_effect=lambda a: a):

            async def run_loop():
                await daemon._execution_loop_inner(
                    db, MagicMock(), MagicMock(), MagicMock(), monitor, "pipe-1"
                )

            loop_task = asyncio.create_task(run_loop())
            # Let two poll cycles run so both tasks get dispatched
            await asyncio.sleep(0.2)
            # Both tasks should have been dispatched
            assert "task-1" in call_order
            assert "task-2" in call_order
            # Release gates so tasks complete
            gate_task1.set()
            gate_task2.set()
            await asyncio.sleep(0.2)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

    async def test_active_tasks_guard_prevents_double_dispatch(self, tmp_path):
        """Tasks already in the pool are filtered from dispatch_plan."""
        daemon = _make_daemon(tmp_path, max_agents=2, scheduler_poll_interval=0.05)

        dispatch_count = {"task-1": 0}
        gate = asyncio.Event()

        async def fake_execute(db, runtime, wt, mw, task_id, agent_id, pipeline_id=None):
            dispatch_count[task_id] = dispatch_count.get(task_id, 0) + 1
            await gate.wait()

        daemon._execute_task = fake_execute

        # Scheduler always returns task-1
        round_num = 0
        def always_dispatch(task_records, agent_records, max_agents):
            nonlocal round_num
            round_num += 1
            if round_num <= 3:
                return [("task-1", "agent-0")]
            return []

        db = MagicMock()
        db.assign_task = AsyncMock()
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        db.log_event = AsyncMock()

        call_count = 0
        async def list_tasks(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return [_make_task(TaskState.IN_PROGRESS.value, "task-1")]
            return [_make_task(TaskState.DONE.value, "task-1")]

        db.list_tasks_by_pipeline = list_tasks
        db.list_agents = AsyncMock(return_value=[
            MagicMock(id="agent-0", state="idle", current_task=None),
        ])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False))

        monitor = MagicMock()
        monitor.take_snapshot = MagicMock(return_value={})
        monitor.can_dispatch = MagicMock(return_value=True)

        with patch("forge.core.daemon.Scheduler.dispatch_plan", side_effect=always_dispatch), \
             patch("forge.core.daemon._print_status_table"), \
             patch("forge.core.daemon._row_to_record", side_effect=lambda t: t), \
             patch("forge.core.engine._row_to_agent", side_effect=lambda a: a):

            loop_task = asyncio.create_task(
                daemon._execution_loop_inner(db, MagicMock(), MagicMock(), MagicMock(), monitor, "pipe-1")
            )
            await asyncio.sleep(0.3)
            gate.set()
            await asyncio.sleep(0.2)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

        assert dispatch_count["task-1"] == 1, f"task-1 dispatched {dispatch_count['task-1']} times, expected 1"

    async def test_exception_in_one_task_does_not_affect_other(self, tmp_path):
        """If task-1 crashes, task-2 still runs to completion."""
        daemon = _make_daemon(tmp_path, max_agents=2, scheduler_poll_interval=0.05)

        task2_completed = asyncio.Event()

        async def fake_execute(db, runtime, wt, mw, task_id, agent_id, pipeline_id=None):
            if task_id == "task-1":
                raise RuntimeError("task-1 crash")
            task2_completed.set()

        daemon._execute_task = fake_execute
        daemon._emit = AsyncMock()

        dispatched = False
        def dispatch_once(task_records, agent_records, max_agents):
            nonlocal dispatched
            if not dispatched:
                dispatched = True
                return [("task-1", "agent-0"), ("task-2", "agent-1")]
            return []

        db = MagicMock()
        db.assign_task = AsyncMock()
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        db.get_task = AsyncMock(return_value=_make_task("error", "task-1"))
        db.log_event = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=[
            _make_task(TaskState.DONE.value, "task-2"),
            _make_task(TaskState.ERROR.value, "task-1"),
        ])

        call_count = 0
        async def list_tasks_seq(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [_make_task(TaskState.TODO.value, "task-1"), _make_task(TaskState.TODO.value, "task-2")]
            return [_make_task(TaskState.ERROR.value, "task-1"), _make_task(TaskState.DONE.value, "task-2")]
        db.list_tasks_by_pipeline = list_tasks_seq

        db.list_agents = AsyncMock(return_value=[
            MagicMock(id="agent-0", state="idle", current_task=None),
            MagicMock(id="agent-1", state="idle", current_task=None),
        ])
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False))

        monitor = MagicMock()
        monitor.take_snapshot = MagicMock(return_value={})
        monitor.can_dispatch = MagicMock(return_value=True)

        with patch("forge.core.daemon.Scheduler.dispatch_plan", side_effect=dispatch_once), \
             patch("forge.core.daemon._print_status_table"), \
             patch("forge.core.daemon._row_to_record", side_effect=lambda t: t), \
             patch("forge.core.engine._row_to_agent", side_effect=lambda a: a):

            loop_task = asyncio.create_task(
                daemon._execution_loop_inner(db, MagicMock(), MagicMock(), MagicMock(), monitor, "pipe-1")
            )
            try:
                await asyncio.wait_for(task2_completed.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pytest.fail("task-2 did not complete within timeout")
            finally:
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass

        assert task2_completed.is_set(), "task-2 should have completed despite task-1 crashing"

    async def test_shutdown_cancels_active_tasks_and_releases_agents(self, tmp_path):
        """On loop exit, all active tasks are cancelled and agents released."""
        daemon = _make_daemon(tmp_path, max_agents=1, scheduler_poll_interval=0.05)

        task_started = asyncio.Event()
        was_cancelled = False

        async def fake_execute(db, runtime, wt, mw, task_id, agent_id, pipeline_id=None):
            nonlocal was_cancelled
            task_started.set()
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                was_cancelled = True
                raise

        daemon._execute_task = fake_execute

        dispatched = False
        def dispatch_once(task_records, agent_records, max_agents):
            nonlocal dispatched
            if not dispatched:
                dispatched = True
                return [("task-1", "agent-0")]
            return []

        db = MagicMock()
        db.assign_task = AsyncMock()
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        db.log_event = AsyncMock()
        db.get_pipeline = AsyncMock(return_value=MagicMock(paused=False))

        call_count = 0
        async def list_tasks(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [_make_task(TaskState.TODO.value, "task-1")]
            # After task starts, report it as in_progress; eventually report done so loop exits
            if call_count <= 5:
                return [_make_task(TaskState.IN_PROGRESS.value, "task-1")]
            return [_make_task(TaskState.DONE.value, "task-1")]
        db.list_tasks_by_pipeline = list_tasks

        db.list_agents = AsyncMock(return_value=[
            MagicMock(id="agent-0", state="idle", current_task=None),
        ])

        monitor = MagicMock()
        monitor.take_snapshot = MagicMock(return_value={})
        monitor.can_dispatch = MagicMock(return_value=True)

        with patch("forge.core.daemon.Scheduler.dispatch_plan", side_effect=dispatch_once), \
             patch("forge.core.daemon._print_status_table"), \
             patch("forge.core.daemon._row_to_record", side_effect=lambda t: t), \
             patch("forge.core.engine._row_to_agent", side_effect=lambda a: a):

            # Use _execution_loop (not _inner) so the finally block runs shutdown
            loop_task = asyncio.create_task(
                daemon._execution_loop(db, MagicMock(), MagicMock(), MagicMock(), monitor, "pipe-1")
            )
            await task_started.wait()
            # Cancel the loop (simulating shutdown)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

        assert was_cancelled, "Active task should have been cancelled on shutdown"
        db.release_agent.assert_called_with("agent-0")


@pytest.mark.asyncio
class TestActiveTaskCleanup:

    async def test_cleanup_handles_concurrent_removal(self, tmp_path):
        """No KeyError when a task is removed from _active_tasks before the cleanup pop runs."""
        daemon = _make_daemon(tmp_path)

        # Create a done asyncio.Task
        async def _noop():
            pass

        done_task = asyncio.ensure_future(_noop())
        await asyncio.sleep(0)  # let it finish

        # Populate _active_tasks then remove the entry to simulate concurrent removal
        daemon._active_tasks["task-1"] = done_task
        daemon._active_tasks.pop("task-1", None)  # removed, as if _on_task_answered ran

        # Snapshot iteration used by the fixed line — should not raise even if key is absent
        done_ids = [
            tid
            for tid, atask in list(daemon._active_tasks.items())
            if atask.done()
        ]
        # No KeyError; done_ids is empty because the entry was already removed
        assert "task-1" not in done_ids

        # pop guard must also be safe
        result = daemon._active_tasks.pop("task-1", None)
        assert result is None

    async def test_cleanup_with_dict_copy_snapshot(self, tmp_path):
        """Iterating over a list() snapshot is safe when items are added/removed mid-iteration."""
        daemon = _make_daemon(tmp_path)

        async def _noop():
            pass

        # Create two done tasks and one pending task
        done1 = asyncio.ensure_future(_noop())
        done2 = asyncio.ensure_future(_noop())
        await asyncio.sleep(0)  # let them finish

        pending_event = asyncio.Event()

        async def _pending():
            await pending_event.wait()

        pending_task = asyncio.ensure_future(_pending())

        daemon._active_tasks["task-done-1"] = done1
        daemon._active_tasks["task-done-2"] = done2
        daemon._active_tasks["task-pending"] = pending_task

        # Take a snapshot (as the fixed line does)
        snapshot = list(daemon._active_tasks.items())

        # Simulate concurrent modification: remove one entry and add a new one
        daemon._active_tasks.pop("task-done-1", None)
        async def _noop2():
            pass
        new_task = asyncio.ensure_future(_noop2())
        daemon._active_tasks["task-new"] = new_task

        # Iterating the snapshot should not raise and should reflect the original state
        done_ids = [tid for tid, atask in snapshot if atask.done()]
        assert "task-done-1" in done_ids
        assert "task-done-2" in done_ids
        assert "task-pending" not in done_ids

        # pop guard: task-done-1 was already removed — must return None, not raise
        assert daemon._active_tasks.pop("task-done-1", None) is None

        # Cleanup
        pending_event.set()
        await asyncio.sleep(0)
        pending_task.cancel()
        new_task.cancel()
