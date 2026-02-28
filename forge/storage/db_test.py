import pytest
from forge.storage.db import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


async def test_create_and_get_task(db: Database):
    await db.create_task(
        id="task-1",
        title="Test task",
        description="A test",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    task = await db.get_task("task-1")
    assert task is not None
    assert task.title == "Test task"
    assert task.state == "todo"


async def test_get_nonexistent_task(db: Database):
    task = await db.get_task("nope")
    assert task is None


async def test_update_task_state(db: Database):
    await db.create_task(
        id="task-1",
        title="Test",
        description="A test",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    await db.update_task_state("task-1", "in_progress")
    task = await db.get_task("task-1")
    assert task.state == "in_progress"


async def test_list_tasks_by_state(db: Database):
    await db.create_task(
        id="t1", title="T1", description="D", files=["a.py"],
        depends_on=[], complexity="low",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=["b.py"],
        depends_on=[], complexity="low",
    )
    await db.update_task_state("t1", "in_progress")
    in_progress = await db.list_tasks(state="in_progress")
    assert len(in_progress) == 1
    assert in_progress[0].id == "t1"


async def test_create_and_get_agent(db: Database):
    await db.create_agent(id="agent-1")
    agent = await db.get_agent("agent-1")
    assert agent is not None
    assert agent.state == "idle"


async def test_assign_task_to_agent(db: Database):
    await db.create_task(
        id="task-1", title="T", description="D", files=["a.py"],
        depends_on=[], complexity="low",
    )
    await db.create_agent(id="agent-1")
    await db.assign_task("task-1", "agent-1")
    task = await db.get_task("task-1")
    assert task.assigned_agent == "agent-1"
    agent = await db.get_agent("agent-1")
    assert agent.current_task == "task-1"
    assert agent.state == "working"


async def test_get_task_counts_by_state_empty_db(db: Database):
    counts = await db.get_task_counts_by_state()
    assert counts == {}


async def test_get_task_counts_by_state_counts_each_state(db: Database):
    await db.create_task(
        id="t1", title="T1", description="D", files=[], depends_on=[], complexity="low",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=[], depends_on=[], complexity="low",
    )
    await db.create_task(
        id="t3", title="T3", description="D", files=[], depends_on=[], complexity="low",
    )
    await db.update_task_state("t2", "in_progress")
    await db.update_task_state("t3", "completed")

    counts = await db.get_task_counts_by_state()

    assert counts == {"todo": 1, "in_progress": 1, "completed": 1}


async def test_get_task_counts_by_state_multiple_tasks_same_state(db: Database):
    for i in range(4):
        await db.create_task(
            id=f"t{i}", title=f"T{i}", description="D", files=[], depends_on=[], complexity="low",
        )
    await db.update_task_state("t0", "failed")
    await db.update_task_state("t1", "failed")
    await db.update_task_state("t2", "failed")

    counts = await db.get_task_counts_by_state()

    assert counts["failed"] == 3
    assert counts["todo"] == 1
    assert len(counts) == 2


async def test_create_task_with_pipeline_id(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test pipeline",
        project_dir="/tmp", model_strategy="auto",
    )
    await db.create_task(
        id="task-1", title="Test task", description="A test",
        files=["a.py"], depends_on=[], complexity="low",
        pipeline_id="pipe-1",
    )
    task = await db.get_task("task-1")
    assert task is not None
    assert task.pipeline_id == "pipe-1"


async def test_list_tasks_by_pipeline(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="P1", project_dir="/tmp", model_strategy="auto",
    )
    await db.create_pipeline(
        id="pipe-2", description="P2", project_dir="/tmp", model_strategy="auto",
    )
    await db.create_task(
        id="t1", title="T1", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-1",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=["b.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-2",
    )
    tasks = await db.list_tasks_by_pipeline("pipe-1")
    assert len(tasks) == 1
    assert tasks[0].id == "t1"


async def test_set_pipeline_pr_url(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.set_pipeline_pr_url("pipe-1", "https://github.com/user/repo/pull/42")
    pipeline = await db.get_pipeline("pipe-1")
    assert pipeline.pr_url == "https://github.com/user/repo/pull/42"
