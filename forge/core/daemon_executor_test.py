"""Tests for daemon_executor — worktree rebase and prompt selection."""

import os
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


@pytest.mark.asyncio
class TestWorktreePathThreading:
    """Verify repo_id is threaded through _execute_task to _prepare_worktree and friends."""

    def _make_mixin(self):
        """Return an ExecutorMixin with required host-class attributes mocked."""
        mixin = ExecutorMixin()
        mixin._project_dir = "/fake/project"
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock(allowed_dirs=[], require_approval=False, budget_limit_usd=0.0)
        mixin._template_config = None
        mixin._contracts = None
        mixin._merge_lock = MagicMock()
        mixin._merge_lock.__aenter__ = AsyncMock(return_value=None)
        mixin._merge_lock.__aexit__ = AsyncMock(return_value=False)
        # _worktree_path is provided by ForgeDaemon at runtime; mock it here
        mixin._worktree_path = MagicMock(
            side_effect=lambda repo_id, task_id: f"/fake/project/.forge/worktrees/{repo_id}/{task_id}",
        )
        return mixin

    async def test_prepare_worktree_uses_worktree_path_on_reuse(self):
        """When worktree_mgr.create raises ValueError, _prepare_worktree falls back
        to self._worktree_path(repo_id, task_id) instead of hardcoded os.path.join."""
        mixin = self._make_mixin()

        worktree_mgr = MagicMock()
        worktree_mgr.create = MagicMock(side_effect=ValueError("already exists"))

        db = AsyncMock()
        db.update_task_state = AsyncMock()

        # Simulate the worktree directory existing on disk
        with patch("os.path.isdir", return_value=True):
            with patch.object(mixin, "_rebase_worktree", new_callable=AsyncMock) as mock_rebase:
                result = await mixin._prepare_worktree(
                    worktree_mgr, "task-1", "pipe-1", db,
                    base_ref="main", repo_id="backend",
                )

        assert result == "/fake/project/.forge/worktrees/backend/task-1"
        mixin._worktree_path.assert_called_once_with("backend", "task-1")

    async def test_handle_merge_fast_path_uses_worktree_path(self):
        """_handle_merge_fast_path uses self._worktree_path(repo_id, task_id)."""
        mixin = self._make_mixin()
        mixin._handle_retry = AsyncMock()
        mixin._emit = AsyncMock()

        task = MagicMock()
        task.complexity = "medium"
        db = AsyncMock()
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        merge_worker = MagicMock()
        merge_worker._main = "main"
        worktree_mgr = MagicMock()

        with patch("os.path.isdir", return_value=False):
            await mixin._handle_merge_fast_path(
                db, merge_worker, worktree_mgr, task,
                "task-1", "agent-1", "pipe-1", repo_id="frontend",
            )

        mixin._worktree_path.assert_called_once_with("frontend", "task-1")

    async def test_resume_task_uses_worktree_path(self):
        """_resume_task uses self._worktree_path(repo_id, task_id)."""
        mixin = self._make_mixin()
        mixin._emit = AsyncMock()
        mixin._handle_retry = AsyncMock()

        db = AsyncMock()
        task = MagicMock()
        task.state = "awaiting_input"
        task.session_id = "sess-1"
        db.get_task = AsyncMock(return_value=task)
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()

        worktree_mgr = MagicMock()
        merge_worker = MagicMock()
        merge_worker._main = "main"
        runtime = MagicMock()

        # Worktree doesn't exist — should fall back to retry
        with patch("os.path.isdir", return_value=False):
            await mixin._resume_task(
                db, runtime, worktree_mgr, merge_worker,
                "task-1", "agent-1", "answer text", "pipe-1",
                repo_id="backend",
            )

        mixin._worktree_path.assert_called_once_with("backend", "task-1")

    async def test_execute_task_reads_repo_id_from_db_task(self):
        """_execute_task reads repo_id from DB task row and threads it to _prepare_worktree."""
        mixin = self._make_mixin()
        mixin._emit = AsyncMock()
        mixin._handle_retry = AsyncMock()

        task = MagicMock()
        task.title = "Test"
        task.retry_count = 0
        task.retry_reason = None
        task.repo_id = "backend"  # stored in DB — should override default

        db = AsyncMock()
        db.get_task = AsyncMock(return_value=task)
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()

        worktree_mgr = MagicMock()
        # Simulate worktree creation failure to trigger _worktree_path fallback
        worktree_mgr.create = MagicMock(side_effect=ValueError("exists"))

        merge_worker = MagicMock()
        merge_worker._main = "main"

        # Worktree doesn't exist → will go to error path and release agent
        with patch("os.path.isdir", return_value=False):
            await mixin._execute_task(
                db, MagicMock(), worktree_mgr, merge_worker,
                task_id="task-1", agent_id="agent-1",
                pipeline_id="pipe-1",
                repo_id="default",  # will be overridden by task.repo_id="backend"
            )

        # _worktree_path should have been called with the DB task's repo_id
        mixin._worktree_path.assert_called_once_with("backend", "task-1")

    async def test_default_repo_id(self):
        """When no repo_id provided, defaults to 'default'."""
        mixin = self._make_mixin()

        worktree_mgr = MagicMock()
        worktree_mgr.create = MagicMock(side_effect=ValueError("exists"))

        db = AsyncMock()
        db.update_task_state = AsyncMock()

        with patch("os.path.isdir", return_value=True):
            with patch.object(mixin, "_rebase_worktree", new_callable=AsyncMock):
                result = await mixin._prepare_worktree(
                    worktree_mgr, "task-1", "pipe-1", db, base_ref="main",
                )

        # Default repo_id is 'default'
        mixin._worktree_path.assert_called_once_with("default", "task-1")
