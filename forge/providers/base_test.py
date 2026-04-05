"""Tests for forge/providers/base.py — core provider types."""

from __future__ import annotations

import pytest

from forge.providers.base import (
    AuditVerdict,
    CatalogEntry,
    EventKind,
    ExecutionMode,
    MCPServerConfig,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ProviderHealthStatus,
    ResumeState,
    SafetyBoundary,
    SafetyViolation,
    ToolPolicy,
    WorkspaceRoots,
)


# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------


class TestModelSpecParse:
    def test_bare_claude_alias(self) -> None:
        spec = ModelSpec.parse("sonnet")
        assert spec.provider == "claude"
        assert spec.model == "sonnet"

    def test_bare_opus(self) -> None:
        spec = ModelSpec.parse("opus")
        assert spec.provider == "claude"
        assert spec.model == "opus"

    def test_bare_haiku(self) -> None:
        spec = ModelSpec.parse("haiku")
        assert spec.provider == "claude"
        assert spec.model == "haiku"

    def test_bare_openai_alias(self) -> None:
        spec = ModelSpec.parse("gpt-5.4")
        assert spec.provider == "openai"
        assert spec.model == "gpt-5.4"

    def test_provider_colon_model(self) -> None:
        spec = ModelSpec.parse("claude:opus")
        assert spec.provider == "claude"
        assert spec.model == "opus"

    def test_openai_colon_model(self) -> None:
        spec = ModelSpec.parse("openai:gpt-5.4")
        assert spec.provider == "openai"
        assert spec.model == "gpt-5.4"

    def test_unknown_bare_alias_defaults_claude(self) -> None:
        spec = ModelSpec.parse("future-model")
        assert spec.provider == "claude"
        assert spec.model == "future-model"

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            ModelSpec.parse("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            ModelSpec.parse("   ")

    def test_invalid_colon_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            ModelSpec.parse(":sonnet")

    def test_invalid_trailing_colon(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            ModelSpec.parse("claude:")

    def test_str_representation(self) -> None:
        spec = ModelSpec(provider="claude", model="sonnet")
        assert str(spec) == "claude:sonnet"

    def test_frozen(self) -> None:
        spec = ModelSpec(provider="claude", model="sonnet")
        with pytest.raises(AttributeError):
            spec.provider = "openai"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CatalogEntry
# ---------------------------------------------------------------------------


class TestCatalogEntry:
    @pytest.fixture
    def entry(self) -> CatalogEntry:
        return CatalogEntry(
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
            max_context_tokens=200_000,
            supports_structured_output=False,
            supports_reasoning=True,
            cost_key="claude:sonnet",
            validated_stages=frozenset(["planner", "agent"]),
        )

    def test_spec_property(self, entry: CatalogEntry) -> None:
        spec = entry.spec
        assert spec.provider == "claude"
        assert spec.model == "sonnet"

    def test_resolved_cost_key_uses_cost_key(self, entry: CatalogEntry) -> None:
        assert entry.resolved_cost_key == "claude:sonnet"

    def test_resolved_cost_key_fallback(self) -> None:
        entry = CatalogEntry(
            provider="claude",
            alias="test",
            canonical_id="test-id",
            backend="claude-code-sdk",
            tier="experimental",
            can_use_tools=True,
            can_stream=True,
            can_resume_session=False,
            can_run_shell=False,
            can_edit_files=False,
            supports_mcp_servers=False,
            max_context_tokens=100_000,
            supports_structured_output=False,
            supports_reasoning=False,
            cost_key="",
            validated_stages=frozenset(),
        )
        assert entry.resolved_cost_key == "claude:test"


# ---------------------------------------------------------------------------
# ResumeState serialization
# ---------------------------------------------------------------------------


class TestResumeState:
    def test_round_trip(self) -> None:
        state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token="tok-123",
            created_at="2026-04-05T10:00:00Z",
            last_active_at="2026-04-05T10:05:00Z",
            turn_count=3,
            is_resumable=True,
        )
        json_str = state.to_json()
        restored = ResumeState.from_json(json_str)
        assert restored.provider == state.provider
        assert restored.backend == state.backend
        assert restored.session_token == state.session_token
        assert restored.created_at == state.created_at
        assert restored.last_active_at == state.last_active_at
        assert restored.turn_count == state.turn_count
        assert restored.is_resumable == state.is_resumable

    def test_from_json_invalid(self) -> None:
        with pytest.raises((KeyError, Exception)):
            ResumeState.from_json("{}")


# ---------------------------------------------------------------------------
# ProviderEvent
# ---------------------------------------------------------------------------


class TestProviderEvent:
    def test_text_event(self) -> None:
        evt = ProviderEvent(kind=EventKind.TEXT, text="hello")
        assert evt.kind == EventKind.TEXT
        assert evt.text == "hello"
        assert evt.tool_name is None

    def test_tool_use_event(self) -> None:
        evt = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="Bash",
            tool_call_id="call-1",
            tool_input='{"command": "ls"}',
        )
        assert evt.kind == EventKind.TOOL_USE
        assert evt.tool_name == "Bash"
        assert evt.tool_input == '{"command": "ls"}'

    def test_usage_event(self) -> None:
        evt = ProviderEvent(
            kind=EventKind.USAGE, input_tokens=100, output_tokens=50
        )
        assert evt.input_tokens == 100
        assert evt.output_tokens == 50


