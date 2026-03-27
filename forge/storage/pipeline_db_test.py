"""Tests for pipeline persistence."""

import pytest

from forge.storage.db import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


async def test_create_and_get_pipeline(db):
    await db.create_pipeline(
        id="pipe-1",
        description="Build login",
        project_dir="/tmp/test",
        model_strategy="auto",
    )
    p = await db.get_pipeline("pipe-1")
    assert p is not None
    assert p.description == "Build login"
    assert p.status == "planning"
    assert p.model_strategy == "auto"


async def test_update_pipeline_status(db):
    await db.create_pipeline(
        id="pipe-1", description="t", project_dir="/tmp", model_strategy="auto"
    )
    await db.update_pipeline_status("pipe-1", "executing")
    p = await db.get_pipeline("pipe-1")
    assert p.status == "executing"


async def test_set_pipeline_plan(db):
    await db.create_pipeline(
        id="pipe-1", description="t", project_dir="/tmp", model_strategy="auto"
    )
    await db.set_pipeline_plan("pipe-1", '{"tasks": []}')
    p = await db.get_pipeline("pipe-1")
    assert p.task_graph_json == '{"tasks": []}'


async def test_list_pipelines(db):
    await db.create_pipeline(
        id="p1", description="a", project_dir="/tmp", model_strategy="auto", user_id="u1"
    )
    await db.create_pipeline(
        id="p2", description="b", project_dir="/tmp", model_strategy="auto", user_id="u2"
    )
    all_pipes = await db.list_pipelines()
    assert len(all_pipes) == 2
    user_pipes = await db.list_pipelines(user_id="u1")
    assert len(user_pipes) == 1
    assert user_pipes[0].id == "p1"


# ── Project tracking tests ───────────────────────────────────────────


async def test_create_pipeline_with_project_path(db):
    """create_pipeline should store project_path and project_name."""
    await db.create_pipeline(
        id="pipe-proj",
        description="Project test",
        project_dir="/tmp/proj",
        model_strategy="auto",
        project_path="/Users/tarun/my-project",
        project_name="my-project",
    )
    p = await db.get_pipeline("pipe-proj")
    assert p is not None
    assert p.project_path == "/Users/tarun/my-project"
    assert p.project_name == "my-project"


async def test_create_pipeline_project_defaults_to_none(db):
    """create_pipeline without project params should default to None."""
    await db.create_pipeline(
        id="pipe-no-proj",
        description="No project",
        project_dir="/tmp",
        model_strategy="auto",
    )
    p = await db.get_pipeline("pipe-no-proj")
    assert p.project_path is None
    assert p.project_name is None


async def test_list_pipelines_filter_by_project_path(db):
    """list_pipelines with project_path should only return matching pipelines."""
    await db.create_pipeline(
        id="p1",
        description="a",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-a",
        project_name="proj-a",
    )
    await db.create_pipeline(
        id="p2",
        description="b",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-b",
        project_name="proj-b",
    )
    await db.create_pipeline(
        id="p3",
        description="c",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-a",
        project_name="proj-a",
    )
    # No filter — all pipelines
    all_pipes = await db.list_pipelines()
    assert len(all_pipes) == 3

    # Filter by project_path
    proj_a = await db.list_pipelines(project_path="/Users/tarun/proj-a")
    assert len(proj_a) == 2
    assert {p.id for p in proj_a} == {"p1", "p3"}

    proj_b = await db.list_pipelines(project_path="/Users/tarun/proj-b")
    assert len(proj_b) == 1
    assert proj_b[0].id == "p2"


async def test_list_pipelines_filter_by_project_path_none_returns_all(db):
    """list_pipelines with project_path=None returns all pipelines."""
    await db.create_pipeline(
        id="p1",
        description="a",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-a",
        project_name="proj-a",
    )
    await db.create_pipeline(
        id="p2",
        description="b",
        project_dir="/tmp",
    )
    all_pipes = await db.list_pipelines(project_path=None)
    assert len(all_pipes) == 2


async def test_list_projects(db):
    """list_projects should return unique projects with counts."""
    await db.create_pipeline(
        id="p1",
        description="a",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-a",
        project_name="proj-a",
    )
    await db.create_pipeline(
        id="p2",
        description="b",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-a",
        project_name="proj-a",
    )
    await db.create_pipeline(
        id="p3",
        description="c",
        project_dir="/tmp",
        project_path="/Users/tarun/proj-b",
        project_name="proj-b",
    )
    # Pipeline with no project_path should be excluded
    await db.create_pipeline(
        id="p4",
        description="d",
        project_dir="/tmp",
    )

    projects = await db.list_projects()
    assert len(projects) == 2

    # Should be ordered by latest_pipeline_at desc
    by_path = {p["project_path"]: p for p in projects}
    assert by_path["/Users/tarun/proj-a"]["project_name"] == "proj-a"
    assert by_path["/Users/tarun/proj-a"]["pipeline_count"] == 2
    assert by_path["/Users/tarun/proj-a"]["latest_pipeline_at"] is not None
    assert by_path["/Users/tarun/proj-b"]["pipeline_count"] == 1


