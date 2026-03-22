from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_graceful_quit_resets_stuck_tasks():
    """Graceful quit should reset non-terminal tasks to TODO and mark pipeline interrupted."""
    from forge.storage.db import Database

    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    await db.create_pipeline(
        id="test-pipe-001",
        description="test",
        project_dir="/tmp",
        model_strategy="balanced",
        budget_limit_usd=10,
    )
    await db.update_pipeline_status("test-pipe-001", "executing")
    await db.create_task(
        id="t1",
        title="A",
        description="A",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-001",
    )
    await db.create_task(
        id="t2",
        title="B",
        description="B",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-001",
    )
    await db.update_task_state("t1", "in_progress")
    await db.update_task_state("t2", "done")

    tasks = await db.list_tasks_by_pipeline("test-pipe-001")
    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
    for t in tasks:
        if t.state in non_terminal:
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status("test-pipe-001", "interrupted")

    p = await db.get_pipeline("test-pipe-001")
    assert p.status == "interrupted"
    t1_row = await db.get_task("t1")
    assert t1_row.state == "todo"
    t2_row = await db.get_task("t2")
    assert t2_row.state == "done"


@pytest.mark.asyncio
async def test_rerun_resets_error_and_blocked_to_todo():
    """Re-run handler should reset error and blocked tasks to TODO."""
    from forge.storage.db import Database

    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    await db.create_pipeline(
        id="test-pipe-002",
        description="test",
        project_dir="/tmp",
        model_strategy="balanced",
        budget_limit_usd=10,
    )
    await db.update_pipeline_status("test-pipe-002", "partial_success")
    await db.create_task(
        id="t1",
        title="A",
        description="A",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-002",
    )
    await db.create_task(
        id="t2",
        title="B",
        description="B",
        files=[],
        depends_on=["t1"],
        complexity="low",
        pipeline_id="test-pipe-002",
    )
    await db.update_task_state("t1", "error")
    await db.update_task_state("t2", "blocked")

    tasks = await db.list_tasks_by_pipeline("test-pipe-002")
    for t in tasks:
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status("test-pipe-002", "retrying")

    t1_row = await db.get_task("t1")
    t2_row = await db.get_task("t2")
    assert t1_row.state == "todo"
    assert t2_row.state == "todo"
    p = await db.get_pipeline("test-pipe-002")
    assert p.status == "retrying"


@pytest.mark.asyncio
async def test_skip_failed_cancels_error_blocked_tasks():
    """Skip handler should cancel error/blocked tasks and mark pipeline complete."""
    from forge.storage.db import Database

    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    await db.create_pipeline(
        id="test-pipe-003",
        description="test",
        project_dir="/tmp",
        model_strategy="balanced",
        budget_limit_usd=10,
    )
    await db.update_pipeline_status("test-pipe-003", "partial_success")
    await db.create_task(
        id="t1",
        title="A",
        description="A",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-003",
    )
    await db.create_task(
        id="t2",
        title="B",
        description="B",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-003",
    )
    await db.update_task_state("t1", "error")
    await db.update_task_state("t2", "blocked")

    tasks = await db.list_tasks_by_pipeline("test-pipe-003")
    for t in tasks:
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "cancelled")
    await db.update_pipeline_status("test-pipe-003", "complete")

    t1_row = await db.get_task("t1")
    t2_row = await db.get_task("t2")
    assert t1_row.state == "cancelled"
    assert t2_row.state == "cancelled"
    p = await db.get_pipeline("test-pipe-003")
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_resume_refetches_tasks_after_reset():
    """Resume should re-fetch tasks after resetting stuck ones so counts are accurate."""
    from forge.storage.db import Database

    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    await db.create_pipeline(
        id="test-pipe-004",
        description="test",
        project_dir="/tmp",
        model_strategy="balanced",
        budget_limit_usd=10,
    )
    await db.update_pipeline_status("test-pipe-004", "interrupted")
    await db.create_task(
        id="t1",
        title="A",
        description="A",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-004",
    )
    await db.create_task(
        id="t2",
        title="B",
        description="B",
        files=[],
        depends_on=[],
        complexity="low",
        pipeline_id="test-pipe-004",
    )
    await db.update_task_state("t1", "done")
    await db.update_task_state("t2", "in_progress")

    tasks = await db.list_tasks_by_pipeline("test-pipe-004")
    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
    for t in tasks:
        if t.state in non_terminal:
            await db.update_task_state(t.id, "todo")

    tasks = await db.list_tasks_by_pipeline("test-pipe-004")
    done_count = sum(1 for t in tasks if t.state == "done")
    assert done_count == 1
    todo_count = sum(1 for t in tasks if t.state == "todo")
    assert todo_count == 1


