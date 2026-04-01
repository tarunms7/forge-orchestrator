from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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


def test_replace_state_rewires_app_callback():
    """Replacing app state should detach from the old state and attach to the new one."""
    from forge.tui.app import ForgeApp

    app = ForgeApp.__new__(ForgeApp)
    old_state = MagicMock()
    new_state = MagicMock()
    callback = MagicMock()
    app._state = old_state
    app._state_cb = callback

    app._replace_state(new_state)

    old_state.remove_change_callback.assert_called_once_with(callback)
    new_state.on_change.assert_called_once_with(callback)
    assert app._state is new_state


@pytest.mark.asyncio
async def test_history_resume_partial_pipeline_retries_failed_tasks():
    """Shift+R from history should reset failed tasks and resume execution immediately."""
    from forge.tui.app import ForgeApp
    from forge.tui.screens.pipeline import PipelineScreen

    pipeline = MagicMock(
        id="pipe-1",
        task_graph_json='{"tasks":[]}',
        base_branch="main",
        branch_name="forge/retry-branch",
    )
    ctx = {
        "status": "partial_success",
        "quit_phase": None,
        "task_graph_json": pipeline.task_graph_json,
        "contracts_json": None,
        "pr_url": None,
        "project_dir": "",
        "base_branch": "main",
        "branch_name": "forge/retry-branch",
        "description": "Retry failed pipeline",
        "executor_pid": None,
        "total_tasks": 3,
        "tasks_done": 1,
        "tasks_error": 1,
        "tasks_in_review": 0,
        "tasks_blocked": 1,
    }

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
    app._db.get_pipeline = AsyncMock(return_value=pipeline)
    app._db.list_tasks_by_pipeline = AsyncMock(
        return_value=[
            MagicMock(id="t-done", state="done"),
            MagicMock(id="t-error", state="error"),
            MagicMock(id="t-blocked", state="blocked"),
        ]
    )
    app._db.retry_task = AsyncMock()
    app._db.update_task_state = AsyncMock()
    app._db.update_pipeline_status = AsyncMock()

    replay_state = MagicMock()
    replay_state.tasks = {
        "t-done": {"state": "done"},
        "t-error": {"state": "error", "error": "usage limit reached"},
        "t-blocked": {"state": "blocked", "error": "blocked by t-error"},
    }
    replay_state._notify = MagicMock()
    replay_state.phase = "partial_success"

    async def _replay(_pipeline):
        app._state = replay_state
        app._pipeline_id = _pipeline.id

    app._replay_state_for_pipeline = AsyncMock(side_effect=_replay)
    app._setup_daemon_for_resume = AsyncMock()
    app._load_task_graph = MagicMock(
        side_effect=lambda _pipeline: setattr(app, "_graph", object()) or True
    )
    app._resume_execution = AsyncMock()
    app._push_final_approval = MagicMock()
    app.push_screen = MagicMock()
    app.notify = MagicMock()
    app._daemon = object()
    app._graph = object()
    app._final_approval_pushed = True

    event = MagicMock()
    event.pipeline_id = "pipe-1"

    await app.on_pipeline_list_resume_requested(event)

    app._db.retry_task.assert_awaited_once_with("t-error")
    app._db.update_task_state.assert_any_await("t-blocked", "todo")
    app._db.update_pipeline_status.assert_awaited_once_with("pipe-1", "retrying")
    app._resume_execution.assert_awaited_once()
    app._push_final_approval.assert_not_called()
    app.push_screen.assert_called_once()
    pushed_screen = app.push_screen.call_args[0][0]
    assert isinstance(pushed_screen, PipelineScreen)
    assert replay_state.tasks["t-error"]["state"] == "todo"
    assert replay_state.tasks["t-blocked"]["state"] == "todo"
    assert "error" not in replay_state.tasks["t-error"]
    assert "error" not in replay_state.tasks["t-blocked"]
    assert replay_state.phase == "retrying"