async def test_list_projects_empty(db):
    """list_projects on empty DB returns empty list."""
    projects = await db.list_projects()
    assert projects == []


async def test_get_pipeline_export_data_not_found(db):
    """get_pipeline_export_data returns None for unknown pipeline."""
    result = await db.get_pipeline_export_data("nonexistent-id")
    assert result is None


async def test_get_pipeline_export_data(db):
    """get_pipeline_export_data returns all pipeline and task fields."""
    await db.create_pipeline(
        id="pipe-export",
        description="Export test pipeline",
        project_dir="/tmp/export",
        model_strategy="auto",
        project_name="export-proj",
    )
    await db.create_task(
        id="task-1",
        title="Add validators",
        description="Add pydantic validators to models",
        files=["src/models.py", "src/validators.py"],
        depends_on=[],
        complexity="medium",
        pipeline_id="pipe-export",
        repo_id="default",
    )
    await db.create_task(
        id="task-2",
        title="Write tests",
        description="Write unit tests",
        files=[],
        depends_on=["task-1"],
        complexity="low",
        pipeline_id="pipe-export",
    )

    data = await db.get_pipeline_export_data("pipe-export")

    assert data is not None
    # Pipeline-level fields
    assert data["id"] == "pipe-export"
    assert data["description"] == "Export test pipeline"
    assert data["status"] == "planning"
    assert data["model_strategy"] == "auto"
    assert data["project_name"] == "export-proj"
    assert data["base_branch"] is None
    assert data["branch_name"] is None
    assert data["pr_url"] is None
    assert data["duration_s"] == 0.0
    assert data["total_cost_usd"] == 0.0
    assert data["planner_cost_usd"] == 0.0
    assert data["total_input_tokens"] == 0
    assert data["total_output_tokens"] == 0
    assert data["tasks_succeeded"] == 0
    assert data["tasks_failed"] == 0
    assert data["total_retries"] == 0

    # Tasks list
    assert len(data["tasks"]) == 2
    by_id = {t["id"]: t for t in data["tasks"]}

    t1 = by_id["task-1"]
    assert t1["title"] == "Add validators"
    assert t1["description"] == "Add pydantic validators to models"
    assert t1["state"] == "todo"
    assert t1["files"] == ["src/models.py", "src/validators.py"]
    assert t1["assigned_agent"] is None
    assert t1["complexity"] == "medium"
    assert t1["repo_id"] == "default"
    assert t1["cost_usd"] == 0.0
    assert t1["agent_cost_usd"] == 0.0
    assert t1["review_cost_usd"] == 0.0
    assert t1["retry_count"] == 0
    assert t1["input_tokens"] == 0
    assert t1["output_tokens"] == 0
    assert t1["started_at"] is None
    assert t1["completed_at"] is None
    assert t1["agent_duration_s"] == 0.0
    assert t1["review_duration_s"] == 0.0
    assert t1["lint_duration_s"] == 0.0
    assert t1["merge_duration_s"] == 0.0
    assert t1["num_turns"] == 0
    assert t1["error_message"] is None

    t2 = by_id["task-2"]
    assert t2["files"] == []
    assert t2["complexity"] == "low"
    assert t2["repo_id"] == "default"


# ── Pipeline analytics tests ─────────────────────────────────────────


async def test_get_pipeline_analytics_empty(db):
    """get_pipeline_analytics on empty DB returns all zeros."""
    result = await db.get_pipeline_analytics()
    assert result == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "partial": 0,
        "cancelled": 0,
        "other": 0,
        "current_streak": 0,
        "longest_streak": 0,
    }


async def _create_pipeline_with_status(db, pid, status, tasks_succeeded=0, tasks_failed=0):
    """Helper: create a pipeline and set its status and task counts."""
    await db.create_pipeline(
        id=pid, description=f"Pipeline {pid}", project_dir="/tmp", model_strategy="auto"
    )
    await db.update_pipeline_status(pid, status)
    # Update task counts directly
    from forge.storage.db import PipelineRow
    from sqlalchemy import update

    async with db._session_factory() as session:
        await session.execute(
            update(PipelineRow)
            .where(PipelineRow.id == pid)
            .values(tasks_succeeded=tasks_succeeded, tasks_failed=tasks_failed)
        )
        await session.commit()


