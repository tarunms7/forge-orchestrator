"""Tests for pipeline health monitor."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.core.health_monitor import HealthConfig, PipelineHealthMonitor


def _make_task(task_id: str, state: str, depends_on: list[str] | None = None):
    t = MagicMock()
    t.id = task_id
    t.state = state
    t.depends_on = depends_on or []
    return t


class TestHealthMonitor:
    def test_record_activity(self):
        monitor = PipelineHealthMonitor(db=MagicMock(), pipeline_id="p1")
        monitor.record_task_activity("task-1")
        assert "task-1" in monitor._task_last_output

    def test_stop(self):
        monitor = PipelineHealthMonitor(db=MagicMock(), pipeline_id="p1")
        monitor._running = True
        monitor.stop()
        assert not monitor._running

    @pytest.mark.asyncio
    async def test_detects_stuck_in_progress(self):
        """Task idle in in_progress for too long should trigger callback."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [_make_task("t1", "in_progress")]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(task_stuck_timeout_s=0.1, check_interval_s=0.1)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        # Record activity far in the past
        monitor._task_last_output["t1"] = time.monotonic() - 10.0

        await monitor._check_health()
        assert len(stuck_tasks) == 1
        assert stuck_tasks[0][0] == "t1"
        assert "no agent output" in stuck_tasks[0][1]

    @pytest.mark.asyncio
    async def test_active_task_not_stuck(self):
        """Task with recent output should not be flagged."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [_make_task("t1", "in_progress")]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(task_stuck_timeout_s=60.0)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )
        # Record recent activity
        monitor.record_task_activity("t1")

        await monitor._check_health()
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_detects_deadlock_circular_dependency(self):
        """Tasks blocked in a circular dependency should trigger deadlock."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked", depends_on=["t2"]),
            _make_task("t2", "blocked", depends_on=["t1"]),
            _make_task("t3", "done"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        assert len(stuck_tasks) == 2
        assert all("deadlock" in reason for _, reason in stuck_tasks)
        # Verify cycle is reported in the reason
        assert all("cycle" in reason for _, reason in stuck_tasks)

    @pytest.mark.asyncio
    async def test_no_deadlock_when_tasks_active(self):
        """Mix of blocked and active tasks is not a deadlock."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked"),
            _make_task("t2", "in_progress"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True, task_stuck_timeout_s=999)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )
        # Record recent activity so in_progress task isn't "stuck"
        monitor.record_task_activity("t2")

        await monitor._check_health()
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_done_tasks_ignored(self):
        """Completed tasks should never be flagged."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "done"),
            _make_task("t2", "error"),
            _make_task("t3", "cancelled"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_cleans_up_terminal_task_tracking(self):
        """Terminal-state tasks should have their tracking entries removed."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "done"),
            _make_task("t2", "error"),
            _make_task("t3", "cancelled"),
            _make_task("t4", "in_progress"),
        ]

        monitor = PipelineHealthMonitor(db=db, pipeline_id="p1")
        # Pre-populate tracking entries
        for tid in ("t1", "t2", "t3", "t4"):
            monitor._task_last_output[tid] = time.monotonic()

        await monitor._check_health()

        # Terminal tasks should be cleaned up
        assert "t1" not in monitor._task_last_output
        assert "t2" not in monitor._task_last_output
        assert "t3" not in monitor._task_last_output
        # Active task should remain
        assert "t4" in monitor._task_last_output

    @pytest.mark.asyncio
    async def test_awaiting_input_no_false_deadlock(self):
        """Tasks in awaiting_input should not trigger deadlock detection."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked", depends_on=["t2"]),
            _make_task("t2", "awaiting_input"),
            _make_task("t3", "done"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_awaiting_approval_no_false_deadlock(self):
        """Tasks in awaiting_approval should not trigger deadlock detection."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked", depends_on=["t2"]),
            _make_task("t2", "awaiting_approval"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_blocked_plus_todo_no_deadlock(self):
        """Mix of BLOCKED + TODO should not trigger deadlock — TODO hasn't been dispatched."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked", depends_on=["t2"]),
            _make_task("t2", "todo"),
            _make_task("t3", "done"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_all_blocked_no_cycle_no_deadlock(self):
        """All blocked but depending on done tasks (deps resolved) — no cycle, no deadlock."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked", depends_on=["t3"]),
            _make_task("t2", "blocked", depends_on=["t3"]),
            _make_task("t3", "done"),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        # t1 and t2 depend on t3 (done), so they're not in a cycle
        # But all *remaining* (non-terminal) tasks are blocked — cycle check runs
        # t1 depends on t3 which is not in blocked_ids → not a deadlock
        assert len(stuck_tasks) == 0

    @pytest.mark.asyncio
    async def test_three_task_cycle_detected(self):
        """Three tasks in a circular dependency chain should be detected."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked", depends_on=["t2"]),
            _make_task("t2", "blocked", depends_on=["t3"]),
            _make_task("t3", "blocked", depends_on=["t1"]),
        ]

        stuck_tasks = []

        async def on_stuck(task_id, reason):
            stuck_tasks.append((task_id, reason))

        config = HealthConfig(deadlock_check_enabled=True)
        monitor = PipelineHealthMonitor(
            db=db,
            pipeline_id="p1",
            config=config,
            on_stuck_task=on_stuck,
        )

        await monitor._check_health()
        assert len(stuck_tasks) == 3
        assert all("cycle" in reason for _, reason in stuck_tasks)

    @pytest.mark.asyncio
    async def test_run_loop_stops(self):
        """Monitor loop should stop when stop() is called."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = []

        config = HealthConfig(check_interval_s=0.05)
        monitor = PipelineHealthMonitor(db=db, pipeline_id="p1", config=config)

        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.15)
        monitor.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert not monitor._running
