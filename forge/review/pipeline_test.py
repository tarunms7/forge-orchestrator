import pytest
from unittest.mock import AsyncMock

from forge.review.pipeline import ReviewPipeline, GateResult


def _pass():
    return GateResult(passed=True, gate="test", details="OK")


def _fail(gate: str, details: str):
    return GateResult(passed=False, gate=gate, details=details)


@pytest.fixture
def mock_gate1():
    return AsyncMock()


@pytest.fixture
def mock_gate2():
    return AsyncMock()


@pytest.fixture
def mock_gate3():
    return AsyncMock()


async def test_all_gates_pass(mock_gate1, mock_gate2, mock_gate3):
    mock_gate1.return_value = _pass()
    mock_gate2.return_value = _pass()
    mock_gate3.return_value = _pass()
    pipeline = ReviewPipeline(gate1=mock_gate1, gate2=mock_gate2, gate3=mock_gate3, max_retries=3)
    outcome = await pipeline.review("task-1")
    assert outcome.approved is True
    assert outcome.gate_results[0].passed is True


async def test_gate1_fail_stops_pipeline(mock_gate1, mock_gate2, mock_gate3):
    mock_gate1.return_value = _fail("auto-check", "Tests failed")
    pipeline = ReviewPipeline(gate1=mock_gate1, gate2=mock_gate2, gate3=mock_gate3, max_retries=3)
    outcome = await pipeline.review("task-1")
    assert outcome.approved is False
    assert outcome.failed_gate == "auto-check"
    mock_gate2.assert_not_called()
    mock_gate3.assert_not_called()


async def test_gate2_fail_stops_pipeline(mock_gate1, mock_gate2, mock_gate3):
    mock_gate1.return_value = _pass()
    mock_gate2.return_value = _fail("llm-review", "Code quality issues")
    pipeline = ReviewPipeline(gate1=mock_gate1, gate2=mock_gate2, gate3=mock_gate3, max_retries=3)
    outcome = await pipeline.review("task-1")
    assert outcome.approved is False
    assert outcome.failed_gate == "llm-review"
    mock_gate3.assert_not_called()