async def test_get_pipeline_analytics_mixed(db):
    """get_pipeline_analytics returns correct counts and streaks for mixed statuses."""
    # Create pipelines in order (created_at is set automatically in order)
    await _create_pipeline_with_status(db, "p1", "done")       # pass
    await _create_pipeline_with_status(db, "p2", "error")      # fail
    await _create_pipeline_with_status(db, "p3", "complete")   # pass
    await _create_pipeline_with_status(db, "p4", "cancelled")  # cancelled
    await _create_pipeline_with_status(db, "p5", "executing", tasks_succeeded=2, tasks_failed=1)  # partial
    await _create_pipeline_with_status(db, "p6", "planning")   # other (no tasks)
    await _create_pipeline_with_status(db, "p7", "done")       # pass — most recent
    await _create_pipeline_with_status(db, "p8", "complete")   # pass — most recent

    result = await db.get_pipeline_analytics()
    assert result["total"] == 8
    assert result["passed"] == 4  # p1, p3, p7, p8
    assert result["failed"] == 1  # p2
    assert result["cancelled"] == 1  # p4
    assert result["partial"] == 1  # p5
    assert result["other"] == 1  # p6
    # Most recent are p8(complete), p7(done) — current streak = 2
    assert result["current_streak"] == 2
    # Longest streak scanning oldest-first: p1(done)=1, p2(error) resets,
    # p3(complete)=1, p4(cancelled) resets, ..., p7(done)=1, p8(complete)=2
    assert result["longest_streak"] == 2


async def test_get_pipeline_analytics_all_passing(db):
    """All-passing pipelines: current_streak == longest_streak == total."""
    await _create_pipeline_with_status(db, "p1", "done")
    await _create_pipeline_with_status(db, "p2", "complete")
    await _create_pipeline_with_status(db, "p3", "done")

    result = await db.get_pipeline_analytics()
    assert result["current_streak"] == 3
    assert result["longest_streak"] == 3


# ── Purge old pipelines tests ────────────────────────────────────────


async def test_purge_old_pipelines_deletes_old(db):
    """purge_old_pipelines deletes old pipelines and keeps recent ones."""
    from datetime import UTC, datetime, timedelta

    from forge.storage.db import PipelineRow
    from sqlalchemy import update

    # Create two pipelines
    await db.create_pipeline(
        id="old-pipe", description="Old", project_dir="/tmp", model_strategy="auto"
    )
    await db.create_pipeline(
        id="new-pipe", description="New", project_dir="/tmp", model_strategy="auto"
    )

    # Set old-pipe's created_at to 60 days ago
    old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    async with db._session_factory() as session:
        await session.execute(
            update(PipelineRow).where(PipelineRow.id == "old-pipe").values(created_at=old_date)
        )
        await session.commit()

    deleted = await db.purge_old_pipelines(older_than_days=30)
    assert deleted == 1

    # old-pipe should be gone, new-pipe should remain
    assert await db.get_pipeline("old-pipe") is None
    assert await db.get_pipeline("new-pipe") is not None


async def test_purge_old_pipelines_deletes_tasks(db):
    """purge_old_pipelines also deletes associated tasks."""
    from datetime import UTC, datetime, timedelta

    from forge.storage.db import PipelineRow
    from sqlalchemy import update

    await db.create_pipeline(
        id="old-pipe", description="Old", project_dir="/tmp", model_strategy="auto"
    )
    await db.create_task(
        id="task-old",
        title="Old task",
        description="Belongs to old pipeline",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="old-pipe",
    )

    old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    async with db._session_factory() as session:
        await session.execute(
            update(PipelineRow).where(PipelineRow.id == "old-pipe").values(created_at=old_date)
        )
        await session.commit()

    deleted = await db.purge_old_pipelines(older_than_days=30)
    assert deleted == 1

    # Task should also be gone
    task = await db.get_task("task-old")
    assert task is None


async def test_purge_old_pipelines_nothing_to_delete(db):
    """purge_old_pipelines returns 0 when nothing is old enough."""
    await db.create_pipeline(
        id="recent", description="Recent", project_dir="/tmp", model_strategy="auto"
    )
    deleted = await db.purge_old_pipelines(older_than_days=30)
    assert deleted == 0


# ── Pipeline trends total_tasks test ─────────────────────────────────


async def test_get_pipeline_trends_includes_total_tasks(db):
    """get_pipeline_trends includes total_tasks field."""
    from forge.storage.db import PipelineRow
    from sqlalchemy import update

    await db.create_pipeline(
        id="t-pipe", description="Trend test", project_dir="/tmp", model_strategy="auto"
    )
    async with db._session_factory() as session:
        await session.execute(
            update(PipelineRow)
            .where(PipelineRow.id == "t-pipe")
            .values(tasks_succeeded=3, tasks_failed=1)
        )
        await session.commit()

    trends = await db.get_pipeline_trends()
    assert len(trends) == 1
    assert trends[0]["total_tasks"] == 4
    assert trends[0]["tasks_succeeded"] == 3
    assert trends[0]["tasks_failed"] == 1
