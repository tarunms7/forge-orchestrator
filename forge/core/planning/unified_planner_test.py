# forge/core/planning/unified_planner_test.py
import json
import pytest
from forge.core.models import TaskGraph
from forge.core.planning.unified_planner import UnifiedPlanner, UnifiedPlannerResult


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
        "tasks": [
            {"id": "task-1", "title": "Add models", "description": "Create data models for the feature with proper validation and tests", "files": ["src/models.py"], "depends_on": [], "complexity": "low"},
            {"id": "task-2", "title": "Add routes", "description": "Create API routes that use the models from task-1 with error handling", "files": ["src/routes.py"], "depends_on": ["task-1"], "complexity": "medium"},
        ],
    })


@pytest.mark.asyncio
async def test_unified_planner_produces_task_graph(valid_graph_json, monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(valid_graph_json)
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp")
    result = await planner.run(user_input="Build an API", spec_text="API spec", snapshot_text="## Project\n10 files")
    assert isinstance(result.task_graph, TaskGraph)
    assert len(result.task_graph.tasks) == 2
    assert result.task_graph.tasks[0].id == "task-1"
    assert result.cost_usd > 0
    assert result.validation_result is not None
    assert result.validation_result.status == "pass"


@pytest.mark.asyncio
async def test_unified_planner_detects_forge_question(monkeypatch):
    question_response = 'I explored the codebase and found...\n\nFORGE_QUESTION:\n{"question": "JWT or session auth?", "context": "Auth needed", "suggestions": ["JWT", "Session"], "impact": "high"}'
    call_count = 0
    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeSdkResult(question_response, session_id="sess-q1")
        return FakeSdkResult(json.dumps({
            "tasks": [{"id": "t1", "title": "T", "description": "Detailed description for task with auth and JWT implementation", "files": ["auth.py"]}]
        }))
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)

    questions_received = []
    async def on_question(q):
        questions_received.append(q)
        return "JWT"

    planner = UnifiedPlanner(model="opus", cwd="/tmp")
    result = await planner.run(user_input="Add auth", spec_text="", snapshot_text="", on_question=on_question)
    assert len(questions_received) == 1
    assert questions_received[0]["question"] == "JWT or session auth?"
    assert result.task_graph is not None


@pytest.mark.asyncio
async def test_unified_planner_retries_on_invalid_json(monkeypatch):
    call_count = 0
    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeSdkResult("Not valid JSON at all")
        return FakeSdkResult(json.dumps({
            "tasks": [{"id": "t1", "title": "T", "description": "Detailed description for the implementation task", "files": ["a.py"]}]
        }))
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp")
    result = await planner.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.task_graph is not None
    assert call_count == 2


@pytest.mark.asyncio
async def test_unified_planner_returns_none_after_max_retries(monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult("garbage output with no JSON")
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp", max_retries=2)
    result = await planner.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.task_graph is None
    assert result.cost_usd > 0


@pytest.mark.asyncio
async def test_unified_planner_has_full_tool_access(monkeypatch):
    """Verify the planner does NOT disallow Read/Glob/Grep/Bash."""
    captured = {}
    async def mock_sdk_query(prompt, options, on_message=None):
        captured["disallowed_tools"] = options.disallowed_tools
        captured["max_turns"] = options.max_turns
        return FakeSdkResult(json.dumps({
            "tasks": [{"id": "t1", "title": "T", "description": "Detailed description for the implementation task here", "files": ["a.py"]}]
        }))
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp", max_turns=30)
    await planner.run(user_input="x", spec_text="x", snapshot_text="x")

    # Should only disallow Edit and Write — NOT Bash, Glob, Grep, Read
    assert "Edit" in captured["disallowed_tools"]
    assert "Write" in captured["disallowed_tools"]
    assert "Bash" not in captured["disallowed_tools"]
    assert "Glob" not in captured["disallowed_tools"]
    assert "Grep" not in captured["disallowed_tools"]
    assert "Read" not in captured["disallowed_tools"]
    assert captured["max_turns"] == 30


@pytest.mark.asyncio
async def test_unified_planner_runs_validation(monkeypatch):
    """Planner should detect file conflicts and cycle issues."""
    # Two independent tasks sharing a file → file_conflict (major)
    graph_with_conflict = json.dumps({
        "tasks": [
            {"id": "t1", "title": "T1", "description": "Task one modifies shared.py with feature A implementation", "files": ["shared.py"]},
            {"id": "t2", "title": "T2", "description": "Task two also modifies shared.py with feature B implementation", "files": ["shared.py"]},
        ]
    })
    call_count = 0
    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeSdkResult(graph_with_conflict)
        # After validation feedback, produce a fixed graph
        return FakeSdkResult(json.dumps({
            "tasks": [
                {"id": "t1", "title": "T1", "description": "Task one modifies shared.py with feature A implementation", "files": ["shared.py"]},
                {"id": "t2", "title": "T2", "description": "Task two modifies other.py with feature B implementation", "files": ["other.py"], "depends_on": ["t1"]},
            ]
        }))
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp")
    result = await planner.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.task_graph is not None
    # Agent should have been called twice: once with conflict, once with fix
    assert call_count == 2
    assert result.validation_result is not None
    assert result.validation_result.status == "pass"


@pytest.mark.asyncio
async def test_unified_planner_cost_breakdown(monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(json.dumps({
            "tasks": [{"id": "t1", "title": "T", "description": "Detailed description for task with enough chars", "files": ["a.py"]}]
        }))
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp")
    result = await planner.run(user_input="x", spec_text="x", snapshot_text="x")
    assert "planner" in result.cost_breakdown
    assert result.cost_breakdown["planner"] == result.cost_usd
    assert result.total_cost_usd == result.cost_usd


@pytest.mark.asyncio
async def test_unified_planner_handles_sdk_error(monkeypatch):
    call_count = 0
    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("SDK connection failed")
        return FakeSdkResult(json.dumps({
            "tasks": [{"id": "t1", "title": "T", "description": "Detailed description for task with enough chars", "files": ["a.py"]}]
        }))
    monkeypatch.setattr("forge.core.planning.unified_planner.sdk_query", mock_sdk_query)
    planner = UnifiedPlanner(model="opus", cwd="/tmp")
    result = await planner.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.task_graph is not None
    assert call_count == 2