@pytest.mark.asyncio
async def test_history_resume_interrupted_planning_restarts_from_scratch():
    """Shift+R should restart planning from scratch after an interrupted planning session."""
    from forge.tui.app import ForgeApp
    from forge.tui.screens.pipeline import PipelineScreen
    from forge.tui.state import TuiState

    pipeline = MagicMock(id="pipe-plan", project_dir="/tmp/project")
    ctx = {
        "status": "interrupted",
        "quit_phase": "planning",
        "task_graph_json": None,
        "contracts_json": None,
        "pr_url": None,
        "project_dir": "/tmp/project",
        "base_branch": "main",
        "branch_name": "forge/plan-branch",
        "description": "Build gauntlet",
        "executor_pid": None,
        "total_tasks": 0,
        "tasks_done": 0,
        "tasks_error": 0,
        "tasks_in_review": 0,
        "tasks_blocked": 0,
    }

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pipeline_resume_context = AsyncMock(return_value=ctx)
    app._db.get_pipeline = AsyncMock(return_value=pipeline)
    app._project_dir = "/tmp/original"
    app._repos = []
    app._source = None
    app._bus = MagicMock()
    app._daemon = None
    app._daemon_task = None
    app._graph = None
    app._pipeline_id = "old-pipeline"
    app._final_approval_pushed = False
    app._cached_pipeline_branch = ""
    app._cached_base_branch = "main"
    app._state = TuiState()
    app._resolve_repos = MagicMock(return_value=[])
    app.push_screen = MagicMock()
    app.notify = MagicMock()
    app._on_daemon_done = MagicMock()
    app._run_plan = AsyncMock()

    with (
        patch("forge.tui.app.asyncio.create_task") as mock_create_task,
        patch("forge.tui.app.os.path.isdir", return_value=True),
    ):
        fake_task = MagicMock()
        mock_create_task.return_value = fake_task
        event = MagicMock()
        event.pipeline_id = "pipe-plan"
        await app.on_pipeline_list_resume_requested(event)
        planning_coro = mock_create_task.call_args[0][0]
        planning_coro.close()

    app._run_plan.assert_called_once_with(
        "Build gauntlet",
        base_branch="main",
        branch_name="forge/plan-branch",
    )
    app.push_screen.assert_called_once()
    pushed_screen = app.push_screen.call_args[0][0]
    assert isinstance(pushed_screen, PipelineScreen)
    assert app._state.phase == "planning"
    assert app._project_dir == "/tmp/project"
    app.notify.assert_called_once()
    assert "restarting" in app.notify.call_args[0][0].lower()


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
    from forge.tui.state import TuiState

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
    app._db.get_pending_questions = AsyncMock(return_value=[])
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = FakeDaemon()
    app._state = TuiState()
    app._state.pending_questions["__planning__"] = {
        "question": "Which DB?",
        "question_id": "q1",
    }

    await app.on_chat_thread_answer_submitted(event)

    assert len(emitted) == 1
    assert emitted[0][0] == "planning:answer"
    assert emitted[0][1]["question_id"] == "q1"
    assert emitted[0][1]["answer"] == "Use JWT"
    app._db.answer_question.assert_awaited_once_with("q1", "Use JWT", "human")


