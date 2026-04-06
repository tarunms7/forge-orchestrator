"""Tests for ClaudeProvider — health check, event conversion, tool policy, resume."""

import asyncio
from unittest.mock import MagicMock, patch

from forge.providers.base import (
    CatalogEntry,
    EventKind,
    ProviderEvent,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.claude import (
    ClaudeProvider,
    _ClaudeExecutionHandle,
    _convert_assistant_message,
    _convert_result_message,
    _convert_stream_event,
    _drain_stream_text,
    _normalize_tool_name,
    _translate_denied_operations,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_catalog_entry(**overrides) -> CatalogEntry:
    defaults = dict(
        provider="claude",
        alias="sonnet",
        canonical_id="claude-sonnet-4-test",
        backend="claude-code-sdk",
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=200_000,
        supports_structured_output=False,
        supports_reasoning=True,
        cost_key="claude:sonnet",
        validated_stages=frozenset(["agent", "planner"]),
    )
    defaults.update(overrides)
    return CatalogEntry(**defaults)


def _make_result_message(**overrides):
    """Create a mock ResultMessage."""
    from claude_code_sdk import ResultMessage

    defaults = dict(
        subtype="success",
        duration_ms=500,
        duration_api_ms=400,
        is_error=False,
        num_turns=3,
        session_id="test-session-123",
        total_cost_usd=0.05,
        result="Task completed successfully",
        usage={"input_tokens": 1000, "output_tokens": 500},
    )
    defaults.update(overrides)
    return ResultMessage(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaudeProviderProperties:
    def test_name(self):
        p = ClaudeProvider()
        assert p.name == "claude"

    def test_catalog_entries_returns_claude_only(self):
        p = ClaudeProvider()
        entries = p.catalog_entries()
        assert len(entries) >= 3  # sonnet, opus, haiku
        assert all(e.provider == "claude" for e in entries)

    def test_catalog_entries_include_sonnet(self):
        p = ClaudeProvider()
        aliases = {e.alias for e in p.catalog_entries()}
        assert "sonnet" in aliases
        assert "opus" in aliases
        assert "haiku" in aliases


class TestHealthCheck:
    def test_healthy_when_sdk_available(self):
        p = ClaudeProvider()
        import os

        os.environ.pop("CLAUDECODE", None)  # Ensure clean env
        status = p.health_check()
        assert status.healthy is True
        assert status.provider == "claude"
        assert "claude-code-sdk" in status.details

    def test_unhealthy_when_claudecode_env_set(self):
        p = ClaudeProvider()
        import os

        os.environ["CLAUDECODE"] = "1"
        try:
            status = p.health_check()
            assert status.healthy is False
            assert any("CLAUDECODE" in e for e in status.errors)
        finally:
            os.environ.pop("CLAUDECODE", None)


class TestToolPolicyTranslation:
    def test_translate_git_operations(self):
        result = _translate_denied_operations(["git:push", "git:merge"])
        assert "Bash(git push)" in result
        assert "Bash(git push *)" in result
        assert "Bash(git merge)" in result
        assert "Bash(git merge *)" in result

    def test_translate_network_operations(self):
        result = _translate_denied_operations(["net:curl", "net:ssh"])
        assert "Bash(curl *)" in result
        assert "Bash(ssh *)" in result

    def test_translate_file_read_dotenv(self):
        result = _translate_denied_operations(["file:read_dotenv"])
        assert "Read(.env)" in result
        assert "Read(.env.*)" in result

    def test_translate_unknown_passthrough(self):
        result = _translate_denied_operations(["custom:something"])
        assert "custom:something" in result

    def test_translate_empty(self):
        assert _translate_denied_operations([]) == []


class TestToolNameNormalization:
    def test_known_tools(self):
        assert _normalize_tool_name("Bash") == "bash"
        assert _normalize_tool_name("Read") == "read"
        assert _normalize_tool_name("Write") == "write"
        assert _normalize_tool_name("Edit") == "edit"

    def test_unknown_tool_passthrough(self):
        assert _normalize_tool_name("CustomTool") == "CustomTool"


class TestEventConversion:
    def test_drain_stream_text_splits_completed_sentences(self):
        fragments, remaining = _drain_stream_text(
            "I am reading the current tests. Now I have enough context.",
        )
        assert fragments == [
            "I am reading the current tests.",
            "Now I have enough context.",
        ]
        assert remaining == ""

    def test_drain_stream_text_keeps_incomplete_tail_until_forced(self):
        fragments, remaining = _drain_stream_text("Still gathering context")
        assert fragments == []
        assert remaining == "Still gathering context"

        forced, remaining = _drain_stream_text(remaining, force=True)
        assert forced == ["Still gathering context"]
        assert remaining == ""

    def test_convert_text_block(self):
        msg = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello world"
        msg.content = [text_block]

        events = _convert_assistant_message(msg)
        assert len(events) == 1
        assert events[0].kind == EventKind.TEXT
        assert events[0].text == "Hello world"

    def test_convert_tool_use_block(self):
        msg = MagicMock()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "Bash"
        tool_block.input = {"command": "ls"}
        tool_block.id = "call-123"
        msg.content = [tool_block]

        events = _convert_assistant_message(msg)
        assert len(events) == 1
        assert events[0].kind == EventKind.TOOL_USE
        assert events[0].tool_name == "bash"  # normalized
        assert events[0].tool_call_id == "call-123"
        assert '"command"' in events[0].tool_input

    def test_convert_tool_result_block(self):
        msg = MagicMock()
        result_block = MagicMock()
        result_block.type = "tool_result"
        result_block.tool_use_id = "call-123"
        result_block.content = "output text"
        result_block.is_error = False
        msg.content = [result_block]

        events = _convert_assistant_message(msg)
        assert len(events) == 1
        assert events[0].kind == EventKind.TOOL_RESULT
        assert events[0].tool_output == "output text"

    def test_convert_thinking_block(self):
        msg = MagicMock()
        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        msg.content = [thinking_block]

        events = _convert_assistant_message(msg)
        assert len(events) == 1
        assert events[0].kind == EventKind.STATUS
        assert events[0].status == "thinking"

    def test_convert_stream_event_tool_use(self):
        from claude_code_sdk.types import StreamEvent

        event = StreamEvent(
            uuid="evt-1",
            session_id="sess-1",
            event={
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "Read",
                    "input": {"file_path": "forge/tui/app.py"},
                },
            },
        )

        events = _convert_stream_event(event)
        assert len(events) == 1
        assert events[0].kind == EventKind.TOOL_USE
        assert events[0].tool_name == "read"
        assert events[0].tool_call_id == "tool-1"
        assert "file_path" in (events[0].tool_input or "")

    def test_convert_stream_event_tool_use_without_input_is_deferred(self):
        from claude_code_sdk.types import StreamEvent

        event = StreamEvent(
            uuid="evt-1b",
            session_id="sess-1",
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "Read",
                    "input": {},
                },
            },
        )

        assert _convert_stream_event(event) == []

    def test_convert_stream_event_text_delta_emits_typing(self):
        from claude_code_sdk.types import StreamEvent

        event = StreamEvent(
            uuid="evt-2",
            session_id="sess-1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        )

        events = _convert_stream_event(event)
        assert len(events) == 1
        assert events[0].kind == EventKind.STATUS
        assert events[0].status == "typing"

    def test_convert_empty_content(self):
        msg = MagicMock()
        msg.content = None
        assert _convert_assistant_message(msg) == []

    def test_convert_result_message(self):
        rm = _make_result_message()
        events, result = _convert_result_message(rm)

        # Should have TEXT, USAGE, STATUS events
        kinds = {e.kind for e in events}
        assert EventKind.TEXT in kinds
        assert EventKind.USAGE in kinds
        assert EventKind.STATUS in kinds

        # Check ProviderResult
        assert result.text == "Task completed successfully"
        assert result.is_error is False
        assert result.input_tokens == 1000
        assert result.output_tokens == 500
        assert result.duration_ms == 500
        assert result.provider_reported_cost_usd == 0.05

    def test_convert_result_message_creates_resume_state(self):
        rm = _make_result_message(session_id="sess-abc")
        _, result = _convert_result_message(rm)

        assert result.resume_state is not None
        assert result.resume_state.provider == "claude"
        assert result.resume_state.session_token == "sess-abc"
        assert result.resume_state.is_resumable is True

    def test_convert_result_message_no_session_id(self):
        rm = _make_result_message(session_id=None)
        _, result = _convert_result_message(rm)
        assert result.resume_state is None


class TestResumeState:
    def test_can_resume_valid(self):
        p = ClaudeProvider()
        state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token="sess-123",
            created_at="2026-01-01T00:00:00",
            last_active_at="2026-01-01T00:00:00",
            turn_count=5,
            is_resumable=True,
        )
        assert p.can_resume(state) is True

    def test_can_resume_wrong_provider(self):
        p = ClaudeProvider()
        state = ResumeState(
            provider="openai",
            backend="codex-sdk",
            session_token="sess-123",
            created_at="",
            last_active_at="",
            turn_count=0,
            is_resumable=True,
        )
        assert p.can_resume(state) is False

    def test_can_resume_not_resumable(self):
        p = ClaudeProvider()
        state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token="sess-123",
            created_at="",
            last_active_at="",
            turn_count=0,
            is_resumable=False,
        )
        assert p.can_resume(state) is False

    def test_can_resume_empty_token(self):
        p = ClaudeProvider()
        state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token="",
            created_at="",
            last_active_at="",
            turn_count=0,
            is_resumable=True,
        )
        assert p.can_resume(state) is False

    def test_cleanup_session_noop(self):
        p = ClaudeProvider()
        state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token="sess-123",
            created_at="",
            last_active_at="",
            turn_count=0,
            is_resumable=True,
        )
        # Should not raise
        p.cleanup_session(state)


