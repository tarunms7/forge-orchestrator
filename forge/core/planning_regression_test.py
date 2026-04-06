"""Regression tests for planning phase event emission and retry logic.

Verifies that:
- daemon.plan() emits events in the correct order
- emit_plan_ready=False skips plan_ready but still emits phase_changed:planned
- ForgeSettings.max_retries defaults to 5
- _handle_retry() allows exactly max_retries retries before error
- plan_ready event data includes tasks but NOT a phase field
"""

from unittest.mock import AsyncMock, MagicMock, patch

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon
from forge.core.events import EventEmitter
from forge.core.models import TaskGraph
from forge.merge.worktree import WorktreeManager
from forge.providers.base import EventKind, ProviderEvent, ProviderResult
from forge.storage.db import Database

# -- Shared test data -------------------------------------------------------

VALID_GRAPH = TaskGraph.model_validate(
    {
        "tasks": [
            {
                "id": "task-1",
                "title": "Create model",
                "description": "Build user model",
                "files": ["src/models/user.py"],
                "depends_on": [],
                "complexity": "low",
            },
            {
                "id": "task-2",
                "title": "Build API",
                "description": "Build auth endpoints",
                "files": ["src/api/auth.py"],
                "depends_on": ["task-1"],
                "complexity": "medium",
            },
        ]
    }
)


def _make_daemon(event_emitter: EventEmitter | None = None) -> ForgeDaemon:
    """Create a ForgeDaemon with default settings for testing."""
    settings = ForgeSettings()
    emitter = event_emitter or EventEmitter()
    return ForgeDaemon(
        project_dir="/tmp/test-project",
        settings=settings,
        event_emitter=emitter,
    )


def _make_mock_db() -> AsyncMock:
    """Create a mock Database with all required async methods."""
    db = AsyncMock(spec=Database)
    db.log_event = AsyncMock()
    db.get_pipeline = AsyncMock(return_value=None)
    db.update_pipeline_conventions = AsyncMock()
    db.add_pipeline_cost = AsyncMock()
    db.set_pipeline_planner_cost = AsyncMock()
    db.get_pipeline_cost = AsyncMock(return_value=0.0)
    db.update_task_state = AsyncMock()
    db.retry_task = AsyncMock()
    db.get_task = AsyncMock()
    return db


# -- Test: event emission order with pipeline_id ----------------------------


async def test_plan_emits_events_in_correct_order():
    """plan() emits: phase_changed:planning → planner:output → plan_ready → phase_changed:planned."""
    emitted_events: list[tuple[str, dict]] = []

    emitter = EventEmitter()

    # Register handlers for all relevant events
    for evt in (
        "pipeline:phase_changed",
        "planner:output",
        "pipeline:plan_ready",
        "pipeline:cost_update",
        "pipeline:cost_estimate",
    ):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitted_events.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    # Mock the Planner so we don't call real LLM
    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(return_value=VALID_GRAPH)

    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        graph = await daemon.plan(
            "Build a REST API",
            db,
            emit_plan_ready=True,
            pipeline_id="pipe-123",
        )

    assert isinstance(graph, TaskGraph)
    assert len(graph.tasks) == 2

    # Extract event types in order
    event_types = [evt for evt, _ in emitted_events]

    # phase_changed:planning must come first
    assert event_types[0] == "pipeline:phase_changed"
    assert emitted_events[0][1] == {"phase": "planning"}

    planner_lines = [data["line"] for evt, data in emitted_events if evt == "planner:output"]
    assert any(line.startswith("Starting planner (") for line in planner_lines)
    assert any(line.startswith("Routing: ") for line in planner_lines)
    assert any("Planner Claude" in line for line in planner_lines if line.startswith("Routing: "))

    # Verify the routing line contains 'Agent (L/M/H)' and 'Review' segments for improved format verification
    routing_lines = [line for line in planner_lines if line.startswith("Routing: ")]
    assert len(routing_lines) > 0, "Expected at least one routing line"
    routing_line = routing_lines[0]
    assert "Agent (L/M/H)" in routing_line, (
        f"Missing 'Agent (L/M/H)' segment in routing line: {routing_line}"
    )
    assert "Review" in routing_line, f"Missing 'Review' segment in routing line: {routing_line}"

    # plan_ready must appear before phase_changed:planned
    plan_ready_idx = next(
        i for i, (evt, _) in enumerate(emitted_events) if evt == "pipeline:plan_ready"
    )
    planned_idx = next(
        i
        for i, (evt, data) in enumerate(emitted_events)
        if evt == "pipeline:phase_changed" and data.get("phase") == "planned"
    )
    assert plan_ready_idx < planned_idx, (
        f"plan_ready (idx={plan_ready_idx}) must come before phase_changed:planned (idx={planned_idx})"
    )

    # phase_changed:planned must be the last phase_changed event
    phase_events = [
        (i, data) for i, (evt, data) in enumerate(emitted_events) if evt == "pipeline:phase_changed"
    ]
    assert phase_events[-1][1] == {"phase": "planned"}