# ---------------------------------------------------------------------------
# EventKind
# ---------------------------------------------------------------------------


class TestEventKind:
    def test_exhaustive_values(self) -> None:
        expected = {"text", "tool_use", "tool_result", "error", "usage", "status"}
        actual = {e.value for e in EventKind}
        assert actual == expected

    def test_str_enum(self) -> None:
        assert EventKind.TEXT == "text"
        assert isinstance(EventKind.TEXT, str)


# ---------------------------------------------------------------------------
# ToolPolicy
# ---------------------------------------------------------------------------


class TestToolPolicy:
    def test_unrestricted(self) -> None:
        p = ToolPolicy(mode="unrestricted")
        assert p.mode == "unrestricted"
        assert p.allowed_tools == []
        assert p.denied_operations == []

    def test_allowlist(self) -> None:
        p = ToolPolicy(mode="allowlist", allowed_tools=["Read", "Grep"])
        assert p.mode == "allowlist"
        assert "Read" in p.allowed_tools

    def test_denylist(self) -> None:
        p = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        assert p.mode == "denylist"
        assert "git:push" in p.denied_operations

    def test_frozen(self) -> None:
        p = ToolPolicy(mode="unrestricted")
        with pytest.raises(AttributeError):
            p.mode = "denylist"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# OutputContract
# ---------------------------------------------------------------------------


class TestOutputContract:
    def test_freeform(self) -> None:
        c = OutputContract(format="freeform")
        assert c.format == "freeform"
        assert c.json_schema is None

    def test_json_with_schema(self) -> None:
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        c = OutputContract(format="json", json_schema=schema)
        assert c.format == "json"
        assert c.json_schema == schema


# ---------------------------------------------------------------------------
# WorkspaceRoots
# ---------------------------------------------------------------------------


class TestWorkspaceRoots:
    def test_defaults(self) -> None:
        w = WorkspaceRoots(primary_cwd="/tmp/work")
        assert w.primary_cwd == "/tmp/work"
        assert w.read_only_dirs == []

    def test_with_read_only(self) -> None:
        w = WorkspaceRoots(primary_cwd="/tmp/work", read_only_dirs=["/opt/shared"])
        assert "/opt/shared" in w.read_only_dirs


# ---------------------------------------------------------------------------
# Other types
# ---------------------------------------------------------------------------


class TestMiscTypes:
    def test_execution_mode_values(self) -> None:
        assert ExecutionMode.CODING == "coding"
        assert ExecutionMode.INTELLIGENCE == "intelligence"

    def test_audit_verdict_values(self) -> None:
        assert AuditVerdict.ALLOW == "allow"
        assert AuditVerdict.ABORT == "abort"
        assert AuditVerdict.WARN == "warn"

    def test_safety_boundary(self) -> None:
        sb = SafetyBoundary(denied_operations=["git:push"])
        assert "git:push" in sb.denied_operations

    def test_safety_violation(self) -> None:
        v = SafetyViolation(
            tool_name="Bash",
            tool_input="git push",
            denied_pattern="git:push",
            verdict=AuditVerdict.ABORT,
            reason="blocked",
        )
        assert v.tool_name == "Bash"
        assert v.verdict == AuditVerdict.ABORT

    def test_provider_health_status(self) -> None:
        s = ProviderHealthStatus(
            healthy=True, provider="claude", details="ok", errors=[]
        )
        assert s.healthy is True

    def test_mcp_server_config(self) -> None:
        c = MCPServerConfig(name="test", command="node", args=["server.js"])
        assert c.name == "test"
        assert c.env is None