class TestExecutionHandle:
    async def test_abort_cancels_task(self):
        async def slow_task():
            await asyncio.sleep(100)

        task = asyncio.ensure_future(slow_task())
        handle = _ClaudeExecutionHandle(task, _make_catalog_entry())

        assert handle.is_running is True
        await handle.abort()
        assert handle.is_running is False

    async def test_result_returns_provider_result(self):
        from forge.providers.base import ProviderResult

        expected = ProviderResult(
            text="done",
            is_error=False,
            input_tokens=100,
            output_tokens=50,
            resume_state=None,
            duration_ms=200,
            provider_reported_cost_usd=0.01,
            model_canonical_id="test",
        )

        async def instant_task():
            return expected

        task = asyncio.ensure_future(instant_task())
        handle = _ClaudeExecutionHandle(task, _make_catalog_entry())
        result = await handle.result()
        assert result is expected
        assert handle.is_running is False


class TestBuildOptions:
    def test_denylist_mode_translates_operations(self):
        policy = ToolPolicy(
            mode="denylist",
            denied_operations=["git:push", "net:curl"],
        )
        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=policy,
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
        )
        assert hasattr(options, "disallowed_tools")
        assert "Bash(git push)" in options.disallowed_tools
        assert "Bash(curl *)" in options.disallowed_tools

    def test_allowlist_mode_sets_allowed_tools(self):
        policy = ToolPolicy(
            mode="allowlist",
            allowed_tools=["Read", "Grep"],
        )
        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=policy,
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
        )
        assert hasattr(options, "allowed_tools")
        assert "Read" in options.allowed_tools
        assert "Grep" in options.allowed_tools

    def test_resume_state_sets_resume(self):
        state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token="sess-abc",
            created_at="",
            last_active_at="",
            turn_count=5,
            is_resumable=True,
        )
        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=ToolPolicy(mode="unrestricted"),
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
            resume_state=state,
        )
        assert options.resume == "sess-abc"

    def test_reasoning_effort_appends_guidance(self):
        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=ToolPolicy(mode="unrestricted"),
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
            reasoning_effort="high",
        )
        assert options.append_system_prompt is not None
        assert "Reasoning effort: high" in options.append_system_prompt
        assert "test" in options.append_system_prompt

    def test_partial_messages_enabled_for_live_streaming(self):
        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=ToolPolicy(mode="unrestricted"),
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
        )
        assert options.include_partial_messages is True


