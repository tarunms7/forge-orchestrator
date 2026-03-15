# forge/core/planning/scout_test.py
import json
import pytest
from forge.core.planning.models import CodebaseMap
from forge.core.planning.scout import Scout


class FakeSdkResult:
    def __init__(self, text: str):
        self.result = text
        self.result_text = text
        self.cost_usd = 0.05
        self.input_tokens = 1000
        self.output_tokens = 500
        self.session_id = "sess-1"
        self.is_error = False
        self.duration_ms = 5000


@pytest.fixture
def valid_map_json():
    return json.dumps({
        "architecture_summary": "Test project with Python backend",
        "key_modules": [{"path": "src/main.py", "purpose": "Entry point", "key_interfaces": ["main()"], "dependencies": [], "loc": 50}],
        "existing_patterns": {"testing": "pytest"},
        "relevant_interfaces": [],
        "risks": [],
    })


@pytest.mark.asyncio
async def test_scout_produces_valid_codebas_map(valid_map_json, monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult(valid_map_json)
    monkeypatch.setattr("forge.core.planning.scout.sdk_query", mock_sdk_query)
    scout = Scout(model="sonnet", cwd="/tmp/test")
    result = await scout.run(user_input="Build an API", spec_text="Build a REST API", snapshot_text="## Project\n10 files")
    assert isinstance(result.codebase_map, CodebaseMap)
    assert result.codebase_map.architecture_summary == "Test project with Python backend"
    assert result.cost_usd > 0


@pytest.mark.asyncio
async def test_scout_retries_on_invalid_json(monkeypatch):
    call_count = 0
    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeSdkResult("not json")
        return FakeSdkResult('{"architecture_summary": "ok", "key_modules": []}')
    monkeypatch.setattr("forge.core.planning.scout.sdk_query", mock_sdk_query)
    scout = Scout(model="sonnet", cwd="/tmp/test")
    result = await scout.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.codebase_map.architecture_summary == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_scout_returns_none_after_max_retries(monkeypatch):
    async def mock_sdk_query(prompt, options, on_message=None):
        return FakeSdkResult("bad json always")
    monkeypatch.setattr("forge.core.planning.scout.sdk_query", mock_sdk_query)
    scout = Scout(model="sonnet", cwd="/tmp/test", max_retries=2)
    result = await scout.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.codebase_map is None
    assert result.cost_usd > 0
