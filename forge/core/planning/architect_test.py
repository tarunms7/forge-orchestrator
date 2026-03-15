# forge/core/planning/architect_test.py
import json
import pytest
from forge.core.models import TaskGraph
from forge.core.planning.architect import Architect, ArchitectResult
from forge.core.planning.models import CodebaseMap, PlanFeedback


class FakeSdkResult:
    def __init__(self, text: str, session_id: str = "sess-1"):
        self.result = text
        self.result_text = text
        self.cost_usd = 0.10
        self.input_tokens = 2000
        self.output_tokens = 1000
        self.session_id = session_id
        self.is_error = False
        self.duration_ms = 8000


@pytest.fixture
def valid_graph_json():
    return json.dumps({
        "tasks": [{"id": "task-1", "title": "Add models", "description": "Create data models for the feature", "files": ["src/models.py"], "depends_on": [], "complexity": "low"}],
    })

@pytest.fixture
def sample_map():
    return CodebaseMap(architecture_summary="Test project", key_modules=[])


@pytest.mark.asyncio
async def test_architect_produces_task_graph(valid_graph_json, sample_map, monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(valid_graph_json)
    monkeypatch.setattr("forge.core.planning.architect.sdk_query", mock_sdk_query)
    arch = Architect(model="opus", cwd="/tmp")
    result = await arch.run(user_input="Build feature X", spec_text="Feature X spec", codebase_map=sample_map, conventions="")
    assert isinstance(result.task_graph, TaskGraph)
    assert len(result.task_graph.tasks) == 1
    assert result.task_graph.tasks[0].id == "task-1"


@pytest.mark.asyncio
async def test_architect_detects_forge_question(sample_map, monkeypatch):
    question_response = 'Some analysis...\n\nFORGE_QUESTION:\n{"question": "JWT or session?", "context": "Auth needed", "suggestions": ["JWT", "Session"], "impact": "high"}'
    call_count = 0
    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeSdkResult(question_response, session_id="sess-q1")
        return FakeSdkResult('{"tasks": [{"id": "t1", "title": "T", "description": "D", "files": ["a.py"]}]}')
    monkeypatch.setattr("forge.core.planning.architect.sdk_query", mock_sdk_query)

    questions_received = []
    async def on_question(q):
        questions_received.append(q)
        return "JWT"

    arch = Architect(model="opus", cwd="/tmp")
    result = await arch.run(user_input="x", spec_text="x", codebase_map=sample_map, conventions="", on_question=on_question)
    assert len(questions_received) == 1
    assert result.task_graph is not None


@pytest.mark.asyncio
async def test_architect_accepts_replan_feedback(valid_graph_json, sample_map, monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(valid_graph_json)
    monkeypatch.setattr("forge.core.planning.architect.sdk_query", mock_sdk_query)

    feedback = PlanFeedback(iteration=2, issues=[], preserved_tasks=["task-0"], replan_scope="Replan task-1 only")
    arch = Architect(model="opus", cwd="/tmp")
    result = await arch.run(user_input="x", spec_text="x", codebase_map=sample_map, conventions="", feedback=feedback)
    assert result.task_graph is not None
