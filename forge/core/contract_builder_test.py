"""Tests for the Contract Builder (mocked LLM)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from forge.core.contract_builder import ContractBuilder, ContractBuilderLLM
from forge.core.contracts import ContractType, IntegrationHint
from forge.core.models import Complexity, TaskDefinition, TaskGraph
from forge.core.sanitize import extract_json_block as _extract_json_block

# -- Helpers ---------------------------------------------------------------


def _sample_graph() -> TaskGraph:
    return TaskGraph(
        tasks=[
            TaskDefinition(
                id="task-1",
                title="Build API",
                description="REST endpoints",
                files=["api.py"],
                complexity=Complexity.MEDIUM,
            ),
            TaskDefinition(
                id="task-2",
                title="Build UI",
                description="React components",
                files=["ui.tsx"],
                complexity=Complexity.MEDIUM,
            ),
        ]
    )


def _sample_hints() -> list[IntegrationHint]:
    return [
        IntegrationHint(
            producer_task_id="task-1",
            consumer_task_ids=["task-2"],
            interface_type=ContractType.API_ENDPOINT,
            description="Templates API",
            endpoint_hints=["GET /api/templates"],
        ),
    ]


def _valid_contract_json() -> str:
    return json.dumps(
        {
            "api_contracts": [
                {
                    "id": "contract-api-1",
                    "method": "GET",
                    "path": "/api/templates",
                    "description": "List templates",
                    "request_body": None,
                    "response_body": [
                        {"name": "items", "type": "Template[]", "required": True},
                    ],
                    "response_example": '{"items": []}',
                    "auth_required": True,
                    "producer_task_id": "task-1",
                    "consumer_task_ids": ["task-2"],
                }
            ],
            "type_contracts": [
                {
                    "name": "Template",
                    "description": "A template",
                    "field_specs": [
                        {"name": "id", "type": "string", "required": True},
                    ],
                    "used_by_tasks": ["task-1", "task-2"],
                }
            ],
        }
    )


# -- _extract_json_block tests --------------------------------------------------


class TestExtractJson:
    def test_plain_json(self):
        result = _extract_json_block('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_json_in_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_block(text)
        assert result == '{"key": "value"}'

    def test_json_with_surrounding_text(self):
        text = 'Here is the output:\n{"key": "value"}\nDone.'
        result = _extract_json_block(text)
        assert result == '{"key": "value"}'

    def test_empty_string(self):
        result = _extract_json_block("")
        assert result is None


# -- ContractBuilder._parse_and_validate tests ----------------------------


class TestParseAndValidate:
    def setup_method(self):
        self.llm = ContractBuilderLLM()
        self.builder = ContractBuilder(self.llm)
        self.graph = _sample_graph()

    def test_valid_json(self):
        cs, err = self.builder._parse_and_validate(_valid_contract_json(), self.graph)
        assert cs is not None
        assert err is None
        assert len(cs.api_contracts) == 1
        assert len(cs.type_contracts) == 1

    def test_empty_response(self):
        cs, err = self.builder._parse_and_validate("", self.graph)
        assert cs is None
        assert "Empty response" in err

    def test_invalid_json(self):
        cs, err = self.builder._parse_and_validate("{invalid", self.graph)
        assert cs is None
        assert "Invalid JSON" in err

    def test_unknown_producer_task(self):
        bad_json = json.dumps(
            {
                "api_contracts": [
                    {
                        "id": "c1",
                        "method": "GET",
                        "path": "/api/x",
                        "response_body": [{"name": "x", "type": "string"}],
                        "producer_task_id": "task-99",
                        "consumer_task_ids": ["task-2"],
                    }
                ],
                "type_contracts": [],
            }
        )
        cs, err = self.builder._parse_and_validate(bad_json, self.graph)
        assert cs is None
        assert "unknown producer task" in err

    def test_unknown_consumer_task(self):
        bad_json = json.dumps(
            {
                "api_contracts": [
                    {
                        "id": "c1",
                        "method": "GET",
                        "path": "/api/x",
                        "response_body": [{"name": "x", "type": "string"}],
                        "producer_task_id": "task-1",
                        "consumer_task_ids": ["task-99"],
                    }
                ],
                "type_contracts": [],
            }
        )
        cs, err = self.builder._parse_and_validate(bad_json, self.graph)
        assert cs is None
        assert "unknown consumer task" in err

    def test_unknown_type_task(self):
        bad_json = json.dumps(
            {
                "api_contracts": [],
                "type_contracts": [
                    {
                        "name": "Foo",
                        "field_specs": [{"name": "x", "type": "string"}],
                        "used_by_tasks": ["task-99"],
                    }
                ],
            }
        )
        cs, err = self.builder._parse_and_validate(bad_json, self.graph)
        assert cs is None
        assert "unknown task" in err


# -- ContractBuilder.build tests (mocked LLM) ----------------------------


@pytest.mark.asyncio
async def test_build_success():
    """Valid LLM response → successful ContractSet."""
    llm = ContractBuilderLLM()
    builder = ContractBuilder(llm, max_retries=2)
    graph = _sample_graph()
    hints = _sample_hints()

    with patch.object(llm, "generate_contracts", new_callable=AsyncMock) as mock:
        mock.return_value = _valid_contract_json()
        result = await builder.build(graph, hints)

    assert result.has_contracts()
    assert len(result.api_contracts) == 1


@pytest.mark.asyncio
async def test_build_graceful_degradation():
    """All retries fail → empty ContractSet (no crash)."""
    llm = ContractBuilderLLM()
    builder = ContractBuilder(llm, max_retries=2)
    graph = _sample_graph()
    hints = _sample_hints()

    with patch.object(llm, "generate_contracts", new_callable=AsyncMock) as mock:
        mock.return_value = ""  # Empty = failure
        result = await builder.build(graph, hints)

    assert not result.has_contracts()
    assert result.api_contracts == []


@pytest.mark.asyncio
async def test_build_retry_on_invalid_then_succeed():
    """First attempt invalid, second attempt valid."""
    llm = ContractBuilderLLM()
    builder = ContractBuilder(llm, max_retries=3)
    graph = _sample_graph()
    hints = _sample_hints()

    with patch.object(llm, "generate_contracts", new_callable=AsyncMock) as mock:
        mock.side_effect = ["{invalid json", _valid_contract_json()]
        result = await builder.build(graph, hints)

    assert result.has_contracts()
    assert mock.call_count == 2


@pytest.mark.asyncio
async def test_build_passes_feedback_on_retry():
    """Validation error from first attempt should be passed as feedback to second."""
    llm = ContractBuilderLLM()
    builder = ContractBuilder(llm, max_retries=3)
    graph = _sample_graph()
    hints = _sample_hints()

    with patch.object(llm, "generate_contracts", new_callable=AsyncMock) as mock:
        mock.side_effect = ["{invalid json", _valid_contract_json()]
        await builder.build(graph, hints)

    # First call: no feedback
    first_call_kwargs = mock.call_args_list[0][1]
    assert first_call_kwargs.get("feedback") is None

    # Second call: should have feedback with the validation error
    second_call_kwargs = mock.call_args_list[1][1]
    assert second_call_kwargs.get("feedback") is not None
    assert "Invalid JSON" in second_call_kwargs["feedback"]


@pytest.mark.asyncio
async def test_build_no_hints_skip():
    """Empty hints list should return empty ContractSet immediately."""
    llm = ContractBuilderLLM()
    builder = ContractBuilder(llm, max_retries=2)
    graph = _sample_graph()

    # build() is called but with empty hints — ContractBuilder doesn't skip,
    # that's the daemon's job. However, LLM should still be called.
    # Test the daemon skip logic via generate_contracts instead.
    # Here we just verify builder works with empty hints.
    with patch.object(llm, "generate_contracts", new_callable=AsyncMock) as mock:
        mock.return_value = '{"api_contracts": [], "type_contracts": []}'
        result = await builder.build(graph, [])

    assert not result.has_contracts()


class TestExtractJsonNestedBraces:
    def test_nested_json_in_fence(self):
        """Greedy regex should capture the full JSON with nested braces."""
        nested = '{"outer": {"inner": {"deep": "value"}}}'
        text = f"```json\n{nested}\n```"
        result = _extract_json_block(text)
        assert result == nested
