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
