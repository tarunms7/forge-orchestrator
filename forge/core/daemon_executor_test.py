"""Tests for daemon_executor — worktree rebase and prompt selection."""

import subprocess

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from forge.core.daemon_executor import ExecutorMixin, _complexity_timeout


def _make_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    """Return a fake CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.mark.asyncio
class TestRebaseWorktree:
    """ExecutorMixin._rebase_worktree() rebases worktree onto pipeline branch."""

    def _make_mixin(self):
        mixin = ExecutorMixin()
        return mixin

    async def test_rebase_success(self):
        """Successful rebase calls git rebase and does not abort."""
        mixin = self._make_mixin()
        rebase_ok = _make_proc(returncode=0)

        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, return_value=rebase_ok) as mock_git:
            await mixin._rebase_worktree("/wt/task-1", "forge/pipeline-abc", "task-1")

        assert mock_git.call_count == 1
        args, kwargs = mock_git.call_args
        assert args[0] == ["rebase", "forge/pipeline-abc"]
        assert kwargs["cwd"] == "/wt/task-1"

    async def test_rebase_conflict_aborts(self):
        """Failed rebase triggers git rebase --abort."""
        mixin = self._make_mixin()
        rebase_fail = _make_proc(returncode=1, stdout="CONFLICT")
        abort_ok = _make_proc(returncode=0)

        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, side_effect=[rebase_fail, abort_ok]) as mock_git:
            await mixin._rebase_worktree("/wt/task-1", "forge/pipeline-abc", "task-1")

        assert mock_git.call_count == 2
        # Second call should be abort
        args, kwargs = mock_git.call_args_list[1]
        assert args[0] == ["rebase", "--abort"]
        assert kwargs["cwd"] == "/wt/task-1"

    async def test_rebase_does_not_raise(self):
        """Even if abort also fails, no exception propagates."""
        mixin = self._make_mixin()
        rebase_fail = _make_proc(returncode=1)
        abort_fail = _make_proc(returncode=1)

        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, side_effect=[rebase_fail, abort_fail]):
            # Should not raise
            await mixin._rebase_worktree("/wt/task-1", "main", "task-1")


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


@pytest.mark.asyncio
class TestAutoCommitIfNeeded:
    """ExecutorMixin._auto_commit_if_needed() commits uncommitted agent changes."""

    async def test_no_uncommitted_changes(self):
        """Clean worktree returns False without committing."""
        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _make_proc(stdout="", returncode=0)
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is False
        # Only status check, no add/commit
        assert mock_git.call_count == 1

    async def test_uncommitted_changes_committed(self):
        """Uncommitted changes are staged and committed."""
        status_result = _make_proc(stdout="M  src/main.py\n?? src/new.py", returncode=0)
        add_result = _make_proc(returncode=0)
        commit_result = _make_proc(returncode=0)

        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, side_effect=[status_result, add_result, commit_result]) as mock_git:
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is True
        assert mock_git.call_count == 3
        # Verify: status, add -A, commit
        calls = mock_git.call_args_list
        assert calls[0].args[0] == ["status", "--porcelain"]
        assert calls[1].args[0] == ["add", "-A"]
        assert calls[2].args[0][0] == "commit"

    async def test_git_add_failure_returns_false(self):
        """If git add fails, no commit is attempted."""
        status_result = _make_proc(stdout="M  src/main.py", returncode=0)
        add_fail = _make_proc(returncode=1, stdout="error: ...")

        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, side_effect=[status_result, add_fail]) as mock_git:
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is False
        assert mock_git.call_count == 2  # status + add, no commit

    async def test_git_commit_failure_returns_false(self):
        """If git commit fails, returns False."""
        status_result = _make_proc(stdout="M  src/main.py", returncode=0)
        add_ok = _make_proc(returncode=0)
        commit_fail = _make_proc(returncode=1, stdout="error: ...")

        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, side_effect=[status_result, add_ok, commit_fail]) as mock_git:
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is False
        assert mock_git.call_count == 3

    async def test_status_command_failure_returns_false(self):
        """If git status fails (not a git repo), returns False."""
        with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _make_proc(returncode=128, stdout="")
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is False


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