class TestRunQueryStreaming:
    async def test_run_query_emits_readable_partial_text_without_duplicate_result_text(self):
        from claude_code_sdk.types import StreamEvent

        events = []

        async def _fake_query(*, prompt, options):
            yield StreamEvent(
                uuid="evt-1",
                session_id="sess-1",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "I am reading the current tests. "},
                },
            )
            yield StreamEvent(
                uuid="evt-2",
                session_id="sess-1",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Now I have enough context."},
                },
            )
            yield _make_result_message(result="Now I have enough context.")

        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=ToolPolicy(mode="unrestricted"),
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
        )

        with patch("forge.providers.claude.query", side_effect=_fake_query):
            result = await ClaudeProvider._run_query(
                prompt="test prompt",
                options=options,
                catalog_entry=_make_catalog_entry(),
                on_event=events.append,
            )

        text_events = [event.text for event in events if event.kind == EventKind.TEXT]
        assert text_events == [
            "I am reading the current tests.",
            "Now I have enough context.",
        ]
        assert result.text == "Now I have enough context."

    async def test_run_query_emits_tool_details_from_input_json_delta(self):
        from claude_code_sdk.types import StreamEvent

        events: list[ProviderEvent] = []

        async def _fake_query(*, prompt, options):
            yield StreamEvent(
                uuid="evt-tool-start",
                session_id="sess-1",
                event={
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Read",
                        "input": {},
                    },
                },
            )
            yield StreamEvent(
                uuid="evt-tool-delta-1",
                session_id="sess-1",
                event={
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"file_path":"forge/'},
                },
            )
            yield StreamEvent(
                uuid="evt-tool-delta-2",
                session_id="sess-1",
                event={
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": 'core/daemon.py"}',
                    },
                },
            )
            yield StreamEvent(
                uuid="evt-tool-stop",
                session_id="sess-1",
                event={"type": "content_block_stop", "index": 1},
            )
            yield _make_result_message(result="Done")

        options = ClaudeProvider._build_options(
            system_prompt="test",
            catalog_entry=_make_catalog_entry(),
            tool_policy=ToolPolicy(mode="unrestricted"),
            workspace=WorkspaceRoots(primary_cwd="/tmp/test"),
            max_turns=10,
        )

        with patch("forge.providers.claude.query", side_effect=_fake_query):
            await ClaudeProvider._run_query(
                prompt="test prompt",
                options=options,
                catalog_entry=_make_catalog_entry(),
                on_event=events.append,
            )

        tool_events = [event for event in events if event.kind == EventKind.TOOL_USE]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "read"
        assert tool_events[0].tool_input == '{"file_path": "forge/core/daemon.py"}'
