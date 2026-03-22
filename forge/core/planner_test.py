import json
from unittest.mock import AsyncMock

import pytest

from forge.core.errors import SdkCallError, ValidationError
from forge.core.models import TaskGraph
from forge.core.planner import Planner, PlannerLLM

VALID_GRAPH_JSON = json.dumps(
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

CYCLIC_GRAPH_JSON = json.dumps(
    {
        "tasks": [
            {
                "id": "task-1",
                "title": "A",
                "description": "A",
                "files": ["a.py"],
                "depends_on": ["task-2"],
                "complexity": "low",
            },
            {
                "id": "task-2",
                "title": "B",
                "description": "B",
                "files": ["b.py"],
                "depends_on": ["task-1"],
                "complexity": "low",
            },
        ]
    }
)


@pytest.fixture
def mock_llm():
    return AsyncMock(spec=PlannerLLM)


async def test_plan_returns_valid_task_graph(mock_llm):
    mock_llm.generate_plan.return_value = VALID_GRAPH_JSON
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build a REST API with auth")
    assert isinstance(graph, TaskGraph)
    assert len(graph.tasks) == 2


async def test_plan_retries_on_invalid_graph(mock_llm):
    mock_llm.generate_plan.side_effect = [
        '{"tasks": []}',
        VALID_GRAPH_JSON,
    ]
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build something")
    assert len(graph.tasks) == 2
    assert mock_llm.generate_plan.call_count == 2


async def test_plan_retries_on_cyclic_graph(mock_llm):
    mock_llm.generate_plan.side_effect = [
        CYCLIC_GRAPH_JSON,
        VALID_GRAPH_JSON,
    ]
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build something")
    assert len(graph.tasks) == 2


async def test_plan_fails_after_max_retries(mock_llm):
    mock_llm.generate_plan.return_value = '{"tasks": []}'
    planner = Planner(llm=mock_llm, max_retries=2)
    with pytest.raises(ValidationError, match="retries"):
        await planner.plan("Build something")
    assert mock_llm.generate_plan.call_count == 2


async def test_plan_retries_on_sdk_call_error(mock_llm):
    """When generate_plan raises SdkCallError, planner retries with SDK error feedback."""
    mock_llm.generate_plan.side_effect = [
        SdkCallError("SDK call failed: rate limit", original_error=RuntimeError("rate limit")),
        VALID_GRAPH_JSON,
    ]
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build something")
    assert len(graph.tasks) == 2
    assert mock_llm.generate_plan.call_count == 2
    # Second call should include SDK error feedback
    second_call_feedback = mock_llm.generate_plan.call_args_list[1][0][2]
    assert "SDK error" in second_call_feedback


async def test_plan_exhausts_retries_on_repeated_sdk_errors(mock_llm):
    """When all attempts raise SdkCallError, planner raises ValidationError after exhausting retries."""
    mock_llm.generate_plan.side_effect = SdkCallError(
        "SDK call failed: timeout", original_error=TimeoutError("timeout")
    )
    planner = Planner(llm=mock_llm, max_retries=2)
    with pytest.raises(ValidationError, match="retries"):
        await planner.plan("Build something")
    assert mock_llm.generate_plan.call_count == 2
