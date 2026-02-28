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
    await db.create_pipeline(id="pipe-1", description="t", project_dir="/tmp", model_strategy="auto")
    await db.update_pipeline_status("pipe-1", "executing")
    p = await db.get_pipeline("pipe-1")
    assert p.status == "executing"


async def test_set_pipeline_plan(db):
    await db.create_pipeline(id="pipe-1", description="t", project_dir="/tmp", model_strategy="auto")
    await db.set_pipeline_plan("pipe-1", '{"tasks": []}')
    p = await db.get_pipeline("pipe-1")
    assert p.task_graph_json == '{"tasks": []}'


async def test_list_pipelines(db):
    await db.create_pipeline(id="p1", description="a", project_dir="/tmp", model_strategy="auto", user_id="u1")
    await db.create_pipeline(id="p2", description="b", project_dir="/tmp", model_strategy="auto", user_id="u2")
    all_pipes = await db.list_pipelines()
    assert len(all_pipes) == 2
    user_pipes = await db.list_pipelines(user_id="u1")
    assert len(user_pipes) == 1
    assert user_pipes[0].id == "p1"
