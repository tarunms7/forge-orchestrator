"""Tests for analytics/metrics DB methods and new columns."""

import pytest

from forge.storage.db import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


async def _create_pipeline(db: Database, pipeline_id: str = "pipe-1", **kwargs):
    """Helper to create a pipeline with defaults."""
    defaults = {
        "id": pipeline_id,
        "description": "Test pipeline",
        "project_dir": "/tmp/test",
        "project_path": "/tmp/test",
    }
    defaults.update(kwargs)
    await db.create_pipeline(**defaults)


async def _create_task(db: Database, task_id: str = "task-1", pipeline_id: str = "pipe-1", **kwargs):
    """Helper to create a task with defaults."""
    defaults = {
        "id": task_id,
        "title": f"Task {task_id}",
        "description": "A test task",
        "files": ["a.py"],
        "depends_on": [],
        "complexity": "low",
        "pipeline_id": pipeline_id,
    }
    defaults.update(kwargs)
    await db.create_task(**defaults)


# ── TaskRow new columns ──────────────────────────────────────────────


class TestTaskRowColumns:
    async def test_new_columns_default_values(self, db: Database):
        await _create_task(db)
        task = await db.get_task("task-1")
        assert task.started_at is None
        assert task.completed_at is None
        assert task.agent_duration_s == 0.0
        assert task.review_duration_s == 0.0
        assert task.lint_duration_s == 0.0
        assert task.merge_duration_s == 0.0
        assert task.num_turns == 0
        assert task.max_turns == 0
        assert task.error_message is None


# ── PipelineRow new columns ──────────────────────────────────────────


class TestPipelineRowColumns:
    async def test_new_columns_default_values(self, db: Database):
        await _create_pipeline(db)
        pipeline = await db.get_pipeline("pipe-1")
        assert pipeline.duration_s == 0.0
        assert pipeline.total_input_tokens == 0
        assert pipeline.total_output_tokens == 0
        assert pipeline.tasks_succeeded == 0
        assert pipeline.tasks_failed == 0
        assert pipeline.total_retries == 0


# ── set_task_timing ──────────────────────────────────────────────────


class TestSetTaskTiming:
    async def test_set_all_timing_fields(self, db: Database):
        await _create_task(db)
        await db.set_task_timing(
            "task-1",
            started_at="2026-03-23T10:00:00+00:00",
            completed_at="2026-03-23T10:05:00+00:00",
            agent_duration_s=120.5,
            review_duration_s=35.2,
            lint_duration_s=4.1,
            merge_duration_s=2.8,
        )
        task = await db.get_task("task-1")
        assert task.started_at == "2026-03-23T10:00:00+00:00"
        assert task.completed_at == "2026-03-23T10:05:00+00:00"
        assert task.agent_duration_s == 120.5
        assert task.review_duration_s == 35.2
        assert task.lint_duration_s == 4.1
        assert task.merge_duration_s == 2.8

    async def test_set_partial_timing(self, db: Database):
        """Only non-None kwargs should be written; others remain at default."""
        await _create_task(db)
        await db.set_task_timing("task-1", started_at="2026-03-23T10:00:00+00:00")
        task = await db.get_task("task-1")
        assert task.started_at == "2026-03-23T10:00:00+00:00"
        assert task.completed_at is None
        assert task.agent_duration_s == 0.0

    async def test_set_timing_nonexistent_task(self, db: Database):
        """Setting timing on a nonexistent task should not raise."""
        await db.set_task_timing("nope", started_at="2026-03-23T10:00:00+00:00")

    async def test_overwrite_timing(self, db: Database):
        """Second call should overwrite previously set values."""
        await _create_task(db)
        await db.set_task_timing("task-1", agent_duration_s=10.0)
        await db.set_task_timing("task-1", agent_duration_s=20.0)
        task = await db.get_task("task-1")
        assert task.agent_duration_s == 20.0


# ── set_task_turns ───────────────────────────────────────────────────


class TestSetTaskTurns:
    async def test_set_turns(self, db: Database):
        await _create_task(db)
        await db.set_task_turns("task-1", num_turns=8, max_turns=25)
        task = await db.get_task("task-1")
        assert task.num_turns == 8
        assert task.max_turns == 25

    async def test_set_turns_nonexistent(self, db: Database):
        await db.set_task_turns("nope", num_turns=5, max_turns=10)


# ── set_task_error ───────────────────────────────────────────────────


class TestSetTaskError:
    async def test_set_error(self, db: Database):
        await _create_task(db)
        await db.set_task_error("task-1", "lint check failed")
        task = await db.get_task("task-1")
        assert task.error_message == "lint check failed"

    async def test_set_error_nonexistent(self, db: Database):
        await db.set_task_error("nope", "some error")

    async def test_overwrite_error(self, db: Database):
        await _create_task(db)
        await db.set_task_error("task-1", "first error")
        await db.set_task_error("task-1", "second error")
        task = await db.get_task("task-1")
        assert task.error_message == "second error"


