"""Tests for daemon_executor — worktree rebase and prompt selection."""

from unittest.mock import MagicMock, call, patch

from forge.core.daemon_executor import ExecutorMixin


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Return a fake CompletedProcess-like mock."""
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


class TestRebaseWorktree:
    """ExecutorMixin._rebase_worktree() rebases worktree onto pipeline branch."""

    def _make_mixin(self):
        mixin = ExecutorMixin()
        return mixin

    def test_rebase_success(self):
        """Successful rebase calls git rebase and does not abort."""
        mixin = self._make_mixin()
        rebase_ok = _make_proc(returncode=0)

        with patch("forge.core.daemon_executor.subprocess.run", return_value=rebase_ok) as mock_run:
            mixin._rebase_worktree("/wt/task-1", "forge/pipeline-abc", "task-1")

        assert mock_run.call_count == 1
        assert mock_run.call_args == call(
            ["git", "rebase", "forge/pipeline-abc"],
            cwd="/wt/task-1",
            capture_output=True,
            text=True,
        )

    def test_rebase_conflict_aborts(self):
        """Failed rebase triggers git rebase --abort."""
        mixin = self._make_mixin()
        rebase_fail = _make_proc(returncode=1, stdout="CONFLICT")
        abort_ok = _make_proc(returncode=0)

        with patch("forge.core.daemon_executor.subprocess.run", side_effect=[rebase_fail, abort_ok]) as mock_run:
            # Should not raise
            mixin._rebase_worktree("/wt/task-1", "forge/pipeline-abc", "task-1")

        assert mock_run.call_count == 2
        # Second call should be abort
        assert mock_run.call_args_list[1] == call(
            ["git", "rebase", "--abort"],
            cwd="/wt/task-1",
            capture_output=True,
        )

    def test_rebase_does_not_raise(self):
        """Even if abort also fails, no exception propagates."""
        mixin = self._make_mixin()
        rebase_fail = _make_proc(returncode=1)
        abort_fail = _make_proc(returncode=1)

        with patch("forge.core.daemon_executor.subprocess.run", side_effect=[rebase_fail, abort_fail]):
            # Should not raise
            mixin._rebase_worktree("/wt/task-1", "main", "task-1")


class TestBuildPrompt:
    """ExecutorMixin._build_prompt() selects the right prompt template."""

    def _make_mixin(self):
        return ExecutorMixin()

    def test_initial_task_uses_agent_prompt(self):
        """First run (retry_count=0) uses _build_agent_prompt."""
        mixin = self._make_mixin()
        task = MagicMock()
        task.retry_count = 0
        task.title = "Add login"
        task.description = "JWT auth"
        task.files = ["auth.py"]
        task.review_feedback = None

        prompt = mixin._build_prompt(task)

        assert "Task: Add login" in prompt
        assert "RETRY" not in prompt

    def test_retry_with_feedback_uses_retry_prompt(self):
        """Retry with feedback uses _build_retry_prompt."""
        mixin = self._make_mixin()
        task = MagicMock()
        task.retry_count = 1
        task.id = "task-1"
        task.title = "Add login"
        task.description = "JWT auth"
        task.files = ["auth.py"]
        task.review_feedback = "Missing error handling"

        prompt = mixin._build_prompt(task)

        assert "RETRY #1" in prompt
        assert "Missing error handling" in prompt

    def test_retry_without_feedback_uses_agent_prompt(self):
        """Retry without review_feedback falls back to initial prompt."""
        mixin = self._make_mixin()
        task = MagicMock()
        task.retry_count = 1
        task.title = "Add login"
        task.description = "JWT auth"
        task.files = ["auth.py"]
        task.review_feedback = None

        prompt = mixin._build_prompt(task)

        assert "RETRY" not in prompt
