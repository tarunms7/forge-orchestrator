"""Tests for pipeline health monitor."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.core.health_monitor import HealthConfig, PipelineHealthMonitor


def _make_task(task_id: str, state: str):
    t = MagicMock()
    t.id = task_id
    t.state = state
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
    async def test_detects_deadlock(self):
        """All tasks blocked should trigger deadlock detection."""
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [
            _make_task("t1", "blocked"),
            _make_task("t2", "blocked"),
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
