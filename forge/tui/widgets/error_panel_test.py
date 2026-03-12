"""Tests for error_panel — classification, suggestions, and formatting."""

from __future__ import annotations

import pytest

from forge.tui.widgets.error_panel import (
    ErrorClassification,
    ErrorSuggestion,
    classify_error,
    format_error_panel,
    get_suggestions,
)


# ── classify_error tests ────────────────────────────────────────────


class TestClassifyError:
    def test_empty_string_returns_unknown(self):
        result = classify_error("")
        assert result.category == "unknown"
        assert result.root_cause == "No error message available"

    def test_syntax_error(self):
        result = classify_error("SyntaxError: unexpected EOF while parsing")
        assert result.category == "syntax_error"
        assert "unexpected EOF" in result.root_cause

    def test_indentation_error(self):
        result = classify_error("IndentationError: unexpected indent")
        assert result.category == "syntax_error"

    def test_import_error_generic(self):
        result = classify_error("ImportError: cannot import name 'foo'")
        assert result.category == "import_error"

    def test_module_not_found(self):
        result = classify_error("ModuleNotFoundError: No module named 'nonexistent'")
        assert result.category == "import_error"
        assert "nonexistent" in result.root_cause

    def test_no_module_named_extracts_name(self):
        result = classify_error("No module named 'forge.missing_module'")
        assert result.category == "import_error"
        assert "forge.missing_module" in result.root_cause

    def test_test_failure_failed(self):
        result = classify_error("FAILED tests/test_foo.py::test_bar - AssertionError")
        assert result.category == "test_failure"

    def test_test_failure_count(self):
        result = classify_error("3 failed, 10 passed")
        assert result.category == "test_failure"
        assert "3" in result.root_cause

    def test_timeout(self):
        result = classify_error("TimeoutError: operation timed out after 300s")
        assert result.category == "timeout"

    def test_timed_out_variant(self):
        result = classify_error("Task timed out waiting for response")
        assert result.category == "timeout"

    def test_permission_error(self):
        result = classify_error("PermissionError: [Errno 13] Permission denied: '/etc/config'")
        assert result.category == "permission"

    def test_permission_denied_lowercase(self):
        result = classify_error("permission denied when accessing /tmp/file")
        assert result.category == "permission"

    def test_git_conflict(self):
        result = classify_error("CONFLICT (content): Merge conflict in src/main.py")
        assert result.category == "git_conflict"

    def test_merge_failed(self):
        result = classify_error("Merge failed: automatic merge went bad")
        assert result.category == "git_conflict"

    def test_rebase_conflict(self):
        result = classify_error("error: rebase had conflict in file.py")
        assert result.category == "git_conflict"

    def test_budget_exceeded(self):
        result = classify_error("budget_exceeded: cost $2.50 over limit $2.00")
        assert result.category == "budget_exceeded"

    def test_unknown_error(self):
        result = classify_error("Something completely unexpected happened")
        assert result.category == "unknown"
        assert "check the output log" in result.root_cause

    def test_traceback_extraction(self):
        error = '''Traceback (most recent call last):
  File "/app/src/main.py", line 42, in run
    do_stuff()
  File "/app/src/utils.py", line 17, in do_stuff
    raise ValueError("bad")
ValueError: bad'''
        result = classify_error(error)
        # Should extract the last file/line from traceback
        assert result.file_path == "/app/src/utils.py"
        assert result.line_number == 17

    def test_traceback_with_syntax_error(self):
        error = '''  File "forge/core/engine.py", line 55
    def broken(
              ^
SyntaxError: unexpected EOF while parsing'''
        result = classify_error(error)
        assert result.category == "syntax_error"
        assert result.file_path == "forge/core/engine.py"
        assert result.line_number == 55

    def test_no_traceback_no_file_info(self):
        result = classify_error("TimeoutError: timed out")
        assert result.file_path is None
        assert result.line_number is None


# ── get_suggestions tests ────────────────────────────────────────────


class TestGetSuggestions:
    def _classify_and_suggest(self, category: str) -> list[ErrorSuggestion]:
        c = ErrorClassification(category=category, root_cause="test")
        return get_suggestions(c, {})

    def test_syntax_error_suggestions(self):
        suggestions = self._classify_and_suggest("syntax_error")
        actions = [s.action for s in suggestions]
        assert "retry" in actions
        assert "edit" in actions
        assert "skip" in actions

    def test_import_error_suggestions(self):
        suggestions = self._classify_and_suggest("import_error")
        actions = [s.action for s in suggestions]
        assert "retry" in actions
        assert "edit" in actions

    def test_test_failure_suggestions(self):
        suggestions = self._classify_and_suggest("test_failure")
        actions = [s.action for s in suggestions]
        assert "retry" in actions
        assert "view_output" in actions
        assert "skip" in actions

    def test_timeout_suggestions(self):
        suggestions = self._classify_and_suggest("timeout")
        actions = [s.action for s in suggestions]
        assert "retry" in actions
        assert "increase_budget" in actions

    def test_git_conflict_suggestions(self):
        suggestions = self._classify_and_suggest("git_conflict")
        actions = [s.action for s in suggestions]
        assert "retry" in actions
        assert "manual_merge" in actions

    def test_budget_exceeded_suggestions(self):
        suggestions = self._classify_and_suggest("budget_exceeded")
        actions = [s.action for s in suggestions]
        assert "increase_budget" in actions
        assert "retry" in actions

    def test_unknown_suggestions(self):
        suggestions = self._classify_and_suggest("unknown")
        actions = [s.action for s in suggestions]
        assert "retry" in actions
        assert "skip" in actions

    def test_suggestions_have_keys_and_labels(self):
        suggestions = self._classify_and_suggest("syntax_error")
        for s in suggestions:
            assert s.key  # Non-empty key
            assert s.label  # Non-empty label
            assert s.action  # Non-empty action

    def test_suggestions_are_copies(self):
        """Ensure returned lists are copies, not references to internal data."""
        s1 = self._classify_and_suggest("syntax_error")
        s2 = self._classify_and_suggest("syntax_error")
        assert s1 is not s2