async def test_plan_tracks_provider_reported_cost_for_simple_planner():
    emitter = EventEmitter()
    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(return_value=VALID_GRAPH)

    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = ProviderResult(
        text="",
        is_error=False,
        input_tokens=100,
        output_tokens=50,
        resume_state=None,
        duration_ms=250,
        provider_reported_cost_usd=1.25,
        model_canonical_id="claude-opus",
    )

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        await daemon.plan(
            "Build a REST API",
            db,
            emit_plan_ready=True,
            pipeline_id="pipe-123",
        )

    db.add_pipeline_cost.assert_any_call("pipe-123", 1.25)
    db.set_pipeline_planner_cost.assert_any_call("pipe-123", 1.25)


# -- Test: emit_plan_ready=False skips plan_ready but emits planned ----------


async def test_emit_plan_ready_false_skips_plan_ready_but_emits_planned():
    """When emit_plan_ready=False, plan_ready is NOT emitted but phase_changed:planned still IS."""
    emitted_events: list[tuple[str, dict]] = []

    emitter = EventEmitter()

    for evt in (
        "pipeline:phase_changed",
        "planner:output",
        "pipeline:plan_ready",
        "pipeline:cost_update",
        "pipeline:cost_estimate",
    ):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitted_events.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(return_value=VALID_GRAPH)
    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        graph = await daemon.plan(
            "Build a REST API",
            db,
            emit_plan_ready=False,
            pipeline_id="pipe-456",
        )

    assert isinstance(graph, TaskGraph)

    event_types = [evt for evt, _ in emitted_events]

    # plan_ready should NOT appear
    assert "pipeline:plan_ready" not in event_types, (
        "plan_ready should not be emitted when emit_plan_ready=False"
    )

    # phase_changed:planning should still be emitted
    assert emitted_events[0] == ("pipeline:phase_changed", {"phase": "planning"})

    # phase_changed:planned should STILL be emitted even when emit_plan_ready=False
    planned_events = [
        data
        for evt, data in emitted_events
        if evt == "pipeline:phase_changed" and data.get("phase") == "planned"
    ]
    assert len(planned_events) == 1, (
        "phase_changed:planned should STILL be emitted when emit_plan_ready=False — "
        "only plan_ready is guarded by the emit_plan_ready flag"
    )


# -- Test: max_retries default is 5 -----------------------------------------


def test_default_max_retries_is_5():
    """ForgeSettings.max_retries defaults to 5."""
    settings = ForgeSettings()
    assert settings.max_retries == 5


# -- Test: _handle_retry allows exactly max_retries retries ------------------


@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_handle_retry_allows_max_retries_then_errors(mock_sleep):
    """_handle_retry() retries for retry_count 0..4, then marks error at retry_count=5."""
    settings = ForgeSettings()
    assert settings.max_retries == 5

    emitter = EventEmitter()
    emitter_calls: list[tuple[str, dict]] = []

    for evt in ("task:state_changed",):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitter_calls.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()
    worktree_mgr = MagicMock(spec=WorktreeManager)

    # Test retries 0 through 4 (should retry, not error)
    for retry_count in range(5):
        emitter_calls.clear()

        task_record = MagicMock()
        task_record.retry_count = retry_count
        db.get_task.return_value = task_record

        await daemon._handle_retry(
            db=db,
            task_id="task-1",
            worktree_mgr=worktree_mgr,
            review_feedback="fix this",
            pipeline_id="pipe-789",
        )

        # Should have called retry_task, not update_task_state to error
        db.retry_task.assert_called_with("task-1", review_feedback="fix this")
        assert any(
            evt == "task:state_changed" and data.get("state") == "retrying"
            for evt, data in emitter_calls
        ), f"retry_count={retry_count} should emit retrying state"

    # Test retry_count = 5 (should mark as error)
    emitter_calls.clear()
    db.retry_task.reset_mock()

    task_record = MagicMock()
    task_record.retry_count = 5  # equals max_retries → error
    db.get_task.return_value = task_record

    await daemon._handle_retry(
        db=db,
        task_id="task-1",
        worktree_mgr=worktree_mgr,
        review_feedback="fix this",
        pipeline_id="pipe-789",
    )

    # Should have called update_task_state to ERROR, not retry_task
    db.update_task_state.assert_called_with("task-1", "error")
    db.retry_task.assert_not_called()
    assert any(
        evt == "task:state_changed" and data.get("state") == "error" for evt, data in emitter_calls
    ), "retry_count=5 should emit error state"


# -- Test: plan_ready data includes tasks but no phase field -----------------