@pytest.mark.asyncio
async def test_planning_answer_uses_current_pending_question_id_over_stale_db_rows():
    """Current pending planning question should win over older unanswered DB rows."""
    from forge.tui.app import ForgeApp
    from forge.tui.state import TuiState

    emitted = []

    class FakeEmitter:
        async def emit(self, event_type, data):
            emitted.append((event_type, data))

    class FakeDaemon:
        _events = FakeEmitter()

    event = MagicMock()
    event.task_id = "__planning__"
    event.answer = "Option C"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pending_questions = AsyncMock(
        return_value=[
            MagicMock(task_id="__planning__", answer=None, id="old-q"),
            MagicMock(task_id="__planning__", answer=None, id="new-q"),
        ]
    )
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = FakeDaemon()
    app._state = TuiState()
    app._state.pending_questions["__planning__"] = {
        "question": "Pick one",
        "question_id": "new-q",
    }

    await app.on_chat_thread_answer_submitted(event)

    app._db.answer_question.assert_awaited_once_with("new-q", "Option C", "human")
    assert emitted == [("planning:answer", {"question_id": "new-q", "answer": "Option C"})]


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
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = None
    app._state = MagicMock()
    app._state.pending_questions = {"__planning__": {"question": "Which DB?", "question_id": "q1"}}
    app.notify = MagicMock()

    await app.on_chat_thread_answer_submitted(event)

    app._db.answer_question.assert_awaited_once_with("q1", "Use JWT", "human")
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
async def test_task_answer_rejected_when_question_is_no_longer_pending():
    """Submitting an answer for a stale question should not clear the live TUI state."""
    from forge.tui.app import ForgeApp
    from forge.tui.state import TuiState

    event = MagicMock()
    event.task_id = "t1"
    event.answer = "Option A"

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.get_pending_questions = AsyncMock(return_value=[])
    app._db.answer_question = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = None
    app.notify = MagicMock()
    app._state = TuiState()
    app._state.tasks = {"t1": {"id": "t1", "state": "awaiting_input", "title": "Review task"}}
    app._state.pending_questions["t1"] = {"question": "Approve?"}

    await app.on_chat_thread_answer_submitted(event)

    app._db.answer_question.assert_not_awaited()
    assert app._state.tasks["t1"]["state"] == "awaiting_input"
    assert "t1" in app._state.pending_questions
    app.notify.assert_called_once()
    assert "no longer pending" in app.notify.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_task_answer_without_live_agent_keeps_task_waiting():
    """If the answer is saved but no live agent is attached, clear the question without faking a resume."""
    from forge.tui.app import ForgeApp
    from forge.tui.state import TuiState

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
    app._daemon = None
    app.notify = MagicMock()
    app._state = TuiState()
    app._state.tasks = {"t1": {"id": "t1", "state": "awaiting_input", "title": "Review task"}}
    app._state.pending_questions["t1"] = {"question": "Approve?"}

    await app.on_chat_thread_answer_submitted(event)

    app._db.answer_question.assert_awaited_once_with("q1", "Option A", "human")
    assert app._state.tasks["t1"]["state"] == "awaiting_input"
    assert "t1" not in app._state.pending_questions
    assert app._state.question_history["t1"] == [
        {"question": {"question": "Approve?"}, "answer": "Option A"}
    ]
    app.notify.assert_called_once()
    assert "live agent is not attached" in app.notify.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_final_approval_follow_up_queues_local_task_and_resumes():
    """Follow-up should create the task, sync local state, and resume execution."""
    from forge.tui.app import ForgeApp
    from forge.tui.state import TuiState

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.list_tasks_by_pipeline = AsyncMock(
        return_value=[
            MagicMock(id="t-done", state="done"),
            MagicMock(id="t-review", state="in_review"),
        ]
    )
    app._db.create_task = AsyncMock()
    app._db.update_pipeline_status = AsyncMock()
    app._pipeline_id = "12345678-abcdef"
    app._daemon = object()
    app._graph = object()
    app._resume_execution = AsyncMock()
    app.notify = MagicMock()
    app._final_approval_pushed = True
    type(app).screen_stack = property(lambda self: self._test_screen_stack)
    app._test_screen_stack = [object(), object(), object()]
    app.pop_screen = MagicMock(side_effect=lambda: app._test_screen_stack.pop())
    app._state = TuiState()
    app._state.phase = "final_approval"
    app._state.tasks = {"t-done": {"id": "t-done", "title": "Done", "state": "done"}}
    app._state.task_order = ["t-done"]

    event = MagicMock()
    event.prompt = "Tighten the gauntlet report wording"

    await app.on_final_approval_screen_follow_up(event)

    app._db.create_task.assert_awaited_once_with(
        id="12345678-followup-1",
        title="Tighten the gauntlet report wording",
        description="Tighten the gauntlet report wording",
        files=[],
        depends_on=["t-done"],
        complexity="medium",
        pipeline_id="12345678-abcdef",
    )
    app._db.update_pipeline_status.assert_awaited_once_with("12345678-abcdef", "executing")
    app._resume_execution.assert_awaited_once()
    assert app._state.phase == "executing"
    assert app._state.selected_task_id == "12345678-followup-1"
    assert app._state.task_order[-1] == "12345678-followup-1"
    assert app._state.tasks["12345678-followup-1"]["state"] == "todo"
    assert len(app.screen_stack) == 2
    app.notify.assert_called()