@pytest.mark.asyncio
async def test_answer_submission_emits_to_daemon_events():
    """Answering a question should emit task:answer on the daemon's EventEmitter."""
    from forge.tui.app import ForgeApp

    emitted = []

    class FakeEmitter:
        async def emit(self, event_type, data):
            emitted.append((event_type, data))

    class FakeDaemon:
        _events = FakeEmitter()

    event = MagicMock()
    event.task_id = "t1"
    event.answer = "Use JWT"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pending_questions = AsyncMock(
        return_value=[MagicMock(task_id="t1", answer=None, id="q1")]
    )
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = FakeDaemon()
    app._state = MagicMock()

    await app.on_chat_thread_answer_submitted(event)

    assert len(emitted) == 1
    assert emitted[0][0] == "task:answer"
    assert emitted[0][1]["task_id"] == "t1"
    assert emitted[0][1]["answer"] == "Use JWT"
    assert emitted[0][1]["pipeline_id"] == "pipe1"


@pytest.mark.asyncio
async def test_planning_answer_emits_planning_answer_event():
    """Answering a planning question should emit planning:answer to daemon."""
    from forge.tui.app import ForgeApp

    emitted = []

    class FakeEmitter:
        async def emit(self, event_type, data):
            emitted.append((event_type, data))

    class FakeDaemon:
        _events = FakeEmitter()

    event = MagicMock()
    event.task_id = "__planning__"
    event.answer = "Use JWT"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pending_questions = AsyncMock(
        return_value=[MagicMock(task_id="__planning__", answer=None, id="q1")]
    )
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = FakeDaemon()
    app._state = MagicMock()

    await app.on_chat_thread_answer_submitted(event)

    assert len(emitted) == 1
    assert emitted[0][0] == "planning:answer"
    assert emitted[0][1]["question_id"] == "q1"
    assert emitted[0][1]["answer"] == "Use JWT"


@pytest.mark.asyncio
async def test_planning_answer_applies_planning_answer_event():
    """Planning answer should apply planning:answer (not task:answer) to state."""
    from forge.tui.app import ForgeApp

    event = MagicMock()
    event.task_id = "__planning__"
    event.answer = "Use JWT"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pending_questions = AsyncMock(return_value=[])
    app._pipeline_id = "pipe1"
    app._daemon = None
    app._state = MagicMock()

    await app.on_chat_thread_answer_submitted(event)

    app._state.apply_event.assert_called_once_with("planning:answer", {"answer": "Use JWT"})


@pytest.mark.asyncio
async def test_task_answer_does_not_emit_planning_event():
    """Regular task answers should NOT emit planning:answer."""
    from forge.tui.app import ForgeApp

    emitted = []

    class FakeEmitter:
        async def emit(self, event_type, data):
            emitted.append((event_type, data))

    class FakeDaemon:
        _events = FakeEmitter()

    event = MagicMock()
    event.task_id = "t1"
    event.answer = "Option A"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pending_questions = AsyncMock(
        return_value=[MagicMock(task_id="t1", answer=None, id="q1")]
    )
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = FakeDaemon()
    app._state = MagicMock()

    await app.on_chat_thread_answer_submitted(event)

    assert len(emitted) == 1
    assert emitted[0][0] == "task:answer"
    assert emitted[0][1]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_interjection_creates_db_record():
    """InterjectionSubmitted should create a DB interjection record."""
    from forge.tui.app import ForgeApp

    event = MagicMock()
    event.task_id = "t1"
    event.message = "Use factory pattern"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.create_interjection = AsyncMock()
    app._pipeline_id = "pipe1"
    app._state = MagicMock()

    await app.on_chat_thread_interjection_submitted(event)

    app._db.create_interjection.assert_called_once_with(
        task_id="t1",
        pipeline_id="pipe1",
        message="Use factory pattern",
    )
    app._state.apply_event.assert_called_once_with(
        "task:interjection",
        {
            "task_id": "t1",
            "message": "Use factory pattern",
        },
    )


@pytest.mark.asyncio
async def test_interjection_skipped_without_db():
    """InterjectionSubmitted should do nothing if DB or pipeline_id is missing."""
    from forge.tui.app import ForgeApp

    event = MagicMock()
    event.task_id = "t1"
    event.message = "Use factory pattern"

    app = ForgeApp.__new__(ForgeApp)
    app._db = None
    app._pipeline_id = None
    app._state = MagicMock()

    await app.on_chat_thread_interjection_submitted(event)

    app._state.apply_event.assert_not_called()


def test_chat_thread_interjection_mode():
    """ChatThread in interjection mode should have correct mode."""
    from forge.tui.widgets.chat_thread import ChatThread

    chat = ChatThread(task_id="t1", mode="interjection")
    assert chat._mode == "interjection"
    assert chat.task_id == "t1"


def test_chat_thread_default_mode():
    """ChatThread should default to answer mode."""
    from forge.tui.widgets.chat_thread import ChatThread

    chat = ChatThread(task_id="t2")
    assert chat._mode == "answer"
    assert chat.task_id == "t2"


def test_chat_thread_interjection_event_type():
    """task:interjection should be in TUI_EVENT_TYPES."""
    from forge.tui.bus import TUI_EVENT_TYPES

    assert "task:interjection" in TUI_EVENT_TYPES
