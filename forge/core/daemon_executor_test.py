"""Tests for daemon_executor — worktree rebase and prompt selection."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch

from forge.core.daemon_executor import ExecutorMixin, _complexity_timeout


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


class TestComplexityTimeout:
    """_complexity_timeout() scales base timeout by task complexity."""

    def test_complexity_timeout_low(self):
        assert _complexity_timeout(600, "low") == 600

    def test_complexity_timeout_medium(self):
        assert _complexity_timeout(600, "medium") == 900

    def test_complexity_timeout_high(self):
        assert _complexity_timeout(600, "high") == 1200

    def test_complexity_timeout_unknown_defaults_to_medium(self):
        assert _complexity_timeout(600, "unknown") == 900

    def test_complexity_timeout_none_defaults_to_medium(self):
        assert _complexity_timeout(600, None) == 900

    def test_complexity_timeout_respects_base(self):
        assert _complexity_timeout(300, "high") == 600


class TestDeliverInterjections:
    """ExecutorMixin._deliver_interjections() delivers pending human messages."""

    @pytest.mark.asyncio
    async def test_deliver_interjections_resumes_agent(self):
        """Pending interjections should be delivered via agent resume."""
        executor = ExecutorMixin.__new__(ExecutorMixin)

        mock_db = AsyncMock()
        mock_ij = MagicMock()
        mock_ij.id = "ij1"
        mock_ij.message = "Use factory pattern"
        mock_db.get_pending_interjections = AsyncMock(
            side_effect=[[mock_ij], []]  # First call returns interjection, second empty
        )
        mock_db.mark_interjection_delivered = AsyncMock()

        agent_result = MagicMock()
        agent_result.session_id = "sess-2"
        agent_result.summary = "Adjusted to use factory pattern"

        executor._run_agent = AsyncMock(return_value=agent_result)

        delivered, session_id = await executor._deliver_interjections(
            db=mock_db, runtime=MagicMock(), worktree_mgr=MagicMock(),
            task_id="t1", task=MagicMock(), agent_id="a1",
            worktree_path="/tmp/wt", pipeline_id="pipe1",
            session_id="sess-1", pipeline_branch="main",
        )

        assert delivered is True
        assert session_id == "sess-2"
        mock_db.mark_interjection_delivered.assert_called_once_with("ij1")
        # Verify _run_agent was called with resume=sess-1 and prompt_override
        call_kwargs = executor._run_agent.call_args.kwargs
        assert call_kwargs.get("resume") == "sess-1"
        assert "Human message:" in call_kwargs.get("prompt_override", "")

    @pytest.mark.asyncio
    async def test_no_interjections_returns_false(self):
        """No pending interjections should return (False, original_session)."""
        executor = ExecutorMixin.__new__(ExecutorMixin)

        mock_db = AsyncMock()
        mock_db.get_pending_interjections = AsyncMock(return_value=[])

        delivered, session_id = await executor._deliver_interjections(
            db=mock_db, runtime=MagicMock(), worktree_mgr=MagicMock(),
            task_id="t1", task=MagicMock(), agent_id="a1",
            worktree_path="/tmp/wt", pipeline_id="pipe1",
            session_id="sess-1",
        )

        assert delivered is False
        assert session_id == "sess-1"
