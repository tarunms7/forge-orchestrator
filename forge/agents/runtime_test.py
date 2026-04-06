import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.agents.adapter import AgentResult
from forge.agents.runtime import AgentRuntime, run_with_retry
from forge.providers.base import (
    CatalogEntry,
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderResult,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)


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
        lessons_block="",
        resume=None,
        autonomy="balanced",
        questions_remaining=3,
        project_dir=None,
        agent_max_turns=75,
        project_commands=None,
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
        success=True,
        files_changed=["a.py"],
        summary="Done",
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
        success=True,
        files_changed=[],
        summary="Done",
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
        success=True,
        files_changed=[],
        summary="Done",
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
        success=True,
        files_changed=[],
        summary="Done",
    )
    conventions_md = "## Lint\nUse ruff."
    conventions_json = json.dumps({"Naming": "snake_case"})
    deps = [
        {"task_id": "t1", "title": "Setup", "implementation_summary": "Init", "files_changed": []}
    ]

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
        lessons_block="",
        resume=None,
        autonomy="balanced",
        questions_remaining=3,
        project_dir=None,
        agent_max_turns=75,
        project_commands=None,
    )


async def test_runtime_passes_autonomy_settings_to_adapter():
    """AgentRuntime.run_task() should forward autonomy and questions_remaining to adapter."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True,
        files_changed=[],
        summary="Done",
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


async def test_run_task_stringifies_modelspec_for_adapter():
    """Legacy adapter path should receive provider:model strings, not ModelSpec objects."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True,
        files_changed=[],
        summary="Done",
    )

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    await runtime.run_task(
        agent_id="agent-1",
        task_prompt="test",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        model=ModelSpec("openai", "gpt-5.4-mini"),
    )

    call_kwargs = mock_adapter.run.call_args[1]
    assert call_kwargs["model"] == "openai:gpt-5.4-mini"


async def test_run_task_accepts_modelspec_with_provider_registry(monkeypatch):
    """Provider path should accept pre-resolved ModelSpec values without reparsing them."""
    provider = MagicMock()
    catalog = _make_catalog_entry(
        provider="openai",
        alias="gpt-5.4-mini",
        canonical_id="gpt-5.4-mini-0414",
        backend="codex-sdk",
        tier="supported",
        can_resume_session=False,
        supports_mcp_servers=False,
        supports_structured_output=True,
        cost_key="openai:gpt-5.4-mini",
    )
    provider.start.return_value = MockExecutionHandle(
        _make_provider_result(model_canonical_id="gpt-5.4-mini-0414")
    )

    registry = MagicMock()
    registry.get_for_model.return_value = provider
    registry.get_catalog_entry.return_value = catalog

    monkeypatch.setattr("forge.agents.runtime._get_changed_files", AsyncMock(return_value=[]))

    runtime = AgentRuntime(registry=registry)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        model=ModelSpec("openai", "gpt-5.4-mini"),
    )

    registry.get_for_model.assert_called_once_with(ModelSpec("openai", "gpt-5.4-mini"))
    registry.get_catalog_entry.assert_called_once_with(ModelSpec("openai", "gpt-5.4-mini"))
    assert result.success is True
    assert result.provider_model == "openai:gpt-5.4-mini"


# --- Provider protocol: run_with_retry tests ---


def _make_catalog_entry(**overrides):
    defaults = dict(
        provider="claude",
        alias="sonnet",
        canonical_id="claude-sonnet-4-20250514",
        backend="claude-code-sdk",
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=200000,
        supports_structured_output=False,
        supports_reasoning=False,
        cost_key="claude:sonnet",
        validated_stages=frozenset(["agent"]),
    )
    defaults.update(overrides)
    return CatalogEntry(**defaults)


def _make_provider_result(**overrides):
    defaults = dict(
        text="Task completed",
        is_error=False,
        input_tokens=100,
        output_tokens=200,
        resume_state=None,
        duration_ms=5000,
        provider_reported_cost_usd=0.01,
        model_canonical_id="claude-sonnet-4-20250514",
    )
    defaults.update(overrides)
    return ProviderResult(**defaults)


class MockExecutionHandle:
    """Mock ExecutionHandle for testing."""

    def __init__(self, result: ProviderResult):
        self._result = result
        self.is_running = False
        self.aborted = False

    async def result(self):
        return self._result

    async def abort(self):
        self.aborted = True


async def test_run_with_retry_success():
    """run_with_retry returns success when provider completes normally."""
    provider = MagicMock()
    catalog = _make_catalog_entry()
    pr = _make_provider_result()
    handle = MockExecutionHandle(pr)
    provider.start.return_value = handle

    result = await run_with_retry(
        provider=provider,
        catalog_entry=catalog,
        prompt="Build X",
        system_prompt="You are an agent",
        execution_mode=ExecutionMode.CODING,
        tool_policy=ToolPolicy(mode="unrestricted"),
        output_contract=OutputContract(format="freeform"),
        workspace=WorkspaceRoots(primary_cwd="/tmp/wt", read_only_dirs=[]),
        max_turns=75,
        timeout_seconds=600,
    )
    assert result.success is True
    assert result.provider_model == "claude:sonnet"
    assert result.backend == "claude-code-sdk"
    assert result.model_history_entry is not None
    assert result.model_history_entry["result"] == "success"


