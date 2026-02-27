import pytest
from unittest.mock import AsyncMock, MagicMock

from forge.core.engine import ForgeEngine
from forge.core.models import (
    TaskGraph, TaskDefinition, Complexity,
)
from forge.core.monitor import ResourceSnapshot


@pytest.fixture
def mock_deps():
    return {
        "db": AsyncMock(),
        "planner": AsyncMock(),
        "monitor": MagicMock(),
        "worktree_manager": MagicMock(),
        "agent_runtime": AsyncMock(),
        "review_pipeline": AsyncMock(),
        "merge_worker": MagicMock(),
    }


def _healthy_snapshot():
    return ResourceSnapshot(cpu_percent=30.0, memory_available_pct=70.0, disk_free_gb=50.0)


def _simple_graph():
    return TaskGraph(tasks=[
        TaskDefinition(
            id="task-1", title="Build feature",
            description="Build it", files=["a.py"],
            complexity=Complexity.LOW,
        ),
    ])


async def test_engine_plan_validates_and_stores(mock_deps):
    mock_deps["planner"].plan.return_value = _simple_graph()
    mock_deps["db"].list_tasks.return_value = []

    engine = ForgeEngine(**mock_deps, max_agents=4)
    graph = await engine.plan("Build a feature")

    assert len(graph.tasks) == 1
    mock_deps["planner"].plan.assert_called_once()
    mock_deps["db"].create_task.assert_called_once()


async def test_engine_dispatch_respects_resources(mock_deps):
    snapshot = ResourceSnapshot(cpu_percent=95.0, memory_available_pct=5.0, disk_free_gb=1.0)
    mock_deps["monitor"].take_snapshot.return_value = snapshot
    mock_deps["monitor"].can_dispatch.return_value = False

    engine = ForgeEngine(**mock_deps, max_agents=4)
    dispatched = await engine.dispatch_cycle()

    assert dispatched == 0