# ── finalize_pipeline_metrics ────────────────────────────────────────


class TestFinalizePipelineMetrics:
    async def test_basic_finalize(self, db: Database):
        await _create_pipeline(db)
        # Create tasks with various states and costs
        await _create_task(db, "t1")
        await _create_task(db, "t2")
        await _create_task(db, "t3")

        # Set states
        await db.update_task_state("t1", "in_progress")
        await db.update_task_state("t1", "done")
        await db.update_task_state("t2", "in_progress")
        await db.update_task_state("t2", "done")
        await db.update_task_state("t3", "in_progress")
        await db.update_task_state("t3", "error")

        # Add token costs
        await db.add_task_agent_cost("t1", cost=0.10, input_tokens=1000, output_tokens=500)
        await db.add_task_agent_cost("t2", cost=0.20, input_tokens=2000, output_tokens=1000)
        await db.add_task_agent_cost("t3", cost=0.05, input_tokens=500, output_tokens=200)

        await db.finalize_pipeline_metrics("pipe-1")

        pipeline = await db.get_pipeline("pipe-1")
        assert pipeline.total_input_tokens == 3500
        assert pipeline.total_output_tokens == 1700
        assert pipeline.tasks_succeeded == 2
        assert pipeline.tasks_failed == 1
        assert pipeline.total_retries == 0
        assert pipeline.duration_s >= 0.0

    async def test_finalize_with_retries(self, db: Database):
        await _create_pipeline(db)
        await _create_task(db, "t1")

        # Simulate a retry
        await db.update_task_state("t1", "in_progress")
        await db.retry_task("t1", review_feedback="fix lint")

        await db.finalize_pipeline_metrics("pipe-1")
        pipeline = await db.get_pipeline("pipe-1")
        assert pipeline.total_retries == 1

    async def test_finalize_nonexistent_pipeline(self, db: Database):
        """Should not raise for nonexistent pipeline."""
        await db.finalize_pipeline_metrics("nope")

    async def test_finalize_no_tasks(self, db: Database):
        await _create_pipeline(db)
        await db.finalize_pipeline_metrics("pipe-1")
        pipeline = await db.get_pipeline("pipe-1")
        assert pipeline.tasks_succeeded == 0
        assert pipeline.tasks_failed == 0
        assert pipeline.total_input_tokens == 0


# ── get_pipeline_stats ───────────────────────────────────────────────


class TestGetPipelineStats:
    async def test_full_stats(self, db: Database):
        await _create_pipeline(db)
        await _create_task(db, "t1", title="Auth endpoints")
        await db.update_task_state("t1", "in_progress")
        await db.update_task_state("t1", "done")
        await db.set_task_timing(
            "t1",
            started_at="2026-03-23T10:01:00+00:00",
            completed_at="2026-03-23T10:08:00+00:00",
            agent_duration_s=120.5,
            review_duration_s=35.2,
        )
        await db.set_task_turns("t1", num_turns=8, max_turns=25)
        await db.add_task_agent_cost("t1", cost=0.35, input_tokens=32000, output_tokens=12000)
        await db.add_task_review_cost("t1", cost=0.10)
        await db.set_pipeline_planner_cost("pipe-1", cost=0.08)

        await db.finalize_pipeline_metrics("pipe-1")
        stats = await db.get_pipeline_stats("pipe-1")

        assert stats["id"] == "pipe-1"
        assert stats["description"] == "Test pipeline"
        assert stats["status"] == "planning"
        assert stats["planner_cost_usd"] == 0.08
        assert stats["total_input_tokens"] == 32000
        assert stats["total_output_tokens"] == 12000
        assert stats["tasks_succeeded"] == 1
        assert stats["tasks_failed"] == 0

        assert len(stats["tasks"]) == 1
        t = stats["tasks"][0]
        assert t["id"] == "t1"
        assert t["title"] == "Auth endpoints"
        assert t["state"] == "done"
        assert t["agent_duration_s"] == 120.5
        assert t["review_duration_s"] == 35.2
        assert t["cost_usd"] == pytest.approx(0.45, abs=0.01)
        assert t["agent_cost_usd"] == pytest.approx(0.35, abs=0.01)
        assert t["review_cost_usd"] == pytest.approx(0.10, abs=0.01)
        assert t["input_tokens"] == 32000
        assert t["output_tokens"] == 12000
        assert t["num_turns"] == 8
        assert t["max_turns"] == 25
        assert t["error_message"] is None

    async def test_stats_nonexistent_pipeline(self, db: Database):
        stats = await db.get_pipeline_stats("nope")
        assert stats == {}

    async def test_stats_with_error_task(self, db: Database):
        await _create_pipeline(db)
        await _create_task(db, "t1")
        await db.update_task_state("t1", "in_progress")
        await db.update_task_state("t1", "error")
        await db.set_task_error("t1", "lint check failed")

        stats = await db.get_pipeline_stats("pipe-1")
        t = stats["tasks"][0]
        assert t["state"] == "error"
        assert t["error_message"] == "lint check failed"


