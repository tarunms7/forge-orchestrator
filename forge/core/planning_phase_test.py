"""Tests for planning-phase bugs.

Bug 1: daemon.plan() emits pipeline:phase_changed with phase='planning' at the
start and pipeline:plan_ready at the end, but NEVER emits
pipeline:phase_changed with phase='planned'. The frontend relies on a
phase_changed:planned event to transition the UI out of 'planning'.

Bug 2: ForgeSettings().max_retries defaults to 3, but should be 5.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon
from forge.core.events import EventEmitter
from forge.core.models import Complexity, TaskDefinition, TaskGraph


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

VALID_TASK_GRAPH = TaskGraph(
    tasks=[
        TaskDefinition(
            id="task-1",
            title="Create model",
            description="Build user model",
            files=["src/models/user.py"],
            depends_on=[],
            complexity=Complexity.LOW,
        ),
    ]
)


@pytest.fixture
def event_emitter():
    return EventEmitter()


@pytest.fixture
def captured_events(event_emitter):
    """Register a catch-all handler that records every emitted event."""
    events: list[tuple[str, dict]] = []

    # We monkey-patch emit to capture all events with their names
    _original_emit = event_emitter.emit

    async def _capturing_emit(event: str, data=None):
        events.append((event, data))
        await _original_emit(event, data)

    event_emitter.emit = _capturing_emit
    return events


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.log_event = AsyncMock()
    db.get_pipeline = AsyncMock(return_value=None)
    db.add_pipeline_cost = AsyncMock()
    db.set_pipeline_planner_cost = AsyncMock()
    db.get_pipeline_cost = AsyncMock(return_value=0.0)
    db.update_pipeline_conventions = AsyncMock()
    return db


@pytest.fixture
def daemon(event_emitter):
    settings = ForgeSettings()
    return ForgeDaemon(
        project_dir="/tmp/fake-project",
        settings=settings,
        event_emitter=event_emitter,
    )


# ---------------------------------------------------------------------------
# Bug 1: plan() must emit pipeline:phase_changed with phase='planned'
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_emits_phase_changed_planned_after_plan_ready(
    daemon, mock_db, captured_events
):
    """daemon.plan() should emit pipeline:phase_changed with phase='planned'
    AFTER emitting pipeline:plan_ready.

    Currently FAILS because plan() only emits phase_changed:'planning' at the
    start and plan_ready at the end — it never emits phase_changed:'planned'.
    """
    pipeline_id = "pipe-001"

    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    mock_snapshot = MagicMock()
    mock_snapshot.format_for_planner.return_value = "snapshot context"

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.select_model", return_value="claude-sonnet-4-20250514"),
        patch("forge.core.daemon.gather_project_snapshot", return_value=mock_snapshot),
        patch("forge.core.daemon.Planner") as MockPlanner,
        patch("forge.core.daemon.estimate_pipeline_cost", new_callable=AsyncMock, return_value=0.05),
    ):
        mock_planner_instance = AsyncMock()
        mock_planner_instance.plan.return_value = VALID_TASK_GRAPH
        MockPlanner.return_value = mock_planner_instance

        await daemon.plan("Build something", mock_db, pipeline_id=pipeline_id)

    # Collect all phase_changed events
    phase_changed_events = [
        (evt, data) for evt, data in captured_events
        if evt == "pipeline:phase_changed"
    ]

    # Must have at least two phase_changed events: 'planning' and 'planned'
    phases = [data["phase"] for _, data in phase_changed_events]
    assert "planning" in phases, "Expected phase_changed with phase='planning'"
    assert "planned" in phases, (
        "Expected phase_changed with phase='planned' but got phases: "
        f"{phases}. Bug: daemon.plan() never emits phase_changed:'planned'."
    )

    # 'planned' must come AFTER 'plan_ready'
    all_event_names = [evt for evt, _ in captured_events]
    plan_ready_idx = all_event_names.index("pipeline:plan_ready")
    planned_idx = next(
        i for i, (evt, data) in enumerate(captured_events)
        if evt == "pipeline:phase_changed" and data.get("phase") == "planned"
    )
    assert planned_idx > plan_ready_idx, (
        "phase_changed:'planned' must be emitted AFTER plan_ready"
    )


@pytest.mark.asyncio
async def test_plan_emits_phase_changed_planned_without_pipeline_id(
    daemon, captured_events
):
    """Even without a pipeline_id, plan() should emit phase_changed:'planned'
    on the EventEmitter after plan_ready.

    Currently FAILS for the same reason as the above test.
    """
    mock_planner_llm = MagicMock()
    mock_planner_llm._last_sdk_result = None

    mock_snapshot = MagicMock()
    mock_snapshot.format_for_planner.return_value = "snapshot context"

    with (
        patch("forge.core.daemon.ClaudePlannerLLM", return_value=mock_planner_llm),
        patch("forge.core.daemon.select_model", return_value="claude-sonnet-4-20250514"),
        patch("forge.core.daemon.gather_project_snapshot", return_value=mock_snapshot),
        patch("forge.core.daemon.Planner") as MockPlanner,
    ):
        mock_planner_instance = AsyncMock()
        mock_planner_instance.plan.return_value = VALID_TASK_GRAPH
        MockPlanner.return_value = mock_planner_instance

        # No pipeline_id → uses self._events.emit() directly
        await daemon.plan("Build something", AsyncMock())

    phase_changed_events = [
        (evt, data) for evt, data in captured_events
        if evt == "pipeline:phase_changed"
    ]
    phases = [data["phase"] for _, data in phase_changed_events]
    assert "planned" in phases, (
        "Expected phase_changed with phase='planned' but got phases: "
        f"{phases}. Bug: daemon.plan() never emits phase_changed:'planned'."
    )


# ---------------------------------------------------------------------------
# Bug 2: max_retries should default to 5, not 3
# ---------------------------------------------------------------------------

def test_max_retries_default_is_five():
    """ForgeSettings().max_retries should default to 5.

    Currently FAILS because the default is 3 (settings.py line 18).
    """
    s = ForgeSettings()
    assert s.max_retries == 5, (
        f"Expected max_retries default to be 5, got {s.max_retries}"
    )
