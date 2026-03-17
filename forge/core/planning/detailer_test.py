# forge/core/planning/detailer_test.py
import pytest
from forge.core.models import TaskDefinition
from forge.core.planning.detailer import Detailer, DetailerFactory
from forge.core.planning.models import CodebaseMap


class FakeSdkResult:
    def __init__(self, text: str):
        self.result = text
        self.result_text = text
        self.cost_usd = 0.03
        self.input_tokens = 500
        self.output_tokens = 300
        self.session_id = "sess-d"
        self.is_error = False
        self.duration_ms = 3000


@pytest.fixture
def sample_task():
    return TaskDefinition(id="task-1", title="Add models", description="Create data models", files=["src/models.py"])

@pytest.fixture
def sample_map():
    return CodebaseMap(architecture_summary="Test", key_modules=[])


@pytest.mark.asyncio
async def test_detailer_enriches_description(sample_task, sample_map, monkeypatch):
    enriched_text = "Create src/models.py with UserModel(BaseModel) class. Fields: id (int), name (str), email (str). Add validators for email format. Test: test_user_model_valid, test_user_model_invalid_email."
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(enriched_text)
    monkeypatch.setattr("forge.core.planning.detailer.sdk_query", mock_sdk_query)
    detailer = Detailer(model="sonnet", cwd="/tmp")
    result = await detailer.run(task=sample_task, codebase_map=sample_map, conventions="")
    assert "UserModel" in result.enriched_description
    assert result.cost_usd > 0


@pytest.mark.asyncio
async def test_detailer_factory_runs_parallel(sample_map, monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(f"Enriched: {prompt[:20]}")
    monkeypatch.setattr("forge.core.planning.detailer.sdk_query", mock_sdk_query)
    tasks = [TaskDefinition(id=f"t{i}", title=f"Task {i}", description=f"Do thing {i}", files=[f"f{i}.py"]) for i in range(4)]
    factory = DetailerFactory(model="sonnet", cwd="/tmp", max_concurrent=2)
    results = await factory.run_all(tasks=tasks, codebase_map=sample_map, conventions="")
    assert len(results) == 4
    assert all(r.enriched_description for r in results)


@pytest.mark.asyncio
async def test_detailer_limits_tools_and_prompt_scope(sample_task, sample_map, monkeypatch):
    captured: dict = {}

    async def mock_sdk_query(prompt, options, on_message=None):
        captured["prompt"] = prompt
        captured["disallowed_tools"] = set(options.disallowed_tools or [])
        captured["max_turns"] = options.max_turns
        return FakeSdkResult("Enriched task text with concrete edits.")

    monkeypatch.setattr("forge.core.planning.detailer.sdk_query", mock_sdk_query)
    detailer = Detailer(model="sonnet", cwd="/tmp")

    await detailer.run(task=sample_task, codebase_map=sample_map, conventions="")

    assert captured["max_turns"] == 3
    assert {"Bash", "Glob", "Grep", "Task", "Edit", "Write"} <= captured["disallowed_tools"]
    assert "Include exact function signatures, test file paths, and edge cases." not in captured["prompt"]
    assert "Do not add new audit items, new risks, or unrelated refactors." in captured["prompt"]
