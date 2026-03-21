"""Tests for RuntimeGuard — retry loop detection."""

from dataclasses import dataclass

import pytest

from forge.learning.guard import (
    GuardTriggered,
    RuntimeGuard,
    classify_error,
    normalize_command,
)


# ---------------------------------------------------------------------------
# Mock SDK message types
# ---------------------------------------------------------------------------

@dataclass
class MockToolUse:
    id: str
    name: str
    input: dict


@dataclass
class MockToolResult:
    tool_use_id: str
    content: str
    is_error: bool


@dataclass
class MockMessage:
    content: list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bash_call_msg(tool_id: str, command: str) -> MockMessage:
    return MockMessage(content=[MockToolUse(id=tool_id, name="Bash", input={"command": command})])


def _result_msg(tool_id: str, content: str, is_error: bool) -> MockMessage:
    return MockMessage(content=[MockToolResult(tool_use_id=tool_id, content=content, is_error=is_error)])


# ---------------------------------------------------------------------------
# normalize_command tests
# ---------------------------------------------------------------------------

class TestNormalizeCommand:
    def test_strips_redirects(self):
        assert "pytest tests/" == normalize_command("pytest tests/ 2>&1 | tail -80")

    def test_strips_temp_paths(self):
        result = normalize_command("cat /tmp/pytest-abc123/output.log")
        assert "/tmp/TEMP" in result
        assert "pytest-abc123" not in result

    def test_strips_uuids(self):
        result = normalize_command("docker run a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert "UUID" in result
        assert "a1b2c3d4" not in result

    def test_preserves_meaningful_parts(self):
        result = normalize_command("pytest tests/ --timeout=30 -x -v")
        assert result == "pytest tests/ --timeout=30 -x -v"


# ---------------------------------------------------------------------------
# classify_error tests
# ---------------------------------------------------------------------------

class TestClassifyError:
    def test_module_not_found(self):
        assert classify_error("ModuleNotFoundError: No module named 'foo'") == "module_not_found"

    def test_command_not_found(self):
        assert classify_error("bash: foobar: command not found") == "command_not_found"

    def test_unknown(self):
        assert classify_error("something went sideways") == "unknown"


# ---------------------------------------------------------------------------
# RuntimeGuard tests
# ---------------------------------------------------------------------------

class TestRuntimeGuard:
    def test_no_trigger_on_success(self):
        guard = RuntimeGuard()
        for i in range(5):
            tid = f"t{i}"
            guard.inspect(_bash_call_msg(tid, "pytest tests/"))
            result = guard.inspect(_result_msg(tid, "All tests passed", is_error=False))
            assert result is None
        assert not guard.triggered
        assert not guard.warning_issued
        assert guard.failures == []

    def test_no_trigger_on_different_errors(self):
        """Different error classes for the same command should not accumulate."""
        guard = RuntimeGuard()

        guard.inspect(_bash_call_msg("t1", "python app.py"))
        guard.inspect(_result_msg("t1", "ModuleNotFoundError: No module named 'x'", is_error=True))

        guard.inspect(_bash_call_msg("t2", "python app.py"))
        guard.inspect(_result_msg("t2", "permission denied", is_error=True))

        guard.inspect(_bash_call_msg("t3", "python app.py"))
        result = guard.inspect(_result_msg("t3", "SyntaxError: invalid syntax", is_error=True))

        assert not guard.triggered
        # None of the individual error classes reached max_attempts
        assert result is None

    def test_warning_on_second_failure(self):
        guard = RuntimeGuard()

        guard.inspect(_bash_call_msg("t1", "pytest tests/"))
        guard.inspect(_result_msg("t1", "ModuleNotFoundError: No module named 'foo'", is_error=True))

        guard.inspect(_bash_call_msg("t2", "pytest tests/"))
        result = guard.inspect(_result_msg("t2", "ModuleNotFoundError: No module named 'foo'", is_error=True))

        assert result == "warning"
        assert guard.warning_issued
        assert not guard.triggered

    def test_trigger_on_third_failure(self):
        guard = RuntimeGuard()

        guard.inspect(_bash_call_msg("t1", "pytest tests/"))
        guard.inspect(_result_msg("t1", "ModuleNotFoundError: No module named 'foo'", is_error=True))

        guard.inspect(_bash_call_msg("t2", "pytest tests/"))
        guard.inspect(_result_msg("t2", "ModuleNotFoundError: No module named 'bar'", is_error=True))

        guard.inspect(_bash_call_msg("t3", "pytest tests/"))
        with pytest.raises(GuardTriggered) as exc_info:
            guard.inspect(_result_msg("t3", "ModuleNotFoundError: No module named 'baz'", is_error=True))

        assert guard.triggered
        assert len(exc_info.value.failures) == 3

    def test_different_commands_same_error_dont_accumulate(self):
        """Different normalized commands should get separate counters."""
        guard = RuntimeGuard()

        guard.inspect(_bash_call_msg("t1", "pytest tests/"))
        guard.inspect(_result_msg("t1", "ModuleNotFoundError: x", is_error=True))

        guard.inspect(_bash_call_msg("t2", "python -m pytest tests/"))
        guard.inspect(_result_msg("t2", "ModuleNotFoundError: x", is_error=True))

        guard.inspect(_bash_call_msg("t3", "pip install foo"))
        result = guard.inspect(_result_msg("t3", "ModuleNotFoundError: x", is_error=True))

        # Three different normalized commands -- none should have reached 2
        assert not guard.triggered
        assert result is None

    def test_get_warning_message(self):
        guard = RuntimeGuard()

        guard.inspect(_bash_call_msg("t1", "pytest tests/"))
        guard.inspect(_result_msg("t1", "ModuleNotFoundError: No module named 'foo'", is_error=True))

        guard.inspect(_bash_call_msg("t2", "pytest tests/"))
        guard.inspect(_result_msg("t2", "ModuleNotFoundError: No module named 'foo'", is_error=True))

        msg = guard.get_warning_message()
        assert "pytest tests/" in msg
        assert "module_not_found" in msg
        assert "2 times" in msg

    def test_get_failure_summary(self):
        guard = RuntimeGuard()

        guard.inspect(_bash_call_msg("t1", "pytest tests/"))
        guard.inspect(_result_msg("t1", "ModuleNotFoundError: No module named 'foo'", is_error=True))

        guard.inspect(_bash_call_msg("t2", "pip install bar"))
        guard.inspect(_result_msg("t2", "permission denied", is_error=True))

        summary = guard.get_failure_summary()
        assert "2 command failures" in summary
        assert "pytest tests/" in summary
        assert "pip install bar" in summary
