"""Tests for blocked reason formatting utilities."""

from forge.core.blocked_reason import format_blocked_detail, format_blocked_reason


class TestFormatBlockedReason:
    """Test format_blocked_reason function."""

    def test_format_single_waiting_dependency(self):
        """Single waiting dependency should pass through unchanged."""
        reason = "Waiting on task-2"
        result = format_blocked_reason(reason)
        assert result == "Waiting on task-2"

    def test_format_multiple_waiting_dependencies_two(self):
        """Two waiting dependencies should show '+ 1 other'."""
        reason = "Waiting on task-2, task-3"
        result = format_blocked_reason(reason)
        assert result == "Waiting on task-2 + 1 other"

    def test_format_multiple_waiting_dependencies_three(self):
        """Three waiting dependencies should show '+ 2 others'."""
        reason = "Waiting on task-2, task-3, task-4"
        result = format_blocked_reason(reason)
        assert result == "Waiting on task-2 + 2 others"

    def test_format_multiple_waiting_dependencies_five(self):
        """Five waiting dependencies should show '+ 4 others'."""
        reason = "Waiting on auth-backend, db-setup, api-server, web-ui, tests"
        result = format_blocked_reason(reason)
        assert result == "Waiting on auth-backend + 4 others"

    def test_format_single_failed_dependency(self):
        """Single failed dependency should format with 'failed' suffix."""
        reason = "Blocked by failed dependency: auth-backend"
        result = format_blocked_reason(reason)
        assert result == "Blocked: auth-backend failed"

    def test_format_multiple_failed_dependencies_two(self):
        """Two failed dependencies should show first + count."""
        reason = "Blocked by failed dependencies: auth-backend, db-setup"
        result = format_blocked_reason(reason)
        assert result == "Blocked: auth-backend + 1 other failed"

    def test_format_multiple_failed_dependencies_three(self):
        """Three failed dependencies should show first + count."""
        reason = "Blocked by failed dependencies: auth-backend, db-setup, api-server"
        result = format_blocked_reason(reason)
        assert result == "Blocked: auth-backend + 2 others failed"

    def test_format_human_input_needed(self):
        """Human decision should be formatted as input needed."""
        reason = "Human decision required before resume"
        result = format_blocked_reason(reason)
        assert result == "Needs human input before retry"

    def test_format_human_approval_needed(self):
        """Human approval should be formatted as waiting for approval."""
        reason = "Human approval required before merge"
        result = format_blocked_reason(reason)
        assert result == "Waiting for approval"

    def test_format_manual_intervention(self):
        """Manual intervention should be formatted clearly."""
        reason = "Blocked - waiting for manual intervention"
        result = format_blocked_reason(reason)
        assert result == "Blocked: needs manual intervention"

    def test_format_task_failed(self):
        """Task failed should be formatted with retry/skip option."""
        reason = "Task failed and needs retry or skip"
        result = format_blocked_reason(reason)
        assert result == "Failed: needs retry or skip"

    def test_format_empty_reason(self):
        """Empty reason should return empty string."""
        reason = ""
        result = format_blocked_reason(reason)
        assert result == ""

    def test_format_none_reason(self):
        """None reason should return empty string."""
        reason = None
        result = format_blocked_reason(reason)
        assert result == ""

    def test_format_unknown_reason(self):
        """Unknown reason should pass through unchanged."""
        reason = "Some unexpected reason format"
        result = format_blocked_reason(reason)
        assert result == "Some unexpected reason format"

    def test_format_without_status_parameter(self):
        """Ensure function works without status parameter."""
        reason = "Waiting on task-2"
        result = format_blocked_reason(reason)
        assert result == "Waiting on task-2"