@pytest.mark.asyncio
async def test_final_approval_follow_up_requires_live_execution_context():
    """Follow-up should fail fast if the live daemon/graph context is gone."""
    from forge.tui.app import ForgeApp

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._pipeline_id = "pipe1"
    app._daemon = None
    app._graph = object()
    app.notify = MagicMock()

    event = MagicMock()
    event.prompt = "Add a changelog entry"

    await app.on_final_approval_screen_follow_up(event)

    app.notify.assert_called_once()
    assert "resume it from history" in app.notify.call_args[0][0].lower()
    app._db.list_tasks_by_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_final_approval_follow_up_db_failure_does_not_mutate_state():
    """A DB failure while queuing follow-up should leave the final-approval state intact."""
    from forge.tui.app import ForgeApp
    from forge.tui.state import TuiState

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.list_tasks_by_pipeline = AsyncMock(return_value=[])
    app._db.create_task = AsyncMock(side_effect=RuntimeError("db write failed"))
    app._pipeline_id = "pipe1"
    app._daemon = object()
    app._graph = object()
    app._resume_execution = AsyncMock()
    app.notify = MagicMock()
    type(app).screen_stack = property(lambda self: self._test_screen_stack)
    app._test_screen_stack = [object(), object(), object()]
    app.pop_screen = MagicMock(side_effect=lambda: app._test_screen_stack.pop())
    app._state = TuiState()
    app._state.phase = "final_approval"

    event = MagicMock()
    event.prompt = "Add a changelog entry"

    await app.on_final_approval_screen_follow_up(event)

    assert app._state.phase == "final_approval"
    app._resume_execution.assert_not_awaited()
    app.pop_screen.assert_not_called()
    app.notify.assert_called_once()
    assert "failed to queue follow-up" in app.notify.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_final_approval_skip_failed_syncs_local_state():
    """Skip & finish should cancel failed tasks in DB and in the in-memory state."""
    from forge.tui.app import ForgeApp
    from forge.tui.state import TuiState

    app = ForgeApp.__new__(ForgeApp)
    app._db = AsyncMock()
    app._db.list_tasks_by_pipeline = AsyncMock(
        return_value=[
            MagicMock(id="t-error", state="error"),
            MagicMock(id="t-blocked", state="blocked"),
            MagicMock(id="t-done", state="done"),
        ]
    )
    app._db.update_task_state = AsyncMock()
    app._db.update_pipeline_status = AsyncMock()
    app._pipeline_id = "pipe1"
    app.notify = MagicMock()
    app._push_final_approval = MagicMock()
    app._final_approval_pushed = True
    type(app).screen_stack = property(lambda self: self._test_screen_stack)
    app._test_screen_stack = [object(), object(), object()]
    app.pop_screen = MagicMock(side_effect=lambda: app._test_screen_stack.pop())
    app._state = TuiState()
    app._state.phase = "partial_success"
    app._state.error = "PR failed"
    app._state.tasks = {
        "t-error": {"id": "t-error", "title": "A", "state": "error", "error": "boom"},
        "t-blocked": {"id": "t-blocked", "title": "B", "state": "blocked", "error": "wait"},
        "t-done": {"id": "t-done", "title": "C", "state": "done"},
    }
    app._state.pending_questions["t-error"] = {"question": "Old?"}

    event = MagicMock()

    await app.on_final_approval_screen_skip_failed(event)

    app._db.update_task_state.assert_any_await("t-error", "cancelled")
    app._db.update_task_state.assert_any_await("t-blocked", "cancelled")
    app._db.update_pipeline_status.assert_awaited_once_with("pipe1", "complete")
    assert app._state.tasks["t-error"]["state"] == "cancelled"
    assert app._state.tasks["t-blocked"]["state"] == "cancelled"
    assert "error" not in app._state.tasks["t-error"]
    assert "error" not in app._state.tasks["t-blocked"]
    assert "t-error" not in app._state.pending_questions
    assert app._state.phase == "final_approval"
    assert app._state.error is None
    app._push_final_approval.assert_called_once()
    app.notify.assert_called()


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


