"""Tests for OpenAI provider — all mocked, no real API calls."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge.providers.base import (
    CatalogEntry,
    EventKind,
    ExecutionMode,
    OutputContract,
    ProviderEvent,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.catalog import CODEX_TOOL_MAP, CoreTool
from forge.providers.openai import (
    OpenAIProvider,
    _AgentsExecutionHandle,
    _CodexExecutionHandle,
    _convert_agents_event,
    _convert_codex_event,
    _normalize_codex_tool,
    _translate_denied_to_instructions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def provider() -> OpenAIProvider:
    return OpenAIProvider()


@pytest.fixture()
def codex_entry() -> CatalogEntry:
    return CatalogEntry(
        provider="openai",
        alias="gpt-5.4",
        canonical_id="gpt-5.4-0414",
        backend="codex-sdk",
        tier="supported",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=128_000,
        supports_structured_output=True,
        supports_reasoning=False,
        cost_key="openai:gpt-5.4",
        validated_stages=frozenset(["agent", "ci_fix"]),
    )


@pytest.fixture()
def agents_entry() -> CatalogEntry:
    return CatalogEntry(
        provider="openai",
        alias="o3",
        canonical_id="o3-2025-04-16",
        backend="openai-agents-sdk",
        tier="experimental",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=False,
        can_edit_files=False,
        supports_mcp_servers=False,
        max_context_tokens=200_000,
        supports_structured_output=True,
        supports_reasoning=True,
        cost_key="openai:o3",
        validated_stages=frozenset(["planner", "reviewer"]),
    )


@pytest.fixture()
def workspace() -> WorkspaceRoots:
    return WorkspaceRoots(primary_cwd="/tmp/test-workspace")


@pytest.fixture()
def tool_policy() -> ToolPolicy:
    return ToolPolicy(mode="unrestricted")


@pytest.fixture()
def output_contract() -> OutputContract:
    return OutputContract(format="freeform")


# ---------------------------------------------------------------------------
# Provider basics
# ---------------------------------------------------------------------------


class TestOpenAIProviderBasics:
    def test_name(self, provider: OpenAIProvider) -> None:
        assert provider.name == "openai"

    def test_catalog_entries_returns_openai_only(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "forge.providers.openai._available_codex_model_aliases",
                return_value={"gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"},
            ),
        ):
            entries = provider.catalog_entries()
        assert len(entries) == 3
        for entry in entries:
            assert entry.provider == "openai"

    def test_catalog_entries_include_expected_models(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}),
            patch(
                "forge.providers.openai._available_codex_model_aliases",
                return_value={"gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"},
            ),
        ):
            aliases = {e.alias for e in provider.catalog_entries()}
        assert aliases == {"gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "o3"}

    def test_catalog_entries_hide_agents_model_without_api_key(
        self, provider: OpenAIProvider
    ) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "forge.providers.openai._available_codex_model_aliases",
                return_value={"gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"},
            ),
        ):
            aliases = {e.alias for e in provider.catalog_entries()}
        assert "o3" not in aliases

    def test_catalog_entries_filter_to_subscription_models(
        self, provider: OpenAIProvider
    ) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "forge.providers.openai._available_codex_model_aliases",
                return_value={"gpt-5.4", "gpt-5.4-mini"},
            ),
        ):
            aliases = {e.alias for e in provider.catalog_entries()}
        assert aliases == {"gpt-5.4", "gpt-5.4-mini"}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_healthy_when_all_available(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}),
            patch(
                "forge.providers.openai._try_import_codex",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch(
                "forge.providers.openai._try_import_agents",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch(
                "forge.providers.openai._codex_auth_description",
                return_value="Codex ChatGPT subscription login configured",
            ),
            patch(
                "forge.providers.openai._available_codex_model_aliases",
                return_value={"gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"},
            ),
        ):
            status = provider.health_check()
            assert status.healthy is True
            assert status.provider == "openai"
            assert not status.errors

    def test_healthy_codex_backend_with_chatgpt_auth(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "forge.providers.openai._try_import_codex",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch(
                "forge.providers.openai._codex_auth_description",
                return_value="Codex ChatGPT subscription login configured",
            ),
        ):
            status = provider.health_check(backend="codex-sdk")
            assert status.healthy is True
            assert not status.errors

    def test_unhealthy_codex_backend_when_no_auth(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "forge.providers.openai._try_import_codex",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch("forge.providers.openai._codex_auth_description", return_value=None),
        ):
            status = provider.health_check(backend="codex-sdk")
            assert status.healthy is False
            assert any("codex login" in e.lower() or "codex_api_key" in e.lower() for e in status.errors)

    def test_unhealthy_when_codex_not_installed(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("forge.providers.openai._try_import_codex", return_value=None),
            patch(
                "forge.providers.openai._codex_auth_description",
                return_value="Codex ChatGPT subscription login configured",
            ),
        ):
            status = provider.health_check(backend="codex-sdk")
            assert status.healthy is False
            assert any("codex" in e.lower() for e in status.errors)

    def test_unhealthy_when_agents_not_installed(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}),
            patch(
                "forge.providers.openai._try_import_codex",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch("forge.providers.openai._try_import_agents", return_value=None),
        ):
            status = provider.health_check()
            assert status.healthy is False
            assert any("agents" in e.lower() for e in status.errors)

    def test_backend_specific_check_codex(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "forge.providers.openai._try_import_codex",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch(
                "forge.providers.openai._codex_auth_description",
                return_value="Codex ChatGPT subscription login configured",
            ),
        ):
            status = provider.health_check(backend="codex-sdk")
            assert status.healthy is True
            # Should not check agents-sdk
            assert not any("agents" in e.lower() for e in status.errors)

    def test_backend_specific_check_codex_with_openai_api_key_fallback(
        self, provider: OpenAIProvider
    ) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True),
            patch(
                "forge.providers.openai._try_import_codex",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
            patch("forge.providers.openai._codex_auth_description", return_value=None),
        ):
            status = provider.health_check(backend="codex-sdk")
            assert status.healthy is True
            assert "fallback" in status.details.lower()

    def test_backend_specific_check_agents(self, provider: OpenAIProvider) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}),
            patch(
                "forge.providers.openai._try_import_agents",
                return_value=SimpleNamespace(__version__="0.1.0"),
            ),
        ):
            status = provider.health_check(backend="openai-agents-sdk")
            assert status.healthy is True


# ---------------------------------------------------------------------------
# Tool name normalization
# ---------------------------------------------------------------------------


class TestToolNormalization:
    def test_codex_tool_map_entries(self) -> None:
        assert CODEX_TOOL_MAP["command_execution"] == CoreTool.BASH
        assert CODEX_TOOL_MAP["file_read"] == CoreTool.READ
        assert CODEX_TOOL_MAP["file_write"] == CoreTool.WRITE
        assert CODEX_TOOL_MAP["file_change"] == CoreTool.EDIT
        assert CODEX_TOOL_MAP["glob"] == CoreTool.GLOB
        assert CODEX_TOOL_MAP["grep"] == CoreTool.GREP

    def test_normalize_known_tool(self) -> None:
        assert _normalize_codex_tool("command_execution") == "bash"
        assert _normalize_codex_tool("file_read") == "read"

    def test_normalize_unknown_tool_passthrough(self) -> None:
        assert _normalize_codex_tool("custom_tool") == "custom_tool"


# ---------------------------------------------------------------------------
# Codex event conversion
# ---------------------------------------------------------------------------


class TestCodexEventConversion:
    def test_text_event(self) -> None:
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "content": "Hello world", "id": "1"},
        }
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.TEXT
        assert result.text == "Hello world"

    def test_tool_use_event(self) -> None:
        event = {
            "type": "item.started",
            "item": {"type": "command_execution", "content": "ls -la", "id": "cmd-1"},
        }
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.TOOL_USE
        assert result.tool_name == "bash"
        assert result.tool_call_id == "cmd-1"

    def test_tool_result_event(self) -> None:
        event = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "content": {"output": "file1.py\nfile2.py"},
                "id": "cmd-1",
            },
        }
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.TOOL_RESULT
        assert result.tool_name == "bash"
        assert "file1.py" in result.tool_output

    def test_turn_completed_with_usage(self) -> None:
        event = {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.USAGE
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_turn_completed_without_usage(self) -> None:
        event = {"type": "turn.completed"}
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.STATUS
        assert result.status == "completed"

    def test_error_event(self) -> None:
        event = {"type": "error", "message": "Rate limit exceeded"}
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.ERROR
        assert "Rate limit" in result.text

    def test_turn_failed_event(self) -> None:
        event = {"type": "turn.failed", "message": "Internal error"}
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.ERROR

    def test_unknown_event_returns_none(self) -> None:
        event = {"type": "some.unknown.event"}
        assert _convert_codex_event(event) is None

    def test_none_type_returns_none(self) -> None:
        assert _convert_codex_event({}) is None

    def test_attribute_style_event(self) -> None:
        event = SimpleNamespace(
            type="item.completed",
            item=SimpleNamespace(type="agent_message", content="test", id="1"),
        )
        result = _convert_codex_event(event)
        assert result is not None
        assert result.kind == EventKind.TEXT
        assert result.text == "test"


# ---------------------------------------------------------------------------
# Agents SDK event conversion
# ---------------------------------------------------------------------------


class TestAgentsEventConversion:
    def test_text_delta_event(self) -> None:
        event = {"type": "response.text.delta", "delta": "Hello"}
        result = _convert_agents_event(event)
        assert result is not None
        assert result.kind == EventKind.TEXT
        assert result.text == "Hello"

    def test_response_completed_with_usage(self) -> None:
        event = {
            "type": "response.completed",
            "usage": {"input_tokens": 200, "output_tokens": 80},
        }
        result = _convert_agents_event(event)
        assert result is not None
        assert result.kind == EventKind.USAGE
        assert result.input_tokens == 200

    def test_response_completed_without_usage(self) -> None:
        event = {"type": "response.completed"}
        result = _convert_agents_event(event)
        assert result is not None
        assert result.kind == EventKind.STATUS
        assert result.status == "completed"

    def test_error_event(self) -> None:
        event = {"type": "error", "message": "API error"}
        result = _convert_agents_event(event)
        assert result is not None
        assert result.kind == EventKind.ERROR

    def test_response_failed_event(self) -> None:
        event = {"type": "response.failed", "message": "Failed"}
        result = _convert_agents_event(event)
        assert result is not None
        assert result.kind == EventKind.ERROR

    def test_unknown_event_returns_none(self) -> None:
        assert _convert_agents_event({"type": "unknown"}) is None


# ---------------------------------------------------------------------------
# Safety translation
# ---------------------------------------------------------------------------


class TestSafetyTranslation:
    def test_translate_git_operations(self) -> None:
        result = _translate_denied_to_instructions(["git:push", "git:rebase"])
        assert "SAFETY RESTRICTIONS" in result
        assert "git push" in result
        assert "git rebase" in result

    def test_translate_empty_list(self) -> None:
        assert _translate_denied_to_instructions([]) == ""

    def test_translate_unknown_operations_ignored(self) -> None:
        result = _translate_denied_to_instructions(["unknown:operation"])
        assert result == ""


# ---------------------------------------------------------------------------
# Codex start() mock
# ---------------------------------------------------------------------------


class TestCodexStart:
    @pytest.fixture()
    def _mock_codex_sdk(self) -> Any:
        """Create a mock Codex SDK module."""

        async def _fake_stream(**kwargs: Any) -> Any:
            events = [
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "content": "Done", "id": "1"},
                },
                {"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 25}},
            ]
            for e in events:
                yield e

        mock_sdk = MagicMock()
        mock_sdk.__version__ = "0.1.0"
        mock_sdk.start_thread = MagicMock(return_value=_fake_stream())
        mock_sdk.startThread = None
        return mock_sdk

    async def test_codex_start_produces_events(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        _mock_codex_sdk: Any,
    ) -> None:
        events: list[ProviderEvent] = []

        with patch("forge.providers.openai._try_import_codex", return_value=_mock_codex_sdk):
            handle = provider.start(
                prompt="test prompt",
                system_prompt="you are helpful",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=5,
                on_event=events.append,
            )
            assert isinstance(handle, _CodexExecutionHandle)
            result = await handle.result()

        assert result.is_error is False
        assert result.text == "Done"
        assert result.input_tokens == 50
        assert result.output_tokens == 25
        assert result.provider_reported_cost_usd is None
        assert result.model_canonical_id == "gpt-5.4-0414"

        # Should have STATUS started, TEXT, USAGE, STATUS completed
        kinds = [e.kind for e in events]
        assert EventKind.STATUS in kinds
        assert EventKind.TEXT in kinds
        assert EventKind.USAGE in kinds

    async def test_codex_start_with_denylist(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        output_contract: OutputContract,
        _mock_codex_sdk: Any,
    ) -> None:
        denylist_policy = ToolPolicy(
            mode="denylist",
            denied_operations=["git:push", "net:curl"],
        )

        captured_kwargs: dict[str, Any] = {}

        async def _capturing_stream(**kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            async for event in []:
                yield event  # pragma: no cover
            yield {"type": "turn.completed"}

        _mock_codex_sdk.start_thread = MagicMock(side_effect=_capturing_stream)

        with patch("forge.providers.openai._try_import_codex", return_value=_mock_codex_sdk):
            handle = provider.start(
                prompt="test",
                system_prompt="base",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=denylist_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
            )
            await handle.result()

        # Safety instructions should be appended to instructions
        assert captured_kwargs.get("model") == "gpt-5.4"
        instructions = captured_kwargs.get("instructions", "")
        assert "git push" in instructions
        assert "curl" in instructions

    async def test_codex_start_error_handling(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        events: list[ProviderEvent] = []
        mock_sdk = MagicMock()
        mock_sdk.start_thread = MagicMock(side_effect=RuntimeError("Connection failed"))
        mock_sdk.startThread = None

        with patch("forge.providers.openai._try_import_codex", return_value=mock_sdk):
            handle = provider.start(
                prompt="test",
                system_prompt="test",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
                on_event=events.append,
            )
            result = await handle.result()

        assert result.is_error is True
        assert "Connection failed" in result.text
        error_events = [e for e in events if e.kind == EventKind.ERROR]
        assert len(error_events) >= 1

    async def test_codex_client_start_path_produces_events(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        events: list[ProviderEvent] = []
        captured: dict[str, Any] = {}

        async def _event_stream() -> Any:
            yield SimpleNamespace(
                type="thread.started",
                thread_id="thread-123",
            )
            yield SimpleNamespace(
                type="item.completed",
                item=SimpleNamespace(
                    type="agent_message",
                    text="Done from client",
                    id="msg-1",
                ),
            )
            yield SimpleNamespace(
                type="turn.completed",
                usage=SimpleNamespace(input_tokens=60, output_tokens=30),
            )

        class _FakeThread:
            def __init__(self) -> None:
                self.id = None

            async def run_streamed(self, input_: Any) -> Any:
                captured["input"] = input_
                self.id = "thread-123"
                return SimpleNamespace(events=_event_stream())

        class _FakeCodexClient:
            def __init__(self, options: Any = None) -> None:
                captured["client_options"] = options

            def start_thread(self, options: Any = None) -> Any:
                captured["thread_options"] = options
                return _FakeThread()

        mock_sdk = MagicMock()
        mock_sdk.__version__ = "0.1.0"
        mock_sdk.start_thread = None
        mock_sdk.startThread = None
        mock_sdk.resume_thread = None
        mock_sdk.resumeThread = None
        mock_sdk.Codex = _FakeCodexClient

        with (
            patch("forge.providers.openai._try_import_codex", return_value=mock_sdk),
            patch(
                "forge.providers.openai._codex_auth_description",
                return_value="Codex ChatGPT subscription login configured",
            ),
        ):
            handle = provider.start(
                prompt="test prompt",
                system_prompt="system rules",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=5,
                on_event=events.append,
            )
            result = await handle.result()

        assert result.is_error is False
        assert result.text == "Done from client"
        assert result.input_tokens == 60
        assert result.output_tokens == 30
        assert result.resume_state is not None
        assert result.resume_state.session_token == "thread-123"
        assert captured["client_options"] is None
        assert captured["thread_options"]["model"] == "gpt-5.4"
        assert captured["thread_options"]["workingDirectory"] == workspace.primary_cwd
        assert "system rules" in captured["input"]
        assert "test prompt" in captured["input"]

    async def test_codex_client_uses_api_key_when_cli_auth_missing(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _event_stream() -> Any:
            yield {"type": "turn.completed"}

        class _FakeThread:
            async def run_streamed(self, input_: Any) -> Any:
                return SimpleNamespace(events=_event_stream())

        class _FakeCodexClient:
            def __init__(self, options: Any = None) -> None:
                captured["client_options"] = options

            def start_thread(self, options: Any = None) -> Any:
                return _FakeThread()

        mock_sdk = MagicMock()
        mock_sdk.__version__ = "0.1.0"
        mock_sdk.start_thread = None
        mock_sdk.startThread = None
        mock_sdk.resume_thread = None
        mock_sdk.resumeThread = None
        mock_sdk.Codex = _FakeCodexClient

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}, clear=True),
            patch("forge.providers.openai._try_import_codex", return_value=mock_sdk),
            patch("forge.providers.openai._codex_auth_description", return_value=None),
        ):
            handle = provider.start(
                prompt="test prompt",
                system_prompt="system rules",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=5,
            )
            result = await handle.result()

        assert result.is_error is False
        assert captured["client_options"] == {"apiKey": "sk-test-key"}

    async def test_codex_not_installed_raises(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        with patch("forge.providers.openai._try_import_codex", return_value=None):
            handle = provider.start(
                prompt="test",
                system_prompt="test",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
            )
            result = await handle.result()
            assert result.is_error is True
            assert "not installed" in result.text


# ---------------------------------------------------------------------------
# Agents start() mock
# ---------------------------------------------------------------------------


class TestAgentsStart:
    async def test_agents_start_produces_events(
        self,
        provider: OpenAIProvider,
        agents_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        events: list[ProviderEvent] = []

        async def _fake_stream(*args: Any, **kwargs: Any) -> Any:
            stream_events = [
                {"type": "response.text.delta", "delta": "Analysis complete"},
                {"type": "response.completed", "usage": {"input_tokens": 100, "output_tokens": 40}},
            ]
            for e in stream_events:
                yield e

        mock_agents = MagicMock()
        mock_agents.__version__ = "0.1.0"
        mock_agents.Agent = MagicMock(return_value=MagicMock())
        mock_agents.Runner = MagicMock()
        mock_agents.Runner.run_streamed = MagicMock(return_value=_fake_stream())

        with patch("forge.providers.openai._try_import_agents", return_value=mock_agents):
            handle = provider.start(
                prompt="analyze this",
                system_prompt="you are a reviewer",
                catalog_entry=agents_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
                on_event=events.append,
            )
            assert isinstance(handle, _AgentsExecutionHandle)
            result = await handle.result()

        assert result.is_error is False
        assert result.text == "Analysis complete"
        assert result.input_tokens == 100
        assert result.output_tokens == 40
        assert result.resume_state is None  # Agents SDK doesn't support resume

    async def test_agents_not_installed_raises(
        self,
        provider: OpenAIProvider,
        agents_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        with patch("forge.providers.openai._try_import_agents", return_value=None):
            handle = provider.start(
                prompt="test",
                system_prompt="test",
                catalog_entry=agents_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
            )
            result = await handle.result()
            assert result.is_error is True
            assert "not installed" in result.text


# ---------------------------------------------------------------------------
# Resume / cleanup
# ---------------------------------------------------------------------------


class TestResumeAndCleanup:
    def test_can_resume_valid_state(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="codex-sdk",
            session_token="thread-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        assert provider.can_resume(state) is True

    def test_can_resume_wrong_provider(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="claude",
            backend="codex-sdk",
            session_token="thread-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        assert provider.can_resume(state) is False

    def test_can_resume_wrong_backend(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="openai-agents-sdk",
            session_token="thread-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        assert provider.can_resume(state) is False

    def test_can_resume_not_resumable(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="codex-sdk",
            session_token="thread-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=False,
        )
        assert provider.can_resume(state) is False

    def test_can_resume_empty_token(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="codex-sdk",
            session_token="",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        assert provider.can_resume(state) is False

    def test_cleanup_session_codex(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="codex-sdk",
            session_token="thread-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        mock_sdk = MagicMock()
        mock_sdk.delete_thread = MagicMock()
        mock_sdk.deleteThread = None
        with patch("forge.providers.openai._try_import_codex", return_value=mock_sdk):
            provider.cleanup_session(state)
        mock_sdk.delete_thread.assert_called_once_with("thread-abc")

    def test_cleanup_session_agents_noop(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="openai-agents-sdk",
            session_token="sess-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        # Should not raise
        provider.cleanup_session(state)

    def test_cleanup_session_no_sdk(self, provider: OpenAIProvider) -> None:
        state = ResumeState(
            provider="openai",
            backend="codex-sdk",
            session_token="thread-abc",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:01:00",
            turn_count=5,
            is_resumable=True,
        )
        with patch("forge.providers.openai._try_import_codex", return_value=None):
            # Should not raise even when SDK unavailable
            provider.cleanup_session(state)


# ---------------------------------------------------------------------------
# Abort flow
# ---------------------------------------------------------------------------


class TestAbortFlow:
    async def test_codex_abort(
        self,
        provider: OpenAIProvider,
        codex_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        """Test that abort cancels a running Codex execution."""
        started = asyncio.Event()

        async def _slow_stream(**kwargs: Any) -> Any:
            started.set()
            await asyncio.sleep(10)
            yield {"type": "turn.completed"}  # pragma: no cover

        mock_sdk = MagicMock()
        mock_sdk.start_thread = MagicMock(return_value=_slow_stream())
        mock_sdk.startThread = None

        with patch("forge.providers.openai._try_import_codex", return_value=mock_sdk):
            handle = provider.start(
                prompt="test",
                system_prompt="test",
                catalog_entry=codex_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
            )
            assert handle.is_running
            # Give the task a moment to start
            await asyncio.sleep(0.01)
            await handle.abort()
            assert not handle.is_running

    async def test_agents_abort(
        self,
        provider: OpenAIProvider,
        agents_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        """Test that abort cancels a running Agents execution."""

        async def _slow_stream(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(10)
            yield {"type": "response.completed"}  # pragma: no cover

        mock_agents = MagicMock()
        mock_agents.Agent = MagicMock(return_value=MagicMock())
        mock_agents.Runner = MagicMock()
        mock_agents.Runner.run_streamed = MagicMock(return_value=_slow_stream())

        with patch("forge.providers.openai._try_import_agents", return_value=mock_agents):
            handle = provider.start(
                prompt="test",
                system_prompt="test",
                catalog_entry=agents_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
            )
            await asyncio.sleep(0.01)
            await handle.abort()
            assert not handle.is_running


# ---------------------------------------------------------------------------
# Unknown backend
# ---------------------------------------------------------------------------


class TestUnknownBackend:
    def test_unknown_backend_raises(
        self,
        provider: OpenAIProvider,
        workspace: WorkspaceRoots,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
    ) -> None:
        bad_entry = CatalogEntry(
            provider="openai",
            alias="test",
            canonical_id="test-model",
            backend="unknown-sdk",
            tier="experimental",
            can_use_tools=False,
            can_stream=False,
            can_resume_session=False,
            can_run_shell=False,
            can_edit_files=False,
            supports_mcp_servers=False,
            max_context_tokens=1000,
            supports_structured_output=False,
            supports_reasoning=False,
            cost_key="openai:test",
            validated_stages=frozenset(),
        )
        with pytest.raises(ValueError, match="Unknown backend"):
            provider.start(
                prompt="test",
                system_prompt="test",
                catalog_entry=bad_entry,
                execution_mode=ExecutionMode.CODING,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=3,
            )