class TestFormatBlockedDetail:
    """Test format_blocked_detail function."""

    def test_detail_waiting_single_dep(self):
        """Single waiting dependency should list the dependency."""
        reason = "Waiting on task-2"
        result = format_blocked_detail(reason)
        expected = "Waiting for dependencies to complete:\n  - task-2"
        assert result == expected

    def test_detail_waiting_multiple_deps(self):
        """Multiple waiting dependencies should list all dependencies."""
        reason = "Waiting on task-2, task-3, auth-backend"
        result = format_blocked_detail(reason)
        expected = "Waiting for dependencies to complete:\n  - task-2\n  - task-3\n  - auth-backend"
        assert result == expected

    def test_detail_blocked_single_failed(self):
        """Single failed dependency should list it as failed."""
        reason = "Blocked by failed dependency: auth-backend"
        result = format_blocked_detail(reason)
        expected = "Blocked by failed dependency:\n  - auth-backend (failed)"
        assert result == expected

    def test_detail_blocked_multiple_failed(self):
        """Multiple failed dependencies should list all as failed."""
        reason = "Blocked by failed dependencies: auth-backend, db-setup"
        result = format_blocked_detail(reason)
        expected = (
            "Blocked by failed dependencies:\n  - auth-backend (failed)\n  - db-setup (failed)"
        )
        assert result == expected

    def test_detail_human_decision_input(self):
        """Human decision should explain input needed."""
        reason = "Human decision required before resume"
        result = format_blocked_detail(reason)
        expected = "This task needs human input before it can continue."
        assert result == expected

    def test_detail_human_approval_input(self):
        """Human approval should explain input needed."""
        reason = "Human approval required before merge"
        result = format_blocked_detail(reason)
        expected = "This task needs human input before it can continue."
        assert result == expected

    def test_detail_manual_intervention(self):
        """Manual intervention should explain the blocking."""
        reason = "Blocked - waiting for manual intervention"
        result = format_blocked_detail(reason)
        expected = "This task is blocked and needs manual intervention."
        assert result == expected

    def test_detail_task_failed(self):
        """Task failed should explain the failure."""
        reason = "Task failed and needs retry or skip"
        result = format_blocked_detail(reason)
        expected = "This task failed and needs to be retried or skipped."
        assert result == expected

    def test_detail_empty_reason(self):
        """Empty reason should return empty string."""
        reason = ""
        result = format_blocked_detail(reason)
        assert result == ""

    def test_detail_none_reason(self):
        """None reason should return empty string."""
        reason = None
        result = format_blocked_detail(reason)
        assert result == ""

    def test_detail_with_blocking_task_ids(self):
        """Blocking task IDs parameter should be accepted."""
        reason = "Waiting on task-2, task-3"
        result = format_blocked_detail(reason)
        expected = "Waiting for dependencies to complete:\n  - task-2\n  - task-3"
        assert result == expected

    def test_detail_unknown_reason(self):
        """Unknown reason should pass through unchanged."""
        reason = "Some unexpected reason format"
        result = format_blocked_detail(reason)
        assert result == "Some unexpected reason format"


class TestInterfaceContracts:
    """Test exact interface contract transformations."""

    def test_blocked_reason_transform_rules(self):
        """Test exact input-to-output mappings from interface contracts."""
        # Single waiting
        assert format_blocked_reason("Waiting on task-2") == "Waiting on task-2"

        # Multi waiting
        assert format_blocked_reason("Waiting on task-2, task-3") == "Waiting on task-2 + 1 other"
        assert (
            format_blocked_reason("Waiting on task-2, task-3, task-4")
            == "Waiting on task-2 + 2 others"
        )

        # Single failed dep
        assert (
            format_blocked_reason("Blocked by failed dependency: auth-backend")
            == "Blocked: auth-backend failed"
        )

        # Multi failed deps
        assert (
            format_blocked_reason("Blocked by failed dependencies: auth-backend, db-setup")
            == "Blocked: auth-backend + 1 other failed"
        )

        # Human decision/approval
        assert (
            format_blocked_reason("Human decision required before resume")
            == "Needs human input before retry"
        )
        assert (
            format_blocked_reason("Human approval required before merge") == "Waiting for approval"
        )

        # Manual intervention
        assert (
            format_blocked_reason("Blocked - waiting for manual intervention")
            == "Blocked: needs manual intervention"
        )

        # Task failed
        assert (
            format_blocked_reason("Task failed and needs retry or skip")
            == "Failed: needs retry or skip"
        )

        # Empty/None
        assert format_blocked_reason("") == ""
        assert format_blocked_reason(None) == ""
