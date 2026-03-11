import pytest
from forge.storage.db import Database


@pytest.fixture
async def db():
    d = Database("sqlite+aiosqlite:///:memory:")
    await d.initialize()
    yield d
    await d.close()


async def test_create_task_question(db):
    # Setup: create a pipeline and task first
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="Test", description="desc", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")

    q = await db.create_task_question(
        task_id="t1",
        pipeline_id="p1",
        question="Which ORM pattern?",
        suggestions=["SQLAlchemy 2.0", "Raw SQL"],
    )
    assert q is not None
    assert q.question == "Which ORM pattern?"
    assert q.answer is None
    assert q.answered_by is None


async def test_answer_question(db):
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="Test", description="desc", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")
    q = await db.create_task_question(task_id="t1", pipeline_id="p1", question="Which?", suggestions=["A", "B"])

    await db.answer_question(q.id, "A", "human")
    questions = await db.get_task_questions("t1")
    assert len(questions) == 1
    assert questions[0].answer == "A"
    assert questions[0].answered_by == "human"
    assert questions[0].answered_at is not None


async def test_get_pending_questions(db):
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="T1", description="d", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")
    await db.create_task(id="t2", title="T2", description="d", files=["b.py"], depends_on=[], complexity="low", pipeline_id="p1")

    q1 = await db.create_task_question(task_id="t1", pipeline_id="p1", question="Q1")
    q2 = await db.create_task_question(task_id="t2", pipeline_id="p1", question="Q2")
    await db.answer_question(q1.id, "Answer", "human")

    pending = await db.get_pending_questions("p1")
    assert len(pending) == 1
    assert pending[0].id == q2.id


async def test_expired_questions(db):
    from forge.storage.db import TaskQuestionRow

    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="T1", description="d", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")

    q = await db.create_task_question(task_id="t1", pipeline_id="p1", question="Q?")
    # Override created_at to be old
    async with db._session_factory() as session:
        row = await session.get(TaskQuestionRow, q.id)
        row.created_at = "2020-01-01T00:00:00+00:00"
        await session.commit()

    expired = await db.get_expired_questions(timeout_seconds=60)
    assert len(expired) == 1
    assert expired[0].id == q.id


async def test_task_session_id_column(db):
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="T1", description="d", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")
    task = await db.get_task("t1")
    assert task.session_id is None
    assert task.questions_asked == 0
    assert task.questions_limit == 3
