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
        id="t1",
        title="T1",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    await db.create_task(
        id="t2",
        title="T2",
        description="D",
        files=["b.py"],
        depends_on=[],
        complexity="low",
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
        id="task-1",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    await db.create_agent(id="agent-1")
    await db.assign_task("task-1", "agent-1")
    task = await db.get_task("task-1")
    assert task.assigned_agent == "agent-1"
    agent = await db.get_agent("agent-1")
    assert agent.current_task == "task-1"
    assert agent.state == "working"


async def test_force_release_agent(db: Database):
    """force_release_agent resets agent to idle via raw SQL."""
    await db.create_task(
        id="task-1",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    await db.create_agent(id="agent-1")
    await db.assign_task("task-1", "agent-1")
    agent = await db.get_agent("agent-1")
    assert agent.state == "working"
    assert agent.current_task == "task-1"

    await db.force_release_agent("agent-1")
    agent = await db.get_agent("agent-1")
    assert agent.state == "idle"
    assert agent.current_task is None


async def test_create_task_with_pipeline_id(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="Test pipeline",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="task-1",
        title="Test task",
        description="A test",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-1",
    )
    task = await db.get_task("task-1")
    assert task is not None
    assert task.pipeline_id == "pipe-1"


async def test_list_tasks_by_pipeline(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="P1",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_pipeline(
        id="pipe-2",
        description="P2",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t1",
        title="T1",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-1",
    )
    await db.create_task(
        id="t2",
        title="T2",
        description="D",
        files=["b.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-2",
    )
    tasks = await db.list_tasks_by_pipeline("pipe-1")
    assert len(tasks) == 1
    assert tasks[0].id == "t1"


async def test_migrate_adds_missing_columns():
    """Verify initialize() adds columns missing from a stale DB schema."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Step 1: create a DB with old schema (no pipeline_id on tasks, no pr_url on pipelines)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE tasks ("
                "  id VARCHAR PRIMARY KEY, title VARCHAR NOT NULL, description VARCHAR NOT NULL,"
                "  files JSON NOT NULL, depends_on JSON NOT NULL, complexity VARCHAR NOT NULL,"
                "  state VARCHAR NOT NULL DEFAULT 'todo', assigned_agent VARCHAR,"
                "  retry_count INTEGER NOT NULL DEFAULT 0, branch_name VARCHAR, worktree_path VARCHAR"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE pipelines ("
                "  id VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, project_dir VARCHAR NOT NULL,"
                "  status VARCHAR NOT NULL DEFAULT 'planning', model_strategy VARCHAR NOT NULL DEFAULT 'auto',"
                "  task_graph_json VARCHAR, user_id VARCHAR,"
                "  created_at VARCHAR, completed_at VARCHAR"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE agents ("
                "  id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL DEFAULT 'idle', current_task VARCHAR"
                ")"
            )
        )

    # Step 2: wrap in Database and call initialize() — should add missing columns
    from forge.storage.db import Database as DB

    db = DB.__new__(DB)
    db._engine = engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    db._session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await db.initialize()

    # Step 3: operations that touch new columns should work
    await db.create_task(
        id="t1",
        title="T",
        description="D",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-1",
    )
    task = await db.get_task("t1")
    assert task.pipeline_id == "pipe-1"

    await db.create_pipeline(
        id="pipe-1",
        description="P",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.set_pipeline_pr_url("pipe-1", "https://github.com/pull/1")
    pipeline = await db.get_pipeline("pipe-1")
    assert pipeline.pr_url == "https://github.com/pull/1"

    await engine.dispose()


async def test_migrate_adds_project_columns():
    """Verify initialize() adds project_path/project_name to an old pipelines table."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Create a DB with old schema (no project_path/project_name on pipelines)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE pipelines ("
                "  id VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, project_dir VARCHAR NOT NULL,"
                "  status VARCHAR NOT NULL DEFAULT 'planning', model_strategy VARCHAR NOT NULL DEFAULT 'auto',"
                "  task_graph_json VARCHAR, user_id VARCHAR,"
                "  created_at VARCHAR, completed_at VARCHAR, pr_url VARCHAR,"
                "  base_branch VARCHAR, branch_name VARCHAR, cancelled_at VARCHAR,"
                "  build_cmd VARCHAR, test_cmd VARCHAR,"
                "  planner_cost_usd FLOAT DEFAULT 0.0, total_cost_usd FLOAT DEFAULT 0.0,"
                "  budget_limit_usd FLOAT DEFAULT 0.0, estimated_cost_usd FLOAT DEFAULT 0.0,"
                "  paused BOOLEAN DEFAULT 0, conventions_json TEXT, require_approval BOOLEAN DEFAULT 0,"
                "  github_issue_url VARCHAR, github_issue_number INTEGER,"
                "  template_id VARCHAR, template_config_json TEXT, contracts_json TEXT,"
                "  paused_at VARCHAR, paused_duration FLOAT DEFAULT 0.0"
                ")"
            )
        )
        # Insert a row with old schema (no project columns)
        await conn.execute(
            text(
                "INSERT INTO pipelines (id, description, project_dir, created_at) "
                "VALUES ('old-pipe', 'Old pipeline', '/tmp/old', '2025-01-01T00:00:00+00:00')"
            )
        )
        # Also create minimal tasks/agents tables to avoid errors
        await conn.execute(
            text(
                "CREATE TABLE tasks ("
                "  id VARCHAR PRIMARY KEY, title VARCHAR NOT NULL, description VARCHAR NOT NULL,"
                "  files JSON NOT NULL, depends_on JSON NOT NULL, complexity VARCHAR NOT NULL,"
                "  state VARCHAR NOT NULL DEFAULT 'todo', assigned_agent VARCHAR,"
                "  retry_count INTEGER NOT NULL DEFAULT 0, branch_name VARCHAR, worktree_path VARCHAR,"
                "  pipeline_id VARCHAR, review_feedback VARCHAR, retry_reason VARCHAR,"
                "  cost_usd FLOAT DEFAULT 0.0, agent_cost_usd FLOAT DEFAULT 0.0,"
                "  review_cost_usd FLOAT DEFAULT 0.0, input_tokens INTEGER DEFAULT 0,"
                "  output_tokens INTEGER DEFAULT 0, approval_context VARCHAR, prior_diff VARCHAR,"
                "  implementation_summary VARCHAR, session_id VARCHAR,"
                "  questions_asked INTEGER DEFAULT 0, questions_limit INTEGER DEFAULT 3"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE agents ("
                "  id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL DEFAULT 'idle', current_task VARCHAR"
                ")"
            )
        )

    # Wrap in Database and call initialize() — should add project_path/project_name columns
    from forge.storage.db import Database as DB

    db = DB.__new__(DB)
    db._engine = engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    db._session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await db.initialize()

    # Old row should still be intact with null project columns
    old = await db.get_pipeline("old-pipe")
    assert old is not None
    assert old.description == "Old pipeline"
    assert old.project_path is None
    assert old.project_name is None

    # New pipeline with project columns should work
    await db.create_pipeline(
        id="new-pipe",
        description="New pipeline",
        project_dir="/tmp/new",
        project_path="/Users/tarun/my-project",
        project_name="my-project",
    )
    new = await db.get_pipeline("new-pipe")
    assert new.project_path == "/Users/tarun/my-project"
    assert new.project_name == "my-project"

    await engine.dispose()


async def test_set_pipeline_pr_url(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.set_pipeline_pr_url("pipe-1", "https://github.com/user/repo/pull/42")
    pipeline = await db.get_pipeline("pipe-1")
    assert pipeline.pr_url == "https://github.com/user/repo/pull/42"


async def test_log_event(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.log_event(
        pipeline_id="pipe-1",
        task_id="task-1",
        event_type="agent_output",
        payload={"line": "Hello world"},
    )
    events = await db.list_events("pipe-1")
    assert len(events) == 1
    assert events[0].event_type == "agent_output"
    assert events[0].task_id == "task-1"


async def test_list_events_ordered_by_created_at(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    for i in range(5):
        await db.log_event(
            pipeline_id="pipe-1",
            task_id=None,
            event_type="phase_change",
            payload={"phase": f"phase_{i}"},
        )
    events = await db.list_events("pipe-1")
    assert len(events) == 5
    # Oldest first
    assert events[0].payload["phase"] == "phase_0"


async def test_list_events_by_task(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.log_event(
        pipeline_id="pipe-1", task_id="t1", event_type="agent_output", payload={"line": "a"}
    )
    await db.log_event(
        pipeline_id="pipe-1", task_id="t2", event_type="agent_output", payload={"line": "b"}
    )
    events = await db.list_events("pipe-1", task_id="t1")
    assert len(events) == 1
    assert events[0].payload["line"] == "a"


async def test_add_task_cost(db: Database):
    await db.create_task(
        id="t1",
        title="T",
        description="D",
        files=[],
        depends_on=[],
        complexity="low",
    )
    await db.add_task_cost("t1", 0.05)
    await db.add_task_cost("t1", 0.03)
    task = await db.get_task("t1")
    assert abs(task.cost_usd - 0.08) < 0.001


async def test_list_events_by_type(db: Database):
    await db.create_pipeline(
        id="pipe-1",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.log_event(pipeline_id="pipe-1", task_id=None, event_type="phase_change", payload={})
    await db.log_event(pipeline_id="pipe-1", task_id="t1", event_type="review_update", payload={})
    events = await db.list_events("pipe-1", event_type="review_update")
    assert len(events) == 1


# ── branch_name tests ──────────────────────────────────────────────


async def test_create_pipeline_with_branch_name(db: Database):
    """create_pipeline should store branch_name."""
    await db.create_pipeline(
        id="pipe-bn",
        description="Branch test",
        project_dir="/tmp",
        model_strategy="auto",
        branch_name="feat/my-feature",
    )
    pipeline = await db.get_pipeline("pipe-bn")
    assert pipeline is not None
    assert pipeline.branch_name == "feat/my-feature"


async def test_create_pipeline_branch_name_defaults_to_none(db: Database):
    """create_pipeline without branch_name should default to None."""
    await db.create_pipeline(
        id="pipe-no-bn",
        description="No branch",
        project_dir="/tmp",
        model_strategy="auto",
    )
    pipeline = await db.get_pipeline("pipe-no-bn")
    assert pipeline.branch_name is None


# ── restart_pipeline tests ──────────────────────────────────────────


async def test_restart_pipeline_resets_state(db: Database):
    """restart_pipeline should reset pipeline and tasks, delete events."""
    # Setup: create pipeline with tasks and events
    await db.create_pipeline(
        id="pipe-r1",
        description="Restart test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.set_pipeline_plan("pipe-r1", '{"tasks": []}')
    await db.update_pipeline_status("pipe-r1", "executing")

    await db.create_task(
        id="t1",
        title="T1",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-r1",
    )
    await db.create_task(
        id="t2",
        title="T2",
        description="D",
        files=["b.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-r1",
    )
    await db.update_task_state("t1", "done")
    await db.update_task_state("t2", "in_progress")

    # Add some events
    await db.log_event(
        pipeline_id="pipe-r1", task_id="t1", event_type="agent_output", payload={"line": "hi"}
    )
    await db.log_event(
        pipeline_id="pipe-r1", task_id="t2", event_type="agent_output", payload={"line": "bye"}
    )

    # Act
    result = await db.restart_pipeline("pipe-r1")

    # Assert pipeline reset
    pipeline = await db.get_pipeline("pipe-r1")
    assert pipeline.status == "pending"
    assert pipeline.task_graph_json is None
    assert pipeline.completed_at is None
    assert pipeline.pr_url is None

    # Assert tasks deleted (so re-planning can create fresh rows with same IDs)
    assert result["tasks_reset"] == 2
    tasks = await db.list_tasks_by_pipeline("pipe-r1")
    assert len(tasks) == 0

    # Assert events deleted
    assert result["events_deleted"] == 2
    events = await db.list_events("pipe-r1")
    assert len(events) == 0


async def test_restart_pipeline_nonexistent_returns_zeros(db: Database):
    """restart_pipeline on a nonexistent pipeline should return zero counts."""
    result = await db.restart_pipeline("nonexistent")
    assert result == {"tasks_reset": 0, "events_deleted": 0}


# ── cancel_pipeline_hard tests ──────────────────────────────────────


async def test_cancel_pipeline_hard_marks_cancelled(db: Database):
    """cancel_pipeline_hard should cancel pipeline and non-terminal tasks."""
    await db.create_pipeline(
        id="pipe-c1",
        description="Cancel test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t1",
        title="T1",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-c1",
    )
    await db.create_task(
        id="t2",
        title="T2",
        description="D",
        files=["b.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-c1",
    )
    await db.create_task(
        id="t3",
        title="T3",
        description="D",
        files=["c.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-c1",
    )
    # t1 is in progress, t2 is done, t3 is todo
    await db.update_task_state("t1", "in_progress")
    await db.update_task_state("t2", "done")

    result = await db.cancel_pipeline_hard("pipe-c1")

    # Only t1 (in_progress) and t3 (todo) should be cancelled
    assert result["tasks_cancelled"] == 2

    pipeline = await db.get_pipeline("pipe-c1")
    assert pipeline.status == "cancelled"
    assert pipeline.cancelled_at is not None

    # Verify individual task states
    t1 = await db.get_task("t1")
    assert t1.state == "cancelled"
    t2 = await db.get_task("t2")
    assert t2.state == "done"  # terminal, should not change
    t3 = await db.get_task("t3")
    assert t3.state == "cancelled"


async def test_cancel_pipeline_hard_nonexistent_returns_zero(db: Database):
    """cancel_pipeline_hard on nonexistent pipeline should return zero."""
    result = await db.cancel_pipeline_hard("nonexistent")
    assert result == {"tasks_cancelled": 0}


async def test_cancel_pipeline_hard_skips_error_tasks(db: Database):
    """cancel_pipeline_hard should not cancel tasks in 'error' state."""
    await db.create_pipeline(
        id="pipe-c2",
        description="Cancel skip error",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="te1",
        title="T1",
        description="D",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-c2",
    )
    await db.update_task_state("te1", "error")

    result = await db.cancel_pipeline_hard("pipe-c2")
    assert result["tasks_cancelled"] == 0

    t = await db.get_task("te1")
    assert t.state == "error"


# ── approve_task_atomically tests ────────────────────────────────────


async def test_approve_task_atomically_success(db: Database):
    """approve_task_atomically transitions awaiting_approval -> merging."""
    await db.create_pipeline(
        id="pipe-ap",
        description="Approve test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t-ap",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-ap",
    )
    await db.update_task_state("t-ap", "awaiting_approval")

    task = await db.approve_task_atomically("t-ap", "pipe-ap")
    assert task is not None
    assert task.state == "merging"

    # Verify persisted
    fetched = await db.get_task("t-ap")
    assert fetched.state == "merging"


async def test_approve_task_atomically_wrong_state_raises(db: Database):
    """approve_task_atomically raises ValueError if task not awaiting_approval."""
    await db.create_pipeline(
        id="pipe-ap2",
        description="Approve test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t-ap2",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-ap2",
    )
    await db.update_task_state("t-ap2", "done")

    with pytest.raises(ValueError, match="not 'awaiting_approval'"):
        await db.approve_task_atomically("t-ap2", "pipe-ap2")


async def test_approve_task_atomically_not_found_returns_none(db: Database):
    """approve_task_atomically returns None for nonexistent task."""
    result = await db.approve_task_atomically("nonexistent", "pipe-x")
    assert result is None


async def test_approve_task_atomically_wrong_pipeline_returns_none(db: Database):
    """approve_task_atomically returns None if pipeline_id doesn't match."""
    await db.create_pipeline(
        id="pipe-ap3",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t-ap3",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-ap3",
    )
    await db.update_task_state("t-ap3", "awaiting_approval")

    result = await db.approve_task_atomically("t-ap3", "wrong-pipeline")
    assert result is None


# ── atomic cost accumulation tests ───────────────────────────────────


async def test_add_task_agent_cost_accumulates(db: Database):
    """add_task_agent_cost should atomically accumulate cost and tokens."""
    await db.create_task(
        id="t-ac",
        title="T",
        description="D",
        files=[],
        depends_on=[],
        complexity="low",
    )
    await db.add_task_agent_cost("t-ac", 0.10, 100, 50)
    await db.add_task_agent_cost("t-ac", 0.05, 200, 100)

    task = await db.get_task("t-ac")
    assert abs(task.agent_cost_usd - 0.15) < 0.001
    assert abs(task.cost_usd - 0.15) < 0.001
    assert task.input_tokens == 300
    assert task.output_tokens == 150


async def test_add_task_review_cost_accumulates(db: Database):
    """add_task_review_cost should atomically accumulate review cost."""
    await db.create_task(
        id="t-rc",
        title="T",
        description="D",
        files=[],
        depends_on=[],
        complexity="low",
    )
    await db.add_task_review_cost("t-rc", 0.02)
    await db.add_task_review_cost("t-rc", 0.03)

    task = await db.get_task("t-rc")
    assert abs(task.review_cost_usd - 0.05) < 0.001
    assert abs(task.cost_usd - 0.05) < 0.001


async def test_add_pipeline_cost_accumulates(db: Database):
    """add_pipeline_cost should atomically accumulate pipeline total cost."""
    await db.create_pipeline(
        id="pipe-cost",
        description="Cost test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    await db.add_pipeline_cost("pipe-cost", 0.10)
    await db.add_pipeline_cost("pipe-cost", 0.25)

    cost = await db.get_pipeline_cost("pipe-cost")
    assert abs(cost - 0.35) < 0.001


async def test_executor_tracking_columns(db: Database):
    """executor_pid and executor_token can be set and cleared on a pipeline."""
    await db.create_pipeline(
        id="pipe-exec",
        description="test",
        project_dir="/tmp",
        model_strategy="balanced",
        budget_limit_usd=10,
    )
    await db.set_executor_info("pipe-exec", pid=12345, token="abc-123")
    p = await db.get_pipeline("pipe-exec")
    assert p.executor_pid == 12345
    assert p.executor_token == "abc-123"

    await db.clear_executor_info("pipe-exec")
    p = await db.get_pipeline("pipe-exec")
    assert p.executor_pid is None
    assert p.executor_token is None


# ── TaskQuestionRow stage column tests ────────────────────────────────


async def test_create_task_question_with_stage(db: Database):
    """create_task_question should accept and persist a stage parameter."""
    q = await db.create_task_question(
        task_id="__planning__",
        pipeline_id="pipe-stage",
        question="JWT or session?",
        stage="planning",
    )
    assert q.stage == "planning"


async def test_create_task_question_stage_defaults_none(db: Database):
    """stage should default to None for backward compatibility."""
    q = await db.create_task_question(
        task_id="t1",
        pipeline_id="pipe-stage2",
        question="Which pattern?",
    )
    assert q.stage is None


async def test_get_planning_questions(db: Database):
    """get_planning_questions should return only planning-stage questions."""
    await db.create_task_question(
        task_id="__planning__",
        pipeline_id="pipe-pq",
        question="Auth approach?",
        stage="planning",
    )
    await db.create_task_question(
        task_id="t1",
        pipeline_id="pipe-pq",
        question="File format?",
        stage=None,
    )
    await db.create_task_question(
        task_id="__planning__",
        pipeline_id="pipe-pq",
        question="DB choice?",
        stage="planning",
    )
    questions = await db.get_planning_questions("pipe-pq")
    assert len(questions) == 2
    assert questions[0].question == "Auth approach?"
    assert questions[1].question == "DB choice?"


# ── InterjectionRow tests ─────────────────────────────────────────────


async def test_database_async_context_manager():
    """Database should work as an async context manager."""
    async with Database("sqlite+aiosqlite:///:memory:") as db:
        # Should be initialized — create a task to verify
        await db.create_task(
            id="t-ctx",
            title="Context test",
            description="D",
            files=["a.py"],
            depends_on=[],
            complexity="low",
        )
        task = await db.get_task("t-ctx")
        assert task is not None
        assert task.title == "Context test"
    # After exiting, the engine should be disposed (no error expected)


async def test_create_interjection(db):
    """Should create an interjection row with delivered=False."""
    row = await db.create_interjection(
        task_id="t1", pipeline_id="pipe1", message="Use the factory pattern instead"
    )
    assert row.task_id == "t1"
    assert row.message == "Use the factory pattern instead"
    assert row.delivered is False
    assert row.delivered_at is None


async def test_get_pending_interjections(db):
    """Should return only undelivered interjections for a task."""
    await db.create_interjection(task_id="t1", pipeline_id="pipe1", message="msg1")
    await db.create_interjection(task_id="t1", pipeline_id="pipe1", message="msg2")
    await db.create_interjection(task_id="t2", pipeline_id="pipe1", message="other")

    pending = await db.get_pending_interjections("t1")
    assert len(pending) == 2
    assert all(p.task_id == "t1" for p in pending)


async def test_mark_interjection_delivered(db):
    """Marking delivered should set delivered=True and delivered_at."""
    row = await db.create_interjection(task_id="t1", pipeline_id="pipe1", message="msg")
    await db.mark_interjection_delivered(row.id)

    pending = await db.get_pending_interjections("t1")
    assert len(pending) == 0


# ── repo_id / repos_json tests ────────────────────────────────────────


async def test_task_repo_id_defaults_to_default(db: Database):
    """create_task without repo_id should default to 'default'."""
    await db.create_task(
        id="t-repo-def",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    task = await db.get_task("t-repo-def")
    assert task.repo_id == "default"


async def test_task_repo_id_custom(db: Database):
    """create_task with repo_id='backend' should store it."""
    await db.create_task(
        id="t-repo-be",
        title="T",
        description="D",
        files=["a.py"],
        depends_on=[],
        complexity="low",
        repo_id="backend",
    )
    task = await db.get_task("t-repo-be")
    assert task.repo_id == "backend"


async def test_pipeline_repos_json_defaults_to_none(db: Database):
    """create_pipeline without repos_json should default to None."""
    await db.create_pipeline(
        id="pipe-rj-def",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    pipeline = await db.get_pipeline("pipe-rj-def")
    assert pipeline.repos_json is None


async def test_pipeline_repos_json_stored(db: Database):
    """create_pipeline with repos_json should store the JSON string."""
    import json

    repos = [{"id": "backend", "path": "/tmp/be", "base_branch": "main", "branch_name": "feat/x"}]
    repos_str = json.dumps(repos)
    await db.create_pipeline(
        id="pipe-rj-set",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
        repos_json=repos_str,
    )
    pipeline = await db.get_pipeline("pipe-rj-set")
    assert pipeline.repos_json == repos_str


async def test_pipeline_get_repos_single_repo(db: Database):
    """get_repos() with no repos_json returns synthetic default entry."""
    await db.create_pipeline(
        id="pipe-gr-single",
        description="Test",
        project_dir="/tmp/project",
        model_strategy="auto",
        base_branch="main",
        branch_name="feat/test",
    )
    pipeline = await db.get_pipeline("pipe-gr-single")
    repos = pipeline.get_repos()
    assert len(repos) == 1
    assert repos[0]["id"] == "default"
    assert repos[0]["path"] == "/tmp/project"
    assert repos[0]["base_branch"] == "main"
    assert repos[0]["branch_name"] == "feat/test"


async def test_pipeline_get_repos_multi_repo(db: Database):
    """get_repos() with repos_json parses and returns the list."""
    import json

    repos = [
        {"id": "backend", "path": "/tmp/be", "base_branch": "main", "branch_name": "feat/x"},
        {"id": "frontend", "path": "/tmp/fe", "base_branch": "develop", "branch_name": "feat/y"},
    ]
    await db.create_pipeline(
        id="pipe-gr-multi",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
        repos_json=json.dumps(repos),
    )
    pipeline = await db.get_pipeline("pipe-gr-multi")
    result = pipeline.get_repos()
    assert len(result) == 2
    assert result[0]["id"] == "backend"
    assert result[1]["id"] == "frontend"
    assert result[1]["base_branch"] == "develop"


async def test_pipeline_get_repos_no_base_branch_raises(db: Database):
    """get_repos() without base_branch or repos_json should raise ValueError."""
    await db.create_pipeline(
        id="pipe-gr-err",
        description="Test",
        project_dir="/tmp",
        model_strategy="auto",
    )
    pipeline = await db.get_pipeline("pipe-gr-err")
    with pytest.raises(ValueError, match="has no base_branch set"):
        pipeline.get_repos()


async def test_migrate_adds_repo_id_column():
    """Verify initialize() adds repo_id column to an old tasks table."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Create a DB with old schema (no repo_id on tasks)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE tasks ("
                "  id VARCHAR PRIMARY KEY, title VARCHAR NOT NULL, description VARCHAR NOT NULL,"
                "  files JSON NOT NULL, depends_on JSON NOT NULL, complexity VARCHAR NOT NULL,"
                "  state VARCHAR NOT NULL DEFAULT 'todo', assigned_agent VARCHAR,"
                "  retry_count INTEGER NOT NULL DEFAULT 0, branch_name VARCHAR, worktree_path VARCHAR,"
                "  pipeline_id VARCHAR, review_feedback VARCHAR, retry_reason VARCHAR,"
                "  cost_usd FLOAT DEFAULT 0.0, agent_cost_usd FLOAT DEFAULT 0.0,"
                "  review_cost_usd FLOAT DEFAULT 0.0, input_tokens INTEGER DEFAULT 0,"
                "  output_tokens INTEGER DEFAULT 0, approval_context VARCHAR, prior_diff VARCHAR,"
                "  implementation_summary VARCHAR, session_id VARCHAR,"
                "  questions_asked INTEGER DEFAULT 0, questions_limit INTEGER DEFAULT 3,"
                "  review_diff TEXT"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE pipelines ("
                "  id VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, project_dir VARCHAR NOT NULL,"
                "  status VARCHAR NOT NULL DEFAULT 'planning', model_strategy VARCHAR NOT NULL DEFAULT 'auto',"
                "  task_graph_json VARCHAR, user_id VARCHAR,"
                "  created_at VARCHAR, completed_at VARCHAR, pr_url VARCHAR,"
                "  base_branch VARCHAR, branch_name VARCHAR, cancelled_at VARCHAR,"
                "  build_cmd VARCHAR, test_cmd VARCHAR,"
                "  planner_cost_usd FLOAT DEFAULT 0.0, total_cost_usd FLOAT DEFAULT 0.0,"
                "  budget_limit_usd FLOAT DEFAULT 0.0, estimated_cost_usd FLOAT DEFAULT 0.0,"
                "  paused BOOLEAN DEFAULT 0, conventions_json TEXT, require_approval BOOLEAN DEFAULT 0,"
                "  github_issue_url VARCHAR, github_issue_number INTEGER,"
                "  template_id VARCHAR, template_config_json TEXT, contracts_json TEXT,"
                "  paused_at VARCHAR, paused_duration FLOAT DEFAULT 0.0,"
                "  project_path VARCHAR, project_name VARCHAR,"
                "  executor_pid INTEGER, executor_token VARCHAR,"
                "  baseline_exit_code INTEGER, integration_status VARCHAR,"
                "  repos_json TEXT"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE agents ("
                "  id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL DEFAULT 'idle', current_task VARCHAR"
                ")"
            )
        )
        # Insert a task with old schema (no repo_id)
        await conn.execute(
            text(
                "INSERT INTO tasks (id, title, description, files, depends_on, complexity) "
                "VALUES ('old-task', 'Old', 'Old task', '[]', '[]', 'low')"
            )
        )

    # Wrap in Database and call initialize() — should add repo_id column
    from forge.storage.db import Database as DB

    db = DB.__new__(DB)
    db._engine = engine
    db._session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await db.initialize()

    # Old task should still work
    old = await db.get_task("old-task")
    assert old is not None
    assert old.title == "Old"

    # New task with repo_id should work
    await db.create_task(
        id="new-task",
        title="New",
        description="D",
        files=[],
        depends_on=[],
        complexity="low",
        repo_id="backend",
    )
    new = await db.get_task("new-task")
    assert new.repo_id == "backend"

    await engine.dispose()


async def test_migrate_adds_repos_json_column():
    """Verify initialize() adds repos_json column to an old pipelines table."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Create a DB with old schema (no repos_json on pipelines)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE tasks ("
                "  id VARCHAR PRIMARY KEY, title VARCHAR NOT NULL, description VARCHAR NOT NULL,"
                "  files JSON NOT NULL, depends_on JSON NOT NULL, complexity VARCHAR NOT NULL,"
                "  state VARCHAR NOT NULL DEFAULT 'todo', assigned_agent VARCHAR,"
                "  retry_count INTEGER NOT NULL DEFAULT 0, branch_name VARCHAR, worktree_path VARCHAR,"
                "  pipeline_id VARCHAR, review_feedback VARCHAR, retry_reason VARCHAR,"
                "  cost_usd FLOAT DEFAULT 0.0, agent_cost_usd FLOAT DEFAULT 0.0,"
                "  review_cost_usd FLOAT DEFAULT 0.0, input_tokens INTEGER DEFAULT 0,"
                "  output_tokens INTEGER DEFAULT 0, approval_context VARCHAR, prior_diff VARCHAR,"
                "  implementation_summary VARCHAR, session_id VARCHAR,"
                "  questions_asked INTEGER DEFAULT 0, questions_limit INTEGER DEFAULT 3,"
                "  review_diff TEXT, repo_id VARCHAR DEFAULT 'default'"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE pipelines ("
                "  id VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, project_dir VARCHAR NOT NULL,"
                "  status VARCHAR NOT NULL DEFAULT 'planning', model_strategy VARCHAR NOT NULL DEFAULT 'auto',"
                "  task_graph_json VARCHAR, user_id VARCHAR,"
                "  created_at VARCHAR, completed_at VARCHAR, pr_url VARCHAR,"
                "  base_branch VARCHAR, branch_name VARCHAR, cancelled_at VARCHAR,"
                "  build_cmd VARCHAR, test_cmd VARCHAR,"
                "  planner_cost_usd FLOAT DEFAULT 0.0, total_cost_usd FLOAT DEFAULT 0.0,"
                "  budget_limit_usd FLOAT DEFAULT 0.0, estimated_cost_usd FLOAT DEFAULT 0.0,"
                "  paused BOOLEAN DEFAULT 0, conventions_json TEXT, require_approval BOOLEAN DEFAULT 0,"
                "  github_issue_url VARCHAR, github_issue_number INTEGER,"
                "  template_id VARCHAR, template_config_json TEXT, contracts_json TEXT,"
                "  paused_at VARCHAR, paused_duration FLOAT DEFAULT 0.0,"
                "  project_path VARCHAR, project_name VARCHAR,"
                "  executor_pid INTEGER, executor_token VARCHAR,"
                "  baseline_exit_code INTEGER, integration_status VARCHAR"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE agents ("
                "  id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL DEFAULT 'idle', current_task VARCHAR"
                ")"
            )
        )
        # Insert a pipeline with old schema (no repos_json)
        await conn.execute(
            text(
                "INSERT INTO pipelines (id, description, project_dir, created_at) "
                "VALUES ('old-pipe', 'Old', '/tmp/old', '2025-01-01T00:00:00+00:00')"
            )
        )

    # Wrap in Database and call initialize() — should add repos_json column
    import json

    from forge.storage.db import Database as DB

    db = DB.__new__(DB)
    db._engine = engine
    db._session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await db.initialize()

    # Old pipeline should still work
    old = await db.get_pipeline("old-pipe")
    assert old is not None
    assert old.repos_json is None

    # New pipeline with repos_json should work
    repos = [{"id": "backend", "path": "/tmp/be", "base_branch": "main", "branch_name": ""}]
    await db.create_pipeline(
        id="new-pipe",
        description="New",
        project_dir="/tmp/new",
        repos_json=json.dumps(repos),
    )
    new = await db.get_pipeline("new-pipe")
    assert new.repos_json is not None
    assert json.loads(new.repos_json) == repos

    await engine.dispose()


# ── Lesson tests ─────────────────────────────────────────────────────


async def test_add_lesson_returns_id(db: Database):
    """add_lesson should return a lesson ID."""
    lid = await db.add_lesson(
        scope="global",
        category="command_failure",
        title="Always check exit code",
        content="Check exit code",
        trigger="exit code",
        resolution="Use set -e",
    )
    assert lid is not None
    assert len(lid) == 36  # UUID


async def test_add_lesson_pruning(db: Database):
    """add_lesson should prune excess lessons beyond MAX_LESSONS."""
    original_max = Database.MAX_LESSONS
    Database.MAX_LESSONS = 5
    try:
        ids = []
        for i in range(7):
            lid = await db.add_lesson(
                scope="global",
                category="command_failure",
                title=f"Lesson {i}",
                content=f"Content {i}",
                trigger=f"trigger-{i}",
                resolution=f"Resolution {i}",
            )
            ids.append(lid)

        # Should have pruned down to 5
        all_lessons = await db.list_all_lessons()
        assert len(all_lessons) == 5

        # The first 2 (lowest hit_count=1, oldest) should have been pruned
        remaining_ids = {lesson.id for lesson in all_lessons}
        assert ids[0] not in remaining_ids
        assert ids[1] not in remaining_ids
    finally:
        Database.MAX_LESSONS = original_max


async def test_add_lesson_pruning_respects_hit_count(db: Database):
    """Pruning should remove lowest hit_count lessons first."""
    original_max = Database.MAX_LESSONS
    Database.MAX_LESSONS = 3
    try:
        # Add 3 lessons
        lid1 = await db.add_lesson(
            scope="global",
            category="command_failure",
            title="Low hits",
            content="c",
            trigger="t1",
            resolution="r",
        )
        lid2 = await db.add_lesson(
            scope="global",
            category="command_failure",
            title="High hits",
            content="c",
            trigger="t2",
            resolution="r",
        )
        # Bump hit count on lid2 so it's more valuable
        await db.bump_lesson_hit(lid2)
        await db.bump_lesson_hit(lid2)

        await db.add_lesson(
            scope="global",
            category="command_failure",
            title="Medium hits",
            content="c",
            trigger="t3",
            resolution="r",
        )

        # Adding a 4th should prune the lowest hit_count (lid1)
        await db.add_lesson(
            scope="global",
            category="command_failure",
            title="New lesson",
            content="c",
            trigger="t4",
            resolution="r",
        )

        all_lessons = await db.list_all_lessons()
        assert len(all_lessons) == 3
        remaining_ids = {lesson.id for lesson in all_lessons}
        assert lid1 not in remaining_ids  # lowest hit_count, pruned
        assert lid2 in remaining_ids  # high hits, kept
    finally:
        Database.MAX_LESSONS = original_max


async def test_find_matching_lesson_normalized(db: Database):
    """find_matching_lesson should match case-insensitively with normalized whitespace."""
    await db.add_lesson(
        scope="global",
        category="command_failure",
        title="Exit Code Check",
        content="c",
        trigger="always check  EXIT  code",
        resolution="r",
    )

    # Should match with different casing and whitespace
    match = await db.find_matching_lesson("Always Check Exit Code")
    assert match is not None
    assert match.title == "Exit Code Check"

    # Should match as substring
    match2 = await db.find_matching_lesson("check exit code")
    assert match2 is not None

    # Should not match unrelated trigger
    no_match = await db.find_matching_lesson("something completely different")
    assert no_match is None


async def test_find_matching_lesson_reverse_contains(db: Database):
    """find_matching_lesson should match when stored trigger is substring of query."""
    await db.add_lesson(
        scope="global",
        category="code_pattern",
        title="Use set -e",
        content="c",
        trigger="set -e",
        resolution="r",
    )

    # Query contains the stored trigger
    match = await db.find_matching_lesson("always use set -e in bash scripts")
    assert match is not None
    assert match.title == "Use set -e"


async def test_normalize_trigger():
    """_normalize_trigger should lowercase, strip, and collapse whitespace."""
    assert Database._normalize_trigger("  Hello   World  ") == "hello world"
    assert Database._normalize_trigger("ALL\tCAPS\n\nHERE") == "all caps here"
    assert Database._normalize_trigger("simple") == "simple"


# ── get_tasks_by_ids tests ──────────────────────────────────────────


async def test_get_tasks_by_ids_basic(db: Database):
    """Batch-fetch returns all matching tasks."""
    for i in range(3):
        await db.create_task(
            id=f"t-{i}",
            title=f"Task {i}",
            description="d",
            files=[],
            depends_on=[],
            complexity="low",
        )
    results = await db.get_tasks_by_ids(["t-0", "t-1", "t-2"])
    assert len(results) == 3
    ids = {t.id for t in results}
    assert ids == {"t-0", "t-1", "t-2"}


async def test_get_tasks_by_ids_empty_input(db: Database):
    """Empty input returns empty list without querying."""
    results = await db.get_tasks_by_ids([])
    assert results == []


async def test_get_tasks_by_ids_missing_ids(db: Database):
    """Missing IDs are silently omitted."""
    await db.create_task(
        id="t-1",
        title="T",
        description="d",
        files=[],
        depends_on=[],
        complexity="low",
    )
    results = await db.get_tasks_by_ids(["t-1", "nonexistent"])
    assert len(results) == 1
    assert results[0].id == "t-1"


async def test_get_tasks_by_ids_dedup(db: Database):
    """Duplicate IDs in input produce only one TaskRow per unique ID."""
    await db.create_task(
        id="t-1",
        title="T",
        description="d",
        files=[],
        depends_on=[],
        complexity="low",
    )
    results = await db.get_tasks_by_ids(["t-1", "t-1", "t-1"])
    assert len(results) == 1


# ── Resume pipeline persistence tests ─────────────────────────────


async def _create_pipeline_with_tasks(db: Database, pipeline_id: str = "pipe-resume") -> None:
    """Helper: create a pipeline with tasks in various states."""
    await db.create_pipeline(
        id=pipeline_id,
        description="Resume test pipeline",
        project_dir="/tmp/project",
        base_branch="main",
        branch_name="forge/test-123",
    )
    for task_id, state in [
        ("t1", "done"),
        ("t2", "done"),
        ("t3", "in_review"),
        ("t4", "error"),
        ("t5", "blocked"),
    ]:
        await db.create_task(
            id=task_id,
            title=f"Task {task_id}",
            description="d",
            files=[],
            depends_on=[],
            complexity="low",
            pipeline_id=pipeline_id,
        )
        if state != "todo":
            await db.update_task_state(task_id, state)


async def test_set_pipeline_quit_phase(db: Database):
    """set_pipeline_quit_phase stores and retrieves correctly."""
    await db.create_pipeline(
        id="pipe-qp",
        description="Quit phase test",
        project_dir="/tmp",
    )
    await db.set_pipeline_quit_phase("pipe-qp", "executing")
    pipeline = await db.get_pipeline("pipe-qp")
    assert pipeline.quit_phase == "executing"


async def test_quit_phase_defaults_to_none(db: Database):
    """quit_phase defaults to None for new pipelines."""
    await db.create_pipeline(
        id="pipe-qp-none",
        description="Default quit phase",
        project_dir="/tmp",
    )
    pipeline = await db.get_pipeline("pipe-qp-none")
    assert pipeline.quit_phase is None


async def test_set_pipeline_quit_phase_overwrite(db: Database):
    """set_pipeline_quit_phase can overwrite a previous value."""
    await db.create_pipeline(
        id="pipe-qp-ow",
        description="Overwrite test",
        project_dir="/tmp",
    )
    await db.set_pipeline_quit_phase("pipe-qp-ow", "planning")
    await db.set_pipeline_quit_phase("pipe-qp-ow", "final_approval")
    pipeline = await db.get_pipeline("pipe-qp-ow")
    assert pipeline.quit_phase == "final_approval"


async def test_set_pipeline_quit_phase_nonexistent(db: Database):
    """set_pipeline_quit_phase on nonexistent pipeline is a no-op."""
    await db.set_pipeline_quit_phase("nonexistent", "executing")
    # Should not raise


async def test_get_pipeline_resume_context(db: Database):
    """get_pipeline_resume_context returns correct task counts."""
    await _create_pipeline_with_tasks(db)
    await db.set_pipeline_quit_phase("pipe-resume", "executing")
    await db.update_pipeline_status("pipe-resume", "interrupted")

    ctx = await db.get_pipeline_resume_context("pipe-resume")
    assert ctx is not None
    assert ctx["status"] == "interrupted"
    assert ctx["quit_phase"] == "executing"
    assert ctx["project_dir"] == "/tmp/project"
    assert ctx["base_branch"] == "main"
    assert ctx["branch_name"] == "forge/test-123"
    assert ctx["description"] == "Resume test pipeline"
    assert ctx["total_tasks"] == 5
    assert ctx["tasks_done"] == 2
    assert ctx["tasks_error"] == 1
    assert ctx["tasks_in_review"] == 1
    assert ctx["tasks_blocked"] == 1
    assert ctx["pr_url"] is None
    assert ctx["executor_pid"] is None
    assert ctx["task_graph_json"] is None
    assert ctx["contracts_json"] is None


async def test_get_pipeline_resume_context_zero_tasks(db: Database):
    """get_pipeline_resume_context with no tasks returns zero counts."""
    await db.create_pipeline(
        id="pipe-empty",
        description="No tasks",
        project_dir="/tmp",
    )
    ctx = await db.get_pipeline_resume_context("pipe-empty")
    assert ctx is not None
    assert ctx["total_tasks"] == 0
    assert ctx["tasks_done"] == 0
    assert ctx["tasks_error"] == 0
    assert ctx["tasks_in_review"] == 0
    assert ctx["tasks_blocked"] == 0


async def test_get_pipeline_resume_context_nonexistent(db: Database):
    """get_pipeline_resume_context returns None for nonexistent pipeline."""
    ctx = await db.get_pipeline_resume_context("nonexistent")
    assert ctx is None


async def test_get_pipeline_list_with_counts(db: Database):
    """get_pipeline_list_with_counts returns enriched data."""
    await _create_pipeline_with_tasks(db, "pipe-list-1")
    await db.create_pipeline(
        id="pipe-list-2",
        description="Second pipeline",
        project_dir="/tmp/other",
    )
    await db.create_task(
        id="t-list-1",
        title="Solo task",
        description="d",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="pipe-list-2",
    )
    await db.update_task_state("t-list-1", "done")

    results = await db.get_pipeline_list_with_counts(limit=10)
    assert len(results) == 2

    by_id = {r["id"]: r for r in results}

    p1 = by_id["pipe-list-1"]
    assert p1["description"] == "Resume test pipeline"
    assert p1["total_tasks"] == 5
    assert p1["tasks_done"] == 2
    assert p1["tasks_error"] == 1
    assert p1["project_dir"] == "/tmp/project"

    p2 = by_id["pipe-list-2"]
    assert p2["description"] == "Second pipeline"
    assert p2["total_tasks"] == 1
    assert p2["tasks_done"] == 1
    assert p2["tasks_error"] == 0


async def test_get_pipeline_list_with_counts_respects_limit(db: Database):
    """get_pipeline_list_with_counts respects the limit parameter."""
    for i in range(5):
        await db.create_pipeline(
            id=f"pipe-lim-{i}",
            description=f"Pipeline {i}",
            project_dir="/tmp",
        )
    results = await db.get_pipeline_list_with_counts(limit=3)
    assert len(results) == 3


async def test_get_pipeline_list_with_counts_empty(db: Database):
    """get_pipeline_list_with_counts returns empty list when no pipelines."""
    results = await db.get_pipeline_list_with_counts()
    assert results == []
