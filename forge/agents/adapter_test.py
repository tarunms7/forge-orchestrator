import pytest
from forge.agents.adapter import AgentAdapter, AgentResult, ClaudeAdapter


def test_agent_result_fields():
    result = AgentResult(
        success=True,
        files_changed=["a.py", "b.py"],
        summary="Added user model",
        token_usage=5000,
    )
    assert result.success is True
    assert len(result.files_changed) == 2
    assert result.token_usage == 5000


def test_agent_result_failure():
    result = AgentResult(
        success=False,
        files_changed=[],
        summary="Could not parse requirements",
        error="ValueError: missing field",
    )
    assert result.success is False
    assert result.error is not None


def test_claude_adapter_is_agent_adapter():
    adapter = ClaudeAdapter()
    assert isinstance(adapter, AgentAdapter)


def test_adapter_has_run_method():
    adapter = ClaudeAdapter()
    assert callable(getattr(adapter, "run", None))
