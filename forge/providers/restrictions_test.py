"""Tests for forge/providers/restrictions.py — stage tool policies."""

from __future__ import annotations

from forge.providers.restrictions import (
    AGENT_DENIED_OPERATIONS,
    AGENT_TOOL_POLICY,
    CONTRACT_TOOL_POLICY,
    PLANNER_TOOL_POLICY,
    REVIEWER_TOOL_POLICY,
)


class TestPlannerPolicy:
    def test_mode_is_allowlist(self) -> None:
        assert PLANNER_TOOL_POLICY.mode == "allowlist"

    def test_allowed_tools(self) -> None:
        assert set(PLANNER_TOOL_POLICY.allowed_tools) == {"Read", "Glob", "Grep", "Bash"}

    def test_no_denied_operations(self) -> None:
        assert PLANNER_TOOL_POLICY.denied_operations == []


class TestContractPolicy:
    def test_mode_is_allowlist(self) -> None:
        assert CONTRACT_TOOL_POLICY.mode == "allowlist"

    def test_allowed_tools(self) -> None:
        assert set(CONTRACT_TOOL_POLICY.allowed_tools) == {"Read", "Glob", "Grep"}

    def test_no_bash(self) -> None:
        assert "Bash" not in CONTRACT_TOOL_POLICY.allowed_tools


class TestAgentPolicy:
    def test_mode_is_denylist(self) -> None:
        assert AGENT_TOOL_POLICY.mode == "denylist"

    def test_no_allowed_tools(self) -> None:
        assert AGENT_TOOL_POLICY.allowed_tools == []

    def test_denied_operations_not_empty(self) -> None:
        assert len(AGENT_TOOL_POLICY.denied_operations) > 0

    def test_denied_operations_match_constant(self) -> None:
        assert AGENT_TOOL_POLICY.denied_operations == list(AGENT_DENIED_OPERATIONS)

    def test_git_push_denied(self) -> None:
        assert "git:push" in AGENT_TOOL_POLICY.denied_operations

    def test_net_curl_denied(self) -> None:
        assert "net:curl" in AGENT_TOOL_POLICY.denied_operations

    def test_sudo_denied(self) -> None:
        assert "priv:sudo" in AGENT_TOOL_POLICY.denied_operations

    def test_docker_denied(self) -> None:
        assert "container:docker" in AGENT_TOOL_POLICY.denied_operations


class TestReviewerPolicy:
    def test_mode_is_allowlist(self) -> None:
        assert REVIEWER_TOOL_POLICY.mode == "allowlist"

    def test_allowed_tools(self) -> None:
        assert set(REVIEWER_TOOL_POLICY.allowed_tools) == {"Read", "Glob", "Grep", "Bash"}

    def test_no_denied_operations(self) -> None:
        assert REVIEWER_TOOL_POLICY.denied_operations == []


class TestDeniedOperationsList:
    def test_has_git_operations(self) -> None:
        git_ops = [op for op in AGENT_DENIED_OPERATIONS if op.startswith("git:")]
        assert len(git_ops) >= 10

    def test_has_net_operations(self) -> None:
        net_ops = [op for op in AGENT_DENIED_OPERATIONS if op.startswith("net:")]
        assert len(net_ops) >= 6

    def test_has_priv_operations(self) -> None:
        priv_ops = [op for op in AGENT_DENIED_OPERATIONS if op.startswith("priv:")]
        assert len(priv_ops) >= 3

    def test_no_duplicates(self) -> None:
        assert len(AGENT_DENIED_OPERATIONS) == len(set(AGENT_DENIED_OPERATIONS))