async def test_run_with_retry_timeout():
    """run_with_retry returns failure on timeout."""
    provider = MagicMock()
    catalog = _make_catalog_entry()

    class HangingHandle:
        is_running = True

        async def result(self):
            import asyncio

            await asyncio.sleep(999)

        async def abort(self):
            self.is_running = False

    provider.start.return_value = HangingHandle()

    result = await run_with_retry(
        provider=provider,
        catalog_entry=catalog,
        prompt="Build X",
        system_prompt="You are an agent",
        execution_mode=ExecutionMode.CODING,
        tool_policy=ToolPolicy(mode="unrestricted"),
        output_contract=OutputContract(format="freeform"),
        workspace=WorkspaceRoots(primary_cwd="/tmp/wt", read_only_dirs=[]),
        max_turns=75,
        timeout_seconds=0.01,
    )
    assert result.success is False
    assert "timeout" in result.error.lower()


async def test_run_with_retry_provider_error():
    """run_with_retry returns error result when provider reports error."""
    provider = MagicMock()
    catalog = _make_catalog_entry()
    pr = _make_provider_result(is_error=True, text="Something went wrong")
    handle = MockExecutionHandle(pr)
    provider.start.return_value = handle

    result = await run_with_retry(
        provider=provider,
        catalog_entry=catalog,
        prompt="Build X",
        system_prompt="Agent",
        execution_mode=ExecutionMode.CODING,
        tool_policy=ToolPolicy(mode="unrestricted"),
        output_contract=OutputContract(format="freeform"),
        workspace=WorkspaceRoots(primary_cwd="/tmp/wt", read_only_dirs=[]),
        max_turns=75,
        timeout_seconds=600,
    )
    assert result.success is False
    assert result.error == "Something went wrong"


async def test_run_with_retry_transient_retries():
    """run_with_retry retries on transient errors with backoff."""
    provider = MagicMock()
    catalog = _make_catalog_entry()
    pr = _make_provider_result()
    handle = MockExecutionHandle(pr)

    # First call raises transient error, second succeeds
    call_count = [0]

    def mock_start(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("rate_limit exceeded")
        return handle

    provider.start.side_effect = mock_start

    result = await run_with_retry(
        provider=provider,
        catalog_entry=catalog,
        prompt="Build X",
        system_prompt="Agent",
        execution_mode=ExecutionMode.CODING,
        tool_policy=ToolPolicy(mode="unrestricted"),
        output_contract=OutputContract(format="freeform"),
        workspace=WorkspaceRoots(primary_cwd="/tmp/wt", read_only_dirs=[]),
        max_turns=75,
        timeout_seconds=600,
        max_retries=2,
    )
    assert result.success is True
    assert call_count[0] == 2


async def test_run_with_retry_resume_state_populated():
    """run_with_retry populates resume_state and session_id from ProviderResult."""
    provider = MagicMock()
    catalog = _make_catalog_entry()
    rs = ResumeState(
        provider="claude",
        backend="claude-code-sdk",
        session_token="sess-123",
        created_at="2026-01-01T00:00:00Z",
        last_active_at="2026-01-01T00:01:00Z",
        turn_count=5,
        is_resumable=True,
    )
    pr = _make_provider_result(resume_state=rs)
    handle = MockExecutionHandle(pr)
    provider.start.return_value = handle

    result = await run_with_retry(
        provider=provider,
        catalog_entry=catalog,
        prompt="Build X",
        system_prompt="Agent",
        execution_mode=ExecutionMode.CODING,
        tool_policy=ToolPolicy(mode="unrestricted"),
        output_contract=OutputContract(format="freeform"),
        workspace=WorkspaceRoots(primary_cwd="/tmp/wt", read_only_dirs=[]),
        max_turns=75,
        timeout_seconds=600,
    )
    assert result.session_id == "sess-123"
    assert result.resume_state is rs


async def test_run_with_retry_preserves_forge_question_text():
    """Provider-backed runs must preserve FORGE_QUESTION payloads for daemon pause/resume."""
    provider = MagicMock()
    catalog = _make_catalog_entry(provider="openai", alias="gpt-5.4-mini", backend="codex-sdk")
    question_text = (
        'FORGE_QUESTION:\n{"question":"Use pytest fixture or helper?","suggestions":["fixture","helper"]}'
    )
    pr = _make_provider_result(text=question_text, model_canonical_id="gpt-5.4-mini-0414")
    provider.start.return_value = MockExecutionHandle(pr)

    result = await run_with_retry(
        provider=provider,
        catalog_entry=catalog,
        prompt="Build X",
        system_prompt="Agent",
        execution_mode=ExecutionMode.CODING,
        tool_policy=ToolPolicy(mode="unrestricted"),
        output_contract=OutputContract(format="freeform"),
        workspace=WorkspaceRoots(primary_cwd="/tmp/wt", read_only_dirs=[]),
        max_turns=75,
        timeout_seconds=600,
    )

    assert result.success is True
    assert "FORGE_QUESTION:" in result.summary
