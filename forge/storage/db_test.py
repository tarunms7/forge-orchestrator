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


async def test_force_release_agent(db: Database):
    """force_release_agent resets agent to idle via raw SQL."""
    await db.create_task(
        id="task-1", title="T", description="D", files=["a.py"],
        depends_on=[], complexity="low",
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


async def test_migrate_adds_missing_columns():
    """Verify initialize() adds columns missing from a stale DB schema."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Step 1: create a DB with old schema (no pipeline_id on tasks, no pr_url on pipelines)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE tasks ("
            "  id VARCHAR PRIMARY KEY, title VARCHAR NOT NULL, description VARCHAR NOT NULL,"
            "  files JSON NOT NULL, depends_on JSON NOT NULL, complexity VARCHAR NOT NULL,"
            "  state VARCHAR NOT NULL DEFAULT 'todo', assigned_agent VARCHAR,"
            "  retry_count INTEGER NOT NULL DEFAULT 0, branch_name VARCHAR, worktree_path VARCHAR"
            ")"
        ))
        await conn.execute(text(
            "CREATE TABLE pipelines ("
            "  id VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, project_dir VARCHAR NOT NULL,"
            "  status VARCHAR NOT NULL DEFAULT 'planning', model_strategy VARCHAR NOT NULL DEFAULT 'auto',"
            "  task_graph_json VARCHAR, user_id VARCHAR,"
            "  created_at VARCHAR, completed_at VARCHAR"
            ")"
        ))
        await conn.execute(text(
            "CREATE TABLE agents ("
            "  id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL DEFAULT 'idle', current_task VARCHAR"
            ")"
        ))

    # Step 2: wrap in Database and call initialize() — should add missing columns
    from forge.storage.db import Database as DB
    db = DB.__new__(DB)
    db._engine = engine
    from sqlalchemy.ext.asyncio import async_sessionmaker
    db._session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await db.initialize()

    # Step 3: operations that touch new columns should work
    await db.create_task(
        id="t1", title="T", description="D", files=[], depends_on=[],
        complexity="low", pipeline_id="pipe-1",
    )
    task = await db.get_task("t1")
    assert task.pipeline_id == "pipe-1"

    await db.create_pipeline(
        id="pipe-1", description="P", project_dir="/tmp", model_strategy="auto",
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
        await conn.execute(text(
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
        ))
        # Insert a row with old schema (no project columns)
        await conn.execute(text(
            "INSERT INTO pipelines (id, description, project_dir, created_at) "
            "VALUES ('old-pipe', 'Old pipeline', '/tmp/old', '2025-01-01T00:00:00+00:00')"
        ))
        # Also create minimal tasks/agents tables to avoid errors
        await conn.execute(text(
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
        ))
        await conn.execute(text(
            "CREATE TABLE agents ("
            "  id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL DEFAULT 'idle', current_task VARCHAR"
            ")"
        ))

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
        id="new-pipe", description="New pipeline", project_dir="/tmp/new",
        project_path="/Users/tarun/my-project", project_name="my-project",
    )
    new = await db.get_pipeline("new-pipe")
    assert new.project_path == "/Users/tarun/my-project"
    assert new.project_name == "my-project"

    await engine.dispose()


async def test_set_pipeline_pr_url(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.set_pipeline_pr_url("pipe-1", "https://github.com/user/repo/pull/42")
    pipeline = await db.get_pipeline("pipe-1")
    assert pipeline.pr_url == "https://github.com/user/repo/pull/42"


async def test_log_event(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
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
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
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
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.log_event(pipeline_id="pipe-1", task_id="t1", event_type="agent_output", payload={"line": "a"})
    await db.log_event(pipeline_id="pipe-1", task_id="t2", event_type="agent_output", payload={"line": "b"})
    events = await db.list_events("pipe-1", task_id="t1")
    assert len(events) == 1
    assert events[0].payload["line"] == "a"


async def test_add_task_cost(db: Database):
    await db.create_task(
        id="t1", title="T", description="D", files=[], depends_on=[], complexity="low",
    )
    await db.add_task_cost("t1", 0.05)
    await db.add_task_cost("t1", 0.03)
    task = await db.get_task("t1")
    assert abs(task.cost_usd - 0.08) < 0.001


async def test_list_events_by_type(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.log_event(pipeline_id="pipe-1", task_id=None, event_type="phase_change", payload={})
    await db.log_event(pipeline_id="pipe-1", task_id="t1", event_type="review_update", payload={})
    events = await db.list_events("pipe-1", event_type="review_update")
    assert len(events) == 1


# ── branch_name tests ──────────────────────────────────────────────


async def test_create_pipeline_with_branch_name(db: Database):
    """create_pipeline should store branch_name."""
    await db.create_pipeline(
        id="pipe-bn", description="Branch test", project_dir="/tmp",
        model_strategy="auto", branch_name="feat/my-feature",
    )
    pipeline = await db.get_pipeline("pipe-bn")
    assert pipeline is not None
    assert pipeline.branch_name == "feat/my-feature"


async def test_create_pipeline_branch_name_defaults_to_none(db: Database):
    """create_pipeline without branch_name should default to None."""
    await db.create_pipeline(
        id="pipe-no-bn", description="No branch", project_dir="/tmp",
        model_strategy="auto",
    )
    pipeline = await db.get_pipeline("pipe-no-bn")
    assert pipeline.branch_name is None


# ── restart_pipeline tests ──────────────────────────────────────────


async def test_restart_pipeline_resets_state(db: Database):
    """restart_pipeline should reset pipeline and tasks, delete events."""
    # Setup: create pipeline with tasks and events
    await db.create_pipeline(
        id="pipe-r1", description="Restart test", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.set_pipeline_plan("pipe-r1", '{"tasks": []}')
    await db.update_pipeline_status("pipe-r1", "executing")

    await db.create_task(
        id="t1", title="T1", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-r1",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=["b.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-r1",
    )
    await db.update_task_state("t1", "done")
    await db.update_task_state("t2", "in_progress")

    # Add some events
    await db.log_event(pipeline_id="pipe-r1", task_id="t1", event_type="agent_output", payload={"line": "hi"})
    await db.log_event(pipeline_id="pipe-r1", task_id="t2", event_type="agent_output", payload={"line": "bye"})

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
        id="pipe-c1", description="Cancel test", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t1", title="T1", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-c1",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=["b.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-c1",
    )
    await db.create_task(
        id="t3", title="T3", description="D", files=["c.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-c1",
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
        id="pipe-c2", description="Cancel skip error", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="te1", title="T1", description="D", files=[],
        depends_on=[], complexity="low", pipeline_id="pipe-c2",
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
        id="pipe-ap", description="Approve test", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t-ap", title="T", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-ap",
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
        id="pipe-ap2", description="Approve test", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t-ap2", title="T", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-ap2",
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
        id="pipe-ap3", description="Test", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.create_task(
        id="t-ap3", title="T", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-ap3",
    )
    await db.update_task_state("t-ap3", "awaiting_approval")

    result = await db.approve_task_atomically("t-ap3", "wrong-pipeline")
    assert result is None


# ── atomic cost accumulation tests ───────────────────────────────────


async def test_add_task_agent_cost_accumulates(db: Database):
    """add_task_agent_cost should atomically accumulate cost and tokens."""
    await db.create_task(
        id="t-ac", title="T", description="D", files=[], depends_on=[], complexity="low",
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
        id="t-rc", title="T", description="D", files=[], depends_on=[], complexity="low",
    )
    await db.add_task_review_cost("t-rc", 0.02)
    await db.add_task_review_cost("t-rc", 0.03)

    task = await db.get_task("t-rc")
    assert abs(task.review_cost_usd - 0.05) < 0.001
    assert abs(task.cost_usd - 0.05) < 0.001


async def test_add_pipeline_cost_accumulates(db: Database):
    """add_pipeline_cost should atomically accumulate pipeline total cost."""
    await db.create_pipeline(
        id="pipe-cost", description="Cost test", project_dir="/tmp",
        model_strategy="auto",
    )
    await db.add_pipeline_cost("pipe-cost", 0.10)
    await db.add_pipeline_cost("pipe-cost", 0.25)

    cost = await db.get_pipeline_cost("pipe-cost")
    assert abs(cost - 0.35) < 0.001


async def test_executor_tracking_columns(db: Database):
    """executor_pid and executor_token can be set and cleared on a pipeline."""
    await db.create_pipeline(
        id="pipe-exec", description="test", project_dir="/tmp",
        model_strategy="balanced", budget_limit_usd=10,
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