# ── get_pipeline_trends ──────────────────────────────────────────────


class TestGetPipelineTrends:
    async def test_trends_basic(self, db: Database):
        await _create_pipeline(db, "pipe-1")
        await _create_pipeline(db, "pipe-2", description="Second pipeline")

        trends = await db.get_pipeline_trends()
        assert len(trends) == 2
        # Should be ordered by created_at descending
        assert trends[0]["id"] == "pipe-2"
        assert trends[1]["id"] == "pipe-1"

    async def test_trends_with_limit(self, db: Database):
        for i in range(5):
            await _create_pipeline(db, f"pipe-{i}")
        trends = await db.get_pipeline_trends(limit=3)
        assert len(trends) == 3

    async def test_trends_filter_by_project_path(self, db: Database):
        await _create_pipeline(db, "pipe-1", project_path="/project/a")
        await _create_pipeline(db, "pipe-2", project_path="/project/b")

        trends = await db.get_pipeline_trends(project_path="/project/a")
        assert len(trends) == 1
        assert trends[0]["id"] == "pipe-1"

    async def test_trends_shape(self, db: Database):
        await _create_pipeline(db, "pipe-1")
        await db.finalize_pipeline_metrics("pipe-1")

        trends = await db.get_pipeline_trends()
        assert len(trends) == 1
        t = trends[0]
        expected_keys = {
            "id", "description", "status", "duration_s", "total_cost_usd",
            "total_input_tokens", "total_output_tokens", "tasks_succeeded",
            "tasks_failed", "total_retries", "created_at",
        }
        assert set(t.keys()) == expected_keys

    async def test_trends_empty(self, db: Database):
        trends = await db.get_pipeline_trends()
        assert trends == []


# ── get_retry_summary ────────────────────────────────────────────────


class TestGetRetrySummary:
    async def test_basic_retry_summary(self, db: Database):
        await _create_pipeline(db)
        await _create_task(db, "t1")
        await _create_task(db, "t2")

        # Simulate retries
        await db.update_task_state("t1", "in_progress")
        await db.retry_task("t1", review_feedback="fix lint")
        await db.set_task_error("t1", "lint check failed")

        await db.update_task_state("t2", "in_progress")
        await db.retry_task("t2", review_feedback="fix lint too")
        await db.set_task_error("t2", "lint check failed")

        summary = await db.get_retry_summary(pipeline_id="pipe-1")
        assert len(summary) == 1
        assert summary[0]["error_pattern"] == "lint check failed"
        assert summary[0]["total_retries"] == 2
        assert summary[0]["task_count"] == 2
        assert set(summary[0]["task_ids"]) == {"t1", "t2"}

    async def test_retry_summary_different_errors(self, db: Database):
        await _create_pipeline(db)
        await _create_task(db, "t1")
        await _create_task(db, "t2")

        await db.update_task_state("t1", "in_progress")
        await db.retry_task("t1")
        await db.set_task_error("t1", "lint check failed")

        await db.update_task_state("t2", "in_progress")
        await db.retry_task("t2")
        await db.retry_task("t2")
        await db.set_task_error("t2", "merge conflict")

        summary = await db.get_retry_summary(pipeline_id="pipe-1")
        assert len(summary) == 2
        # Sorted by total_retries desc, "merge conflict" has 2 retries
        assert summary[0]["error_pattern"] == "merge conflict"
        assert summary[0]["total_retries"] == 2
        assert summary[1]["error_pattern"] == "lint check failed"
        assert summary[1]["total_retries"] == 1

    async def test_retry_summary_no_retries(self, db: Database):
        await _create_pipeline(db)
        await _create_task(db, "t1")
        summary = await db.get_retry_summary(pipeline_id="pipe-1")
        assert summary == []

    async def test_retry_summary_all_pipelines(self, db: Database):
        await _create_pipeline(db, "pipe-1")
        await _create_pipeline(db, "pipe-2")
        await _create_task(db, "t1", pipeline_id="pipe-1")
        await _create_task(db, "t2", pipeline_id="pipe-2")

        await db.update_task_state("t1", "in_progress")
        await db.retry_task("t1")
        await db.set_task_error("t1", "error A")

        await db.update_task_state("t2", "in_progress")
        await db.retry_task("t2")
        await db.set_task_error("t2", "error B")

        # No pipeline_id filter — should include both
        summary = await db.get_retry_summary()
        assert len(summary) == 2
        all_task_ids = []
        for s in summary:
            all_task_ids.extend(s["task_ids"])
        assert set(all_task_ids) == {"t1", "t2"}

    async def test_retry_summary_no_error_message(self, db: Database):
        """Tasks with retries but no error_message should use 'unknown error'."""
        await _create_pipeline(db)
        await _create_task(db, "t1")
        await db.update_task_state("t1", "in_progress")
        await db.retry_task("t1")

        summary = await db.get_retry_summary(pipeline_id="pipe-1")
        assert len(summary) == 1
        assert summary[0]["error_pattern"] == "unknown error"