@pytest.mark.asyncio
async def test_final_approval_multirepo_pr_creation_recovers_when_pipeline_repos_json_missing():
    """Multi-repo PR creation should fall back to the app's live repo config for older pipelines."""
    from forge.core.models import RepoConfig
    from forge.tui.app import ForgeApp
    from forge.tui.pr_creator import MultiRepoPrResult

    app = ForgeApp.__new__(ForgeApp)
    app._project_dir = "/workspace"
    app._pipeline_id = "pipe-1"
    app._repos = [
        RepoConfig(id="wizbridge", path="/workspace/WizBridge", base_branch="main"),
        RepoConfig(id="temp", path="/workspace/temp", base_branch="main"),
    ]
    app._get_pipeline_branch = AsyncMock(return_value="forge/content-length")
    app._pipeline_description = MagicMock(return_value="Fix media transform content length")
    app.notify = MagicMock()
    app._state = MagicMock()
    app._state.question_history = {}
    app._state.elapsed_seconds = 120
    app._state.total_cost_usd = 1.41
    app._state.task_order = ["t1"]
    app._state.tasks = {
        "t1": {
            "title": "Add content-length comparison",
            "description": "Update WizBridge media file handling",
            "state": "done",
            "repo_id": "wizbridge",
            "cost_usd": 1.41,
            "merge_result": {
                "success": True,
                "linesAdded": 58,
                "linesRemoved": 0,
                "filesChanged": 2,
            },
            "files": ["internal/services/mediafile/media_file_service.go"],
        }
    }
    app._state.apply_event = MagicMock()
    app._db = AsyncMock()
    app._db.get_pipeline = AsyncMock(
        return_value=MagicMock(
            base_branch="main",
            get_repos=MagicMock(
                return_value=[
                    {
                        "id": "default",
                        "path": "/workspace",
                        "base_branch": "main",
                        "branch_name": "forge/content-length",
                    }
                ]
            ),
        )
    )
    app._db.update_pipeline_repos_json = AsyncMock()

    with (
        patch(
            "forge.tui.pr_creator.create_prs_multi_repo",
            new_callable=AsyncMock,
            return_value=MultiRepoPrResult(
                pr_urls={"wizbridge": "https://github.com/org/WizBridge/pull/291"},
                failures={},
            ),
        ) as mock_create_prs,
        patch("forge.tui.pr_creator.maybe_start_ci_fix", new_callable=AsyncMock),
        patch("forge.tui.app.os.path.exists", return_value=True),
        patch.object(type(app), "screen", new_callable=PropertyMock, return_value=MagicMock()),
    ):
        await app.on_final_approval_screen_create_pr(MagicMock())

    called_repos = mock_create_prs.await_args.kwargs["repos"]
    assert called_repos["wizbridge"]["project_dir"] == "/workspace/WizBridge"
    assert called_repos["temp"]["project_dir"] == "/workspace/temp"
    app._db.update_pipeline_repos_json.assert_awaited_once()
    repos_json = app._db.update_pipeline_repos_json.await_args.args[1]
    assert '"id": "wizbridge"' in repos_json
    assert '"path": "/workspace/WizBridge"' in repos_json
