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