async def test_plan_ready_data_has_tasks_but_no_phase():
    """plan_ready event data includes tasks list but does NOT include a phase field."""
    emitted_events: list[tuple[str, dict]] = []

    emitter = EventEmitter()

    for evt in (
        "pipeline:phase_changed",
        "planner:output",
        "pipeline:plan_ready",
        "pipeline:cost_update",
        "pipeline:cost_estimate",
    ):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitted_events.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(return_value=VALID_GRAPH)
    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        await daemon.plan(
            "Build something",
            db,
            emit_plan_ready=True,
            pipeline_id="pipe-abc",
        )

    # Find the plan_ready event
    plan_ready_events = [
        (evt, data) for evt, data in emitted_events if evt == "pipeline:plan_ready"
    ]
    assert len(plan_ready_events) == 1, "Exactly one plan_ready event expected"

    plan_data = plan_ready_events[0][1]

    # Must include tasks
    assert "tasks" in plan_data, "plan_ready data must include tasks"
    assert len(plan_data["tasks"]) == 2, "plan_ready should include all tasks from graph"

    # Must NOT include phase
    assert "phase" not in plan_data, (
        "plan_ready data must NOT include a phase field — "
        "phase is communicated via separate phase_changed events"
    )

    # Verify task structure
    task_ids = {t["id"] for t in plan_data["tasks"]}
    assert task_ids == {"task-1", "task-2"}
    for task in plan_data["tasks"]:
        assert "title" in task
        assert "description" in task
        assert "files" in task
        assert "depends_on" in task
        assert "complexity" in task


async def test_plan_cost_estimate_event_uses_serializable_total_cost():
    """plan() should emit a plain numeric cost estimate, not a settings object or dataclass."""
    emitted_events: list[tuple[str, dict]] = []

    emitter = EventEmitter()

    for evt in (
        "pipeline:phase_changed",
        "planner:output",
        "pipeline:plan_ready",
        "pipeline:cost_update",
        "pipeline:cost_estimate",
    ):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitted_events.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(return_value=VALID_GRAPH)
    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        await daemon.plan(
            "Build something",
            db,
            emit_plan_ready=True,
            pipeline_id="pipe-cost",
        )

    cost_events = [(evt, data) for evt, data in emitted_events if evt == "pipeline:cost_estimate"]
    assert len(cost_events) == 1

    payload = cost_events[0][1]
    assert isinstance(payload["estimated_cost"], float)
    assert payload["estimated_cost"] > 0
    assert payload["estimated_cost_usd"] == payload["estimated_cost"]
    assert payload["task_count"] == 2


# -- Test: events emitted without pipeline_id use _events.emit directly -----


async def test_plan_without_pipeline_id_uses_events_emit():
    """When pipeline_id is None, events go through _events.emit() (not _emit)."""
    emitted_events: list[tuple[str, dict]] = []

    emitter = EventEmitter()

    for evt in ("pipeline:phase_changed", "planner:output", "pipeline:plan_ready"):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitted_events.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(return_value=VALID_GRAPH)
    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        await daemon.plan(
            "Build something",
            db,
            emit_plan_ready=True,
            pipeline_id=None,
        )

    # Events should still be emitted via _events.emit()
    event_types = [evt for evt, _ in emitted_events]
    assert "pipeline:phase_changed" in event_types
    assert "pipeline:plan_ready" in event_types

    # db.log_event should NOT have been called (no pipeline_id means no DB logging)
    db.log_event.assert_not_called()


async def test_plan_surfaces_provider_status_activity_in_planner_output():
    """Planner status events should be visible in the planning panel."""
    emitted_events: list[tuple[str, dict]] = []

    emitter = EventEmitter()

    for evt in ("pipeline:phase_changed", "planner:output", "pipeline:plan_ready"):
        handler = AsyncMock(side_effect=lambda data, evt=evt: emitted_events.append((evt, data)))
        emitter.on(evt, handler)

    daemon = _make_daemon(event_emitter=emitter)
    db = _make_mock_db()

    async def _plan_side_effect(*args, **kwargs):
        on_message = kwargs.get("on_message")
        assert on_message is not None
        await on_message(ProviderEvent(kind=EventKind.STATUS, status="thinking"))
        return VALID_GRAPH

    mock_planner_instance = AsyncMock()
    mock_planner_instance.plan = AsyncMock(side_effect=_plan_side_effect)
    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.Planner", return_value=mock_planner_instance),
        patch("forge.core.daemon.gather_project_snapshot") as mock_snapshot,
    ):
        mock_snapshot.return_value = MagicMock()
        mock_snapshot.return_value.format_for_planner.return_value = "snapshot"

        await daemon.plan(
            "Build something",
            db,
            emit_plan_ready=True,
            pipeline_id="pipe-status",
        )

    planner_lines = [data["line"] for evt, data in emitted_events if evt == "planner:output"]
    assert "Thinking…" not in planner_lines
