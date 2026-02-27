import pytest
from unittest.mock import AsyncMock

from forge.agents.adapter import AgentResult
from forge.agents.runtime import AgentRuntime


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.run.return_value = AgentResult(
        success=True,
        files_changed=["a.py"],
        summary="Done",
        token_usage=1000,
    )
    return adapter


async def test_run_task_calls_adapter(mock_adapter):
    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
    )
    assert result.success is True
    mock_adapter.run.assert_called_once_with(
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
        timeout_seconds=60,
    )


async def test_run_task_catches_timeout(mock_adapter):
    mock_adapter.run.side_effect = TimeoutError("timed out")
    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
    )
    assert result.success is False
    assert "timeout" in result.error.lower()


async def test_run_task_catches_unexpected_error(mock_adapter):
    mock_adapter.run.side_effect = RuntimeError("kaboom")
    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
    )
    assert result.success is False
    assert "kaboom" in result.error
