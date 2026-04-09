"""Tests for forge/providers/safety_auditor.py — policy enforcement."""

from __future__ import annotations

import json

from forge.providers.base import (
    AuditVerdict,
    EventKind,
    ProviderEvent,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.safety_auditor import SafetyAuditor


def _bash_event(command: str) -> ProviderEvent:
    """Create a Bash TOOL_USE event."""
    return ProviderEvent(
        kind=EventKind.TOOL_USE,
        tool_name="Bash",
        tool_call_id="call-1",
        tool_input=json.dumps({"command": command}),
    )


def _codex_bash_event(command: str) -> ProviderEvent:
    """Create a Codex command_execution TOOL_USE event."""
    return ProviderEvent(
        kind=EventKind.TOOL_USE,
        tool_name="command_execution",
        tool_call_id="call-1",
        tool_input=json.dumps({"command": command}),
    )


def _write_event(tool_name: str, file_path: str) -> ProviderEvent:
    """Create a Write/Edit TOOL_USE event."""
    return ProviderEvent(
        kind=EventKind.TOOL_USE,
        tool_name=tool_name,
        tool_call_id="call-1",
        tool_input=json.dumps({"file_path": file_path}),
    )


_WORKSPACE = WorkspaceRoots(primary_cwd="/tmp/work")


class TestGitPushBlockedClaude:
    """git:push blocked from Claude SDK events."""

    def test_git_push_blocked(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("git push origin main")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_git_push_case_insensitive(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("GIT PUSH origin main")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_git_status_allowed(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("git status")
        assert auditor.check(event) == AuditVerdict.ALLOW


class TestGitPushBlockedOpenAI:
    """git:push blocked from OpenAI Codex events."""

    def test_codex_git_push_blocked(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _codex_bash_event("git push origin main")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_codex_git_diff_allowed(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _codex_bash_event("git diff")
        assert auditor.check(event) == AuditVerdict.ALLOW


class TestAllowlistMode:
    """Allowlist mode blocks unlisted tools."""

    def test_listed_tool_allowed(self) -> None:
        policy = ToolPolicy(mode="allowlist", allowed_tools=["Read", "Grep"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = ProviderEvent(kind=EventKind.TOOL_USE, tool_name="Read", tool_call_id="c1")
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_unlisted_tool_blocked(self) -> None:
        policy = ToolPolicy(mode="allowlist", allowed_tools=["Read", "Grep"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = ProviderEvent(kind=EventKind.TOOL_USE, tool_name="Write", tool_call_id="c1")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_bash_blocked_when_not_in_allowlist(self) -> None:
        policy = ToolPolicy(mode="allowlist", allowed_tools=["Read"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("ls")
        assert auditor.check(event) == AuditVerdict.ABORT


class TestDenylistMode:
    """Denylist mode allows unlisted tools."""

    def test_unlisted_tool_allowed(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = ProviderEvent(kind=EventKind.TOOL_USE, tool_name="Read", tool_call_id="c1")
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_denied_operation_blocked(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["net:curl"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("curl https://example.com")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_multiple_denied_operations(self) -> None:
        policy = ToolPolicy(
            mode="denylist", denied_operations=["git:push", "net:curl", "priv:sudo"]
        )
        auditor = SafetyAuditor(policy, _WORKSPACE)
        assert auditor.check(_bash_event("sudo rm -rf /")) == AuditVerdict.ABORT
        assert auditor.check(_bash_event("git push")) == AuditVerdict.ABORT
        assert auditor.check(_bash_event("python test.py")) == AuditVerdict.ALLOW


class TestUnrestrictedMode:
    """Unrestricted mode allows everything."""

    def test_allows_git_push(self) -> None:
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("git push origin main")
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_allows_any_tool(self) -> None:
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = ProviderEvent(kind=EventKind.TOOL_USE, tool_name="DangerousTool", tool_call_id="c1")
        assert auditor.check(event) == AuditVerdict.ALLOW


class TestReadOnlyDirWriteBlocked:
    """Writes to read-only directories are blocked."""

    def test_write_to_read_only_blocked(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work",
            read_only_dirs=["/opt/shared"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = _write_event("Write", "/opt/shared/file.py")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_edit_to_read_only_blocked(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work",
            read_only_dirs=["/opt/shared"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = _write_event("Edit", "/opt/shared/nested/file.py")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_write_to_primary_cwd_allowed(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work",
            read_only_dirs=["/opt/shared"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = _write_event("Write", "/tmp/work/file.py")
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_read_from_read_only_allowed(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work",
            read_only_dirs=["/opt/shared"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="Read",
            tool_call_id="c1",
            tool_input=json.dumps({"file_path": "/opt/shared/file.py"}),
        )
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_relative_write_escape_to_read_only_blocked(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work/.forge/worktrees/task-1",
            read_only_dirs=["/tmp/work/backend"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = _write_event("Write", "../../../backend/src/app.py")
        assert auditor.check(event) == AuditVerdict.ABORT

    def test_relative_write_inside_worktree_allowed(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work/.forge/worktrees/task-1",
            read_only_dirs=["/tmp/work/backend"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = _write_event("Write", "./src/app.py")
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_file_change_list_to_read_only_blocked(self) -> None:
        workspace = WorkspaceRoots(
            primary_cwd="/tmp/work/.forge/worktrees/task-1",
            read_only_dirs=["/tmp/work/backend"],
        )
        policy = ToolPolicy(mode="unrestricted")
        auditor = SafetyAuditor(policy, workspace)
        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="file_change",
            tool_call_id="call-1",
            tool_input=json.dumps([{"path": "../../../backend/src/app.py", "kind": "replace"}]),
        )
        assert auditor.check(event) == AuditVerdict.ABORT


class TestNonToolUseEvents:
    """Non-TOOL_USE events always return ALLOW."""

    def test_text_event_allowed(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = ProviderEvent(kind=EventKind.TEXT, text="hello")
        assert auditor.check(event) == AuditVerdict.ALLOW

    def test_error_event_allowed(self) -> None:
        policy = ToolPolicy(mode="allowlist", allowed_tools=[])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = ProviderEvent(kind=EventKind.ERROR, text="error msg")
        assert auditor.check(event) == AuditVerdict.ALLOW


class TestViolationTracking:
    """Violations are recorded for audit."""

    def test_violations_tracked(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("git push origin main")
        auditor.check(event)
        assert len(auditor.violations) == 1
        assert auditor.violations[0].denied_pattern == "git:push"

    def test_no_violations_on_allow(self) -> None:
        policy = ToolPolicy(mode="denylist", denied_operations=["git:push"])
        auditor = SafetyAuditor(policy, _WORKSPACE)
        event = _bash_event("git status")
        auditor.check(event)
        assert len(auditor.violations) == 0