# ── format_error_panel tests ─────────────────────────────────────────


class TestFormatErrorPanel:
    def test_basic_rendering(self):
        task = {"title": "Setup DB", "error": "SyntaxError: bad code", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "Setup DB" in result
        assert "SYNTAX ERROR" in result
        assert "SyntaxError" in result

    def test_includes_root_cause(self):
        task = {"title": "Fix", "error": "No module named 'requests'", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "Root cause" in result
        assert "requests" in result

    def test_includes_file_context(self):
        error = 'File "src/app.py", line 10\nSyntaxError: invalid syntax'
        task = {"title": "Build", "error": error, "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "src/app.py" in result
        assert "line 10" in result

    def test_includes_output_tail(self):
        task = {"title": "Test", "error": "failed", "state": "error"}
        lines = [f"line {i}" for i in range(30)]
        result = format_error_panel("t1", task, lines)
        assert "line 29" in result  # Last line should be present
        assert "Last output" in result

    def test_no_output(self):
        task = {"title": "Test", "error": "failed", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "No output captured" in result

    def test_files_changed(self):
        task = {
            "title": "Test",
            "error": "failed",
            "state": "error",
            "files_changed": ["src/a.py", "src/b.py"],
        }
        result = format_error_panel("t1", task, [])
        assert "src/a.py" in result
        assert "src/b.py" in result

    def test_error_history_shown(self):
        task = {"title": "Flaky", "error": "test failed again", "state": "error"}
        history = ["first failure: timeout", "second failure: assertion"]
        result = format_error_panel("t1", task, [], error_history=history)
        assert "Previous failures (2)" in result
        assert "first failure" in result
        assert "second failure" in result

    def test_error_history_truncates_long_messages(self):
        task = {"title": "X", "error": "err", "state": "error"}
        long_msg = "x" * 200
        result = format_error_panel("t1", task, [], error_history=[long_msg])
        assert "..." in result
        # Should be truncated to 120 chars + "..."
        assert "x" * 121 not in result

    def test_no_error_history(self):
        task = {"title": "X", "error": "err", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "Previous failures" not in result

    def test_empty_error_history(self):
        task = {"title": "X", "error": "err", "state": "error"}
        result = format_error_panel("t1", task, [], error_history=[])
        assert "Previous failures" not in result

    def test_suggested_actions_for_test_failure(self):
        task = {"title": "Run tests", "error": "3 failed, 7 passed", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "[R]" in result
        assert "[v]" in result
        assert "[s]" in result

    def test_suggested_actions_for_timeout(self):
        task = {"title": "Slow", "error": "TimeoutError: timed out", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "[b]" in result  # Increase budget
        assert "Increase budget" in result

    def test_suggested_actions_for_git_conflict(self):
        task = {"title": "Merge", "error": "CONFLICT in main.py", "state": "error"}
        result = format_error_panel("t1", task, [])
        assert "[m]" in result  # Manual merge
        assert "Manual merge" in result

    def test_output_lines_contribute_to_classification(self):
        """When error msg is generic but output has clues, classification should use output."""
        task = {"title": "Task", "error": "Agent failed", "state": "error"}
        output = ["running tests...", "FAILED tests/test_x.py - AssertionError"]
        result = format_error_panel("t1", task, output)
        assert "TEST FAILURE" in result


# ── TuiState error_history integration tests ─────────────────────────


class TestTuiStateErrorHistory:
    def _make_state_with_task(self, task_id: str = "t1") -> "TuiState":
        from forge.tui.state import TuiState

        state = TuiState()
        state.apply_event(
            "pipeline:plan_ready",
            {
                "tasks": [
                    {
                        "id": task_id,
                        "title": "X",
                        "description": "",
                        "files": ["f"],
                        "depends_on": [],
                        "complexity": "low",
                    }
                ]
            },
        )
        return state

    def test_error_history_initialized(self):
        from forge.tui.state import TuiState

        state = TuiState()
        assert state.error_history == {}

    def test_error_history_populated_on_error(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error", "error": "first error"},
        )
        assert state.error_history["t1"] == ["first error"]

    def test_error_history_accumulates(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error", "error": "first"},
        )
        # Simulate retry → back to in_progress → error again
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "in_progress"},
        )
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error", "error": "second"},
        )
        assert state.error_history["t1"] == ["first", "second"]

    def test_error_history_not_populated_for_non_error_states(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "done"},
        )
        assert "t1" not in state.error_history

    def test_error_history_cleared_on_reset(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error", "error": "err"},
        )
        state.reset()
        assert state.error_history == {}

    def test_error_history_cleared_on_restart(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error", "error": "err"},
        )
        state.apply_event("pipeline:restarted", {})
        assert state.error_history == {}

    def test_error_history_ignores_empty_error(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error", "error": ""},
        )
        assert state.error_history.get("t1", []) == []

    def test_error_history_ignores_missing_error_key(self):
        state = self._make_state_with_task()
        state.apply_event(
            "task:state_changed",
            {"task_id": "t1", "state": "error"},
        )
        assert state.error_history.get("t1", []) == []
