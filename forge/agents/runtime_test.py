import json
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
        cost_usd=0.01,
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
        allowed_dirs=None,
        model="sonnet",
        on_message=None,
        project_context="",
        conventions_json=None,
        conventions_md=None,
        completed_deps=None,
        contracts_block="",
        resume=None,
        autonomy="balanced",
        questions_remaining=3,
        project_dir=None,
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


async def test_runtime_passes_on_message_to_adapter():
    """AgentRuntime.run_task() should forward on_message to adapter.run()."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True, files_changed=["a.py"], summary="Done",
    )
    callback = AsyncMock()

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="test",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        on_message=callback,
    )

    mock_adapter.run.assert_called_once()
    call_kwargs = mock_adapter.run.call_args[1]
    assert call_kwargs["on_message"] is callback
    assert result.success is True


async def test_runtime_passes_conventions_to_adapter():
    """AgentRuntime.run_task() should forward conventions params to adapter.run()."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True, files_changed=[], summary="Done",
    )
    conventions_md = "## Style\n\nUse black."
    conventions_json = json.dumps({"Testing": "pytest"})

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    await runtime.run_task(
        agent_id="agent-1",
        task_prompt="test",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        conventions_json=conventions_json,
        conventions_md=conventions_md,
    )

    call_kwargs = mock_adapter.run.call_args[1]
    assert call_kwargs["conventions_json"] == conventions_json
    assert call_kwargs["conventions_md"] == conventions_md


async def test_runtime_passes_completed_deps_to_adapter():
    """AgentRuntime.run_task() should forward completed_deps to adapter.run()."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True, files_changed=[], summary="Done",
    )
    deps = [
        {
            "task_id": "task-1",
            "title": "Add models",
            "implementation_summary": "Created models",
            "files_changed": ["models.py"],
        }
    ]

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    await runtime.run_task(
        agent_id="agent-1",
        task_prompt="test",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        completed_deps=deps,
    )

    call_kwargs = mock_adapter.run.call_args[1]
    assert call_kwargs["completed_deps"] == deps


async def test_runtime_passes_all_new_params_together():
    """All three new params should be forwarded correctly when used together."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True, files_changed=[], summary="Done",
    )
    conventions_md = "## Lint\nUse ruff."
    conventions_json = json.dumps({"Naming": "snake_case"})
    deps = [{"task_id": "t1", "title": "Setup", "implementation_summary": "Init", "files_changed": []}]

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build Y",
        worktree_path="/tmp/wt2",
        allowed_files=["b.py"],
        conventions_json=conventions_json,
        conventions_md=conventions_md,
        completed_deps=deps,
    )

    mock_adapter.run.assert_called_once_with(
        task_prompt="Build Y",
        worktree_path="/tmp/wt2",
        allowed_files=["b.py"],
        timeout_seconds=60,
        allowed_dirs=None,
        model="sonnet",
        on_message=None,
        project_context="",
        conventions_json=conventions_json,
        conventions_md=conventions_md,
        completed_deps=deps,
        contracts_block="",
        resume=None,
        autonomy="balanced",
        questions_remaining=3,
        project_dir=None,
    )


async def test_runtime_passes_autonomy_settings_to_adapter():
    """AgentRuntime.run_task() should forward autonomy and questions_remaining to adapter."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True, files_changed=[], summary="Done",
    )

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    await runtime.run_task(
        agent_id="agent-1",
        task_prompt="test",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        autonomy="full",
        questions_remaining=0,
    )

    call_kwargs = mock_adapter.run.call_args[1]
    assert call_kwargs["autonomy"] == "full"
    assert call_kwargs["questions_remaining"] == 0
