"""Tests for daemon_executor — worktree rebase and prompt selection."""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.adapter import AgentResult
from forge.core.daemon_executor import ExecutorMixin, _complexity_timeout
from forge.merge.worker import MergeResult
from forge.review.pipeline import GateResult


def _make_proc(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
    """Return a fake CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
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

        with patch.object(ExecutorMixin, "_ensure_clean_for_rebase", new_callable=AsyncMock):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
                return_value=rebase_ok,
            ) as mock_git:
                await mixin._rebase_worktree("/wt/task-1", "forge/pipeline-abc", "task-1")

        assert mock_git.call_count == 1
        args, kwargs = mock_git.call_args
        assert args[0] == ["rebase", "forge/pipeline-abc"]
        assert kwargs["cwd"] == "/wt/task-1"

    async def test_rebase_conflict_retries_with_theirs(self):
        """Failed clean rebase aborts and retries with -X theirs."""
        mixin = self._make_mixin()
        rebase_fail = _make_proc(returncode=1, stdout="CONFLICT")
        abort_ok = _make_proc(returncode=0)
        rebase_theirs_ok = _make_proc(returncode=0)

        with patch.object(ExecutorMixin, "_ensure_clean_for_rebase", new_callable=AsyncMock):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
                side_effect=[rebase_fail, abort_ok, rebase_theirs_ok],
            ) as mock_git:
                await mixin._rebase_worktree("/wt/task-1", "forge/pipeline-abc", "task-1")

        assert mock_git.call_count == 3
        # 1st: clean rebase, 2nd: abort, 3rd: rebase -X theirs
        assert mock_git.call_args_list[0].args[0] == ["rebase", "forge/pipeline-abc"]
        assert mock_git.call_args_list[1].args[0] == ["rebase", "--abort"]
        assert mock_git.call_args_list[2].args[0] == [
            "rebase",
            "-X",
            "theirs",
            "forge/pipeline-abc",
        ]

    async def test_rebase_all_attempts_fail_does_not_raise(self):
        """If both clean and -X theirs rebase fail, no exception propagates."""
        mixin = self._make_mixin()
        rebase_fail = _make_proc(returncode=1)
        abort_ok = _make_proc(returncode=0)
        rebase_theirs_fail = _make_proc(returncode=1)
        abort_ok2 = _make_proc(returncode=0)

        with patch.object(ExecutorMixin, "_ensure_clean_for_rebase", new_callable=AsyncMock):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
                side_effect=[rebase_fail, abort_ok, rebase_theirs_fail, abort_ok2],
            ) as mock_git:
                # Should not raise
                await mixin._rebase_worktree("/wt/task-1", "main", "task-1")

        assert mock_git.call_count == 4


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

        assert "Retry #1" in prompt
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

        with patch(
            "forge.core.daemon_executor._run_git",
            new_callable=AsyncMock,
            side_effect=[status_result, add_result, commit_result],
        ) as mock_git:
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is True
        assert mock_git.call_count == 3
        # Verify: status, add -A, commit
        calls = mock_git.call_args_list
        assert calls[0].args[0] == ["status", "--porcelain"]
        assert calls[1].args[0][:2] == ["add", "-A"]
        assert calls[2].args[0][0] == "commit"

    async def test_git_add_failure_returns_false(self):
        """If git add fails, no commit is attempted."""
        status_result = _make_proc(stdout="M  src/main.py", returncode=0)
        add_fail = _make_proc(returncode=1, stdout="error: ...")

        with patch(
            "forge.core.daemon_executor._run_git",
            new_callable=AsyncMock,
            side_effect=[status_result, add_fail],
        ) as mock_git:
            result = await ExecutorMixin._auto_commit_if_needed("/wt/task-1", "task-1")

        assert result is False
        assert mock_git.call_count == 2  # status + add, no commit

    async def test_git_commit_failure_returns_false(self):
        """If git commit fails, returns False."""
        status_result = _make_proc(stdout="M  src/main.py", returncode=0)
        add_ok = _make_proc(returncode=0)
        commit_fail = _make_proc(returncode=1, stdout="error: ...")

        with patch(
            "forge.core.daemon_executor._run_git",
            new_callable=AsyncMock,
            side_effect=[status_result, add_ok, commit_fail],
        ) as mock_git:
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
            db=mock_db,
            runtime=MagicMock(),
            worktree_mgr=MagicMock(),
            task_id="t1",
            task=MagicMock(),
            agent_id="a1",
            worktree_path="/tmp/wt",
            pipeline_id="pipe1",
            session_id="sess-1",
            pipeline_branch="main",
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
            db=mock_db,
            runtime=MagicMock(),
            worktree_mgr=MagicMock(),
            task_id="t1",
            task=MagicMock(),
            agent_id="a1",
            worktree_path="/tmp/wt",
            pipeline_id="pipe1",
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
        mock_lock = MagicMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=False)
        mixin._merge_lock = mock_lock
        mixin._get_merge_lock = MagicMock(return_value=mock_lock)
        # _worktree_path is provided by ForgeDaemon at runtime; mock it here
        mixin._worktree_path = MagicMock(
            side_effect=lambda repo_id, task_id: (
                f"/fake/project/.forge/worktrees/{repo_id}/{task_id}"
            ),
        )
        return mixin

    async def test_prepare_worktree_uses_worktree_path_on_reuse(self):
        """When worktree_mgr.create raises ValueError, _prepare_worktree falls back
        to self._worktree_path(repo_id, task_id) instead of hardcoded os.path.join."""
        mixin = self._make_mixin()

        worktree_mgr = MagicMock()
        worktree_mgr.async_create = AsyncMock(side_effect=ValueError("already exists"))

        db = AsyncMock()
        db.update_task_state = AsyncMock()

        # Simulate the worktree directory existing on disk
        with patch("os.path.isdir", return_value=True):
            with patch.object(mixin, "_rebase_worktree", new_callable=AsyncMock):
                with patch(
                    "forge.core.daemon_executor._run_git",
                    new_callable=AsyncMock,
                    return_value=_make_proc(returncode=0),
                ):
                    result = await mixin._prepare_worktree(
                        worktree_mgr,
                        "task-1",
                        "pipe-1",
                        db,
                        base_ref="main",
                        repo_id="backend",
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
                db,
                merge_worker,
                worktree_mgr,
                task,
                "task-1",
                "agent-1",
                "pipe-1",
                repo_id="frontend",
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
                db,
                runtime,
                worktree_mgr,
                merge_worker,
                "task-1",
                "agent-1",
                "answer text",
                "pipe-1",
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
        worktree_mgr.async_create = AsyncMock(side_effect=ValueError("exists"))

        merge_worker = MagicMock()
        merge_worker._main = "main"

        # Worktree doesn't exist → will go to error path and release agent
        with patch("os.path.isdir", return_value=False):
            await mixin._execute_task(
                db,
                MagicMock(),
                worktree_mgr,
                merge_worker,
                task_id="task-1",
                agent_id="agent-1",
                pipeline_id="pipe-1",
                repo_id="default",  # will be overridden by task.repo_id="backend"
            )

        # _worktree_path should have been called with the DB task's repo_id
        mixin._worktree_path.assert_called_once_with("backend", "task-1")

    async def test_default_repo_id(self):
        """When no repo_id provided, defaults to 'default'."""
        mixin = self._make_mixin()

        worktree_mgr = MagicMock()
        worktree_mgr.async_create = AsyncMock(side_effect=ValueError("exists"))

        db = AsyncMock()
        db.update_task_state = AsyncMock()

        with patch("os.path.isdir", return_value=True):
            with patch.object(mixin, "_rebase_worktree", new_callable=AsyncMock):
                with patch(
                    "forge.core.daemon_executor._run_git",
                    new_callable=AsyncMock,
                    return_value=_make_proc(returncode=0),
                ):
                    await mixin._prepare_worktree(
                        worktree_mgr,
                        "task-1",
                        "pipe-1",
                        db,
                        base_ref="main",
                    )

        # Default repo_id is 'default'
        mixin._worktree_path.assert_called_once_with("default", "task-1")


@pytest.mark.asyncio
class TestHandleReviewAnswer:
    """Review escalation answers should resume the correct post-review path."""

    def _make_mixin(self):
        mixin = ExecutorMixin()
        mixin._strategy = "auto"
        mixin._merge_worker = MagicMock()
        mixin._worktree_mgr = MagicMock()
        mixin._handle_merge_fast_path = AsyncMock()
        mixin._handle_retry = AsyncMock()
        return mixin

    async def test_approve_routes_to_merge_fast_path(self):
        mixin = self._make_mixin()
        db = AsyncMock()
        task = MagicMock()
        task.repo_id = "backend"
        db.get_task = AsyncMock(return_value=task)

        await mixin._handle_review_answer(
            db,
            "task-1",
            "agent-1",
            "Approve - the reviewer is overthinking it",
            "pipe-1",
        )

        mixin._handle_merge_fast_path.assert_awaited_once_with(
            db,
            mixin._merge_worker,
            mixin._worktree_mgr,
            task,
            "task-1",
            "agent-1",
            "pipe-1",
            repo_id="backend",
        )

    async def test_approve_releases_agent_when_task_missing(self):
        mixin = self._make_mixin()
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=None)
        db.release_agent = AsyncMock()

        await mixin._handle_review_answer(db, "task-1", "agent-1", "approve", "pipe-1")

        db.release_agent.assert_awaited_once_with("agent-1")
        mixin._handle_merge_fast_path.assert_not_called()


@pytest.mark.asyncio
class TestAttemptMergeLockBehavior:
    """_attempt_merge holds _merge_lock for the full first-attempt + retry sequence."""

    def _make_mixin(self):
        mixin = ExecutorMixin()
        mixin._project_dir = "/fake/project"
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock(allowed_dirs=[], require_approval=False, budget_limit_usd=0.0)
        mixin._template_config = None
        mixin._contracts = None
        mock_lock = MagicMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=False)
        mixin._merge_lock = mock_lock  # backward compat
        mixin._get_merge_lock = MagicMock(return_value=mock_lock)
        mixin._worktree_path = MagicMock(
            side_effect=lambda repo_id, task_id: (
                f"/fake/project/.forge/worktrees/{repo_id}/{task_id}"
            ),
        )
        return mixin

    async def test_lock_acquired_once_on_first_merge_success(self):
        """When the first merge attempt succeeds, _get_merge_lock is entered exactly once."""
        mixin = self._make_mixin()
        mixin._emit = AsyncMock()
        mixin._run_review = AsyncMock(return_value=(True, None, False))
        mixin._ensure_clean_for_rebase = AsyncMock()
        mixin._emit_merge_success = AsyncMock()
        mixin._emit_merge_failure = AsyncMock()

        task = MagicMock()
        task.retry_count = 0
        task.complexity = "medium"

        db = AsyncMock()
        db.update_task_state = AsyncMock()
        db.set_task_review_diff = AsyncMock()
        db.get_pipeline = AsyncMock(
            return_value=MagicMock(
                require_approval=False,
                build_cmd=None,
                test_cmd=None,
            )
        )

        merge_worker = MagicMock()
        merge_worker._main = "main"
        merge_worker.merge = AsyncMock(return_value=MergeResult(success=True))
        merge_worker.retry_merge = AsyncMock()

        worktree_mgr = MagicMock()

        with patch(
            "forge.core.daemon_executor._get_diff_vs_main", new=AsyncMock(return_value="diff")
        ):
            with patch(
                "forge.core.daemon_executor._resolve_ref", new=AsyncMock(return_value="abc123")
            ):
                await mixin._attempt_merge(
                    db,
                    merge_worker,
                    worktree_mgr,
                    task,
                    "task-1",
                    "agent-1",
                    "/wt/task-1",
                    "claude-3-5-sonnet-20241022",
                    "pipe-1",
                )

        # Lock entered exactly once
        assert mixin._get_merge_lock.return_value.__aenter__.call_count == 1
        # retry_merge never called because first attempt succeeded
        merge_worker.retry_merge.assert_not_called()
        mixin._emit_merge_success.assert_called_once()

    async def test_lock_acquired_once_when_first_merge_fails_and_retry_succeeds(self):
        """When the first merge fails and retry succeeds, _merge_lock is still entered only once."""
        mixin = self._make_mixin()
        mixin._emit = AsyncMock()
        mixin._run_review = AsyncMock(return_value=(True, None, False))
        mixin._ensure_clean_for_rebase = AsyncMock()
        mixin._emit_merge_success = AsyncMock()
        mixin._emit_merge_failure = AsyncMock()

        task = MagicMock()
        task.retry_count = 0
        task.complexity = "medium"

        db = AsyncMock()
        db.update_task_state = AsyncMock()
        db.set_task_review_diff = AsyncMock()
        db.get_pipeline = AsyncMock(
            return_value=MagicMock(
                require_approval=False,
                build_cmd=None,
                test_cmd=None,
            )
        )

        merge_worker = MagicMock()
        merge_worker._main = "main"
        # First attempt fails
        merge_worker.merge = AsyncMock(
            return_value=MergeResult(
                success=False,
                error="rebase conflict",
            )
        )
        # Retry succeeds
        merge_worker.retry_merge = AsyncMock(return_value=MergeResult(success=True))

        worktree_mgr = MagicMock()

        with patch(
            "forge.core.daemon_executor._get_diff_vs_main", new=AsyncMock(return_value="diff")
        ):
            with patch(
                "forge.core.daemon_executor._resolve_ref", new=AsyncMock(return_value="abc123")
            ):
                await mixin._attempt_merge(
                    db,
                    merge_worker,
                    worktree_mgr,
                    task,
                    "task-1",
                    "agent-1",
                    "/wt/task-1",
                    "claude-3-5-sonnet-20241022",
                    "pipe-1",
                )

        # Lock entered exactly ONCE even though merge failed and retry was needed
        assert mixin._get_merge_lock.return_value.__aenter__.call_count == 1
        # Both merge and retry_merge were called
        merge_worker.merge.assert_called_once()
        merge_worker.retry_merge.assert_called_once()
        # Success emitted for the retry
        mixin._emit_merge_success.assert_called_once()
        # Failure was also emitted (for the first failed attempt)
        mixin._emit_merge_failure.assert_called_once()

    async def test_lock_acquired_once_when_both_attempts_fail(self):
        """When both merge attempts fail, _merge_lock is still entered only once."""
        mixin = self._make_mixin()
        mixin._emit = AsyncMock()
        mixin._run_review = AsyncMock(return_value=(True, None, False))
        mixin._ensure_clean_for_rebase = AsyncMock()
        mixin._emit_merge_success = AsyncMock()
        mixin._emit_merge_failure = AsyncMock()
        mixin._attempt_tier2_resolution = AsyncMock()

        task = MagicMock()
        task.retry_count = 0
        task.complexity = "medium"

        db = AsyncMock()
        db.update_task_state = AsyncMock()
        db.set_task_review_diff = AsyncMock()
        db.get_pipeline = AsyncMock(
            return_value=MagicMock(
                require_approval=False,
                build_cmd=None,
                test_cmd=None,
            )
        )

        merge_worker = MagicMock()
        merge_worker._main = "main"
        merge_worker.merge = AsyncMock(
            return_value=MergeResult(
                success=False,
                error="rebase conflict",
            )
        )
        merge_worker.retry_merge = AsyncMock(
            return_value=MergeResult(
                success=False,
                error="still conflicting",
            )
        )

        worktree_mgr = MagicMock()

        with patch(
            "forge.core.daemon_executor._get_diff_vs_main", new=AsyncMock(return_value="diff")
        ):
            with patch(
                "forge.core.daemon_executor._resolve_ref", new=AsyncMock(return_value="abc123")
            ):
                await mixin._attempt_merge(
                    db,
                    merge_worker,
                    worktree_mgr,
                    task,
                    "task-1",
                    "agent-1",
                    "/wt/task-1",
                    "claude-3-5-sonnet-20241022",
                    "pipe-1",
                )

        # Lock still entered exactly once
        assert mixin._get_merge_lock.return_value.__aenter__.call_count == 1
        # Tier 2 resolution was triggered
        mixin._attempt_tier2_resolution.assert_called_once()
        mixin._emit_merge_success.assert_not_called()

    async def test_merge_progress_records_health_activity(self):
        """Merge progress should count as activity so the health monitor doesn't false-alarm."""
        mixin = self._make_mixin()
        mixin._emit = AsyncMock()
        mixin._run_review = AsyncMock(return_value=(True, None, False))
        mixin._ensure_clean_for_rebase = AsyncMock()
        mixin._emit_merge_success = AsyncMock()
        mixin._emit_merge_failure = AsyncMock()
        mixin._health_monitor = MagicMock()

        task = MagicMock()
        task.retry_count = 0
        task.complexity = "medium"

        db = AsyncMock()
        db.update_task_state = AsyncMock()
        db.set_task_review_diff = AsyncMock()
        db.get_pipeline = AsyncMock(
            return_value=MagicMock(require_approval=False, build_cmd=None, test_cmd=None)
        )
        db.set_task_timing = AsyncMock()

        merge_worker = MagicMock()
        merge_worker._main = "main"
        merge_worker.merge = AsyncMock(return_value=MergeResult(success=True))
        merge_worker.retry_merge = AsyncMock()

        with (
            patch(
                "forge.core.daemon_executor._get_diff_vs_main", new=AsyncMock(return_value="diff")
            ),
            patch("forge.core.daemon_executor._resolve_ref", new=AsyncMock(return_value="abc123")),
        ):
            await mixin._attempt_merge(
                db,
                merge_worker,
                MagicMock(),
                task,
                "task-1",
                "agent-1",
                "/wt/task-1",
                "claude-3-5-sonnet-20241022",
                "pipe-1",
            )

        mixin._health_monitor.record_task_activity.assert_called_with("task-1")


# ---------------------------------------------------------------------------
# Tests for resume-aware execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteTaskReviewOnly:
    """ExecutorMixin._execute_task_review_only() for resumed in_review tasks."""

    def _make_executor(self):
        executor = ExecutorMixin.__new__(ExecutorMixin)
        executor._emit = AsyncMock()
        executor._strategy = "balanced"
        executor._review_phase_emitted = False
        executor._merging_phase_emitted = False
        executor._repos = {"default": MagicMock(path="/project")}
        return executor

    def _make_task(self, state="in_review", task_id="task-1"):
        task = MagicMock()
        task.id = task_id
        task.state = state
        task.title = "Test task"
        task.complexity = "medium"
        task.retry_count = 0
        task.repo_id = "default"
        task.files = ["main.py"]
        task.review_feedback = None
        return task

    async def test_missing_worktree_resets_to_todo(self):
        """When worktree doesn't exist, task is reset to TODO."""
        executor = self._make_executor()
        db = AsyncMock()
        task = self._make_task()
        db.get_task = AsyncMock(return_value=task)
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        db.log_event = AsyncMock()

        merge_worker = MagicMock()
        merge_worker._main = "forge/pipeline-abc"
        worktree_mgr = MagicMock()

        executor._worktree_path = MagicMock(return_value="/nonexistent/worktree")

        with patch("os.path.isdir", return_value=False):
            await executor._execute_task_review_only(
                db,
                merge_worker,
                worktree_mgr,
                "task-1",
                "agent-1",
                pipeline_id="pipe-1",
                repo_id="default",
            )

        db.update_task_state.assert_called_with("task-1", "todo")
        db.release_agent.assert_called_once_with("agent-1")

    async def test_review_only_dispatches_to_attempt_merge(self):
        """With valid worktree, dispatches to _attempt_merge (review→merge)."""
        executor = self._make_executor()
        db = AsyncMock()
        task = self._make_task()
        db.get_task = AsyncMock(return_value=task)
        db.log_event = AsyncMock()
        db.set_task_timing = AsyncMock()

        merge_worker = MagicMock()
        merge_worker._main = "forge/pipeline-abc"
        worktree_mgr = MagicMock()

        executor._worktree_path = MagicMock(return_value="/wt/task-1")
        executor._attempt_merge = AsyncMock()
        executor._cleanup_and_release = AsyncMock()

        with patch("os.path.isdir", return_value=True):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
                return_value=_make_proc(returncode=0),
            ):
                await executor._execute_task_review_only(
                    db,
                    merge_worker,
                    worktree_mgr,
                    "task-1",
                    "agent-1",
                    pipeline_id="pipe-1",
                    repo_id="default",
                )

        executor._attempt_merge.assert_called_once()
        executor._cleanup_and_release.assert_called_once()

    async def test_rebase_abort_on_review_only(self):
        """Interrupted rebase is cleaned up before review."""
        executor = self._make_executor()
        db = AsyncMock()
        task = self._make_task()
        db.get_task = AsyncMock(return_value=task)
        db.log_event = AsyncMock()
        db.set_task_timing = AsyncMock()

        merge_worker = MagicMock()
        merge_worker._main = "forge/pipeline-abc"
        worktree_mgr = MagicMock()

        executor._worktree_path = MagicMock(return_value="/wt/task-1")
        executor._attempt_merge = AsyncMock()
        executor._cleanup_and_release = AsyncMock()

        def isdir_side_effect(path):
            if path == "/wt/task-1":
                return True
            if path.endswith("rebase-merge"):
                return True
            return False

        with patch("os.path.isdir", side_effect=isdir_side_effect):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
                return_value=_make_proc(returncode=0),
            ) as mock_git:
                await executor._execute_task_review_only(
                    db,
                    merge_worker,
                    worktree_mgr,
                    "task-1",
                    "agent-1",
                    pipeline_id="pipe-1",
                    repo_id="default",
                )

        # Verify rebase --abort was called
        abort_calls = [c for c in mock_git.call_args_list if c.args[0] == ["rebase", "--abort"]]
        assert len(abort_calls) == 1

    async def test_missing_task_releases_agent(self):
        """When task doesn't exist in DB, agent is released immediately."""
        executor = self._make_executor()
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=None)
        db.release_agent = AsyncMock()

        await executor._execute_task_review_only(
            db,
            MagicMock(),
            MagicMock(),
            "task-1",
            "agent-1",
            pipeline_id="pipe-1",
        )

        db.release_agent.assert_called_once_with("agent-1")


@pytest.mark.asyncio
class TestPrepareWorktreeRebaseAbort:
    """_prepare_worktree() cleans up interrupted rebase."""

    async def test_rebase_abort_when_rebase_merge_exists(self):
        """Interrupted rebase-merge is aborted before reusing worktree."""
        executor = ExecutorMixin.__new__(ExecutorMixin)
        executor._repos = {"default": MagicMock(path="/project")}
        executor._emit = AsyncMock()

        def isdir_side_effect(path):
            if path == "/project/.forge/worktrees/task-1":
                return True
            if path.endswith("rebase-merge"):
                return True
            return False

        worktree_mgr = MagicMock()
        worktree_mgr.async_create = AsyncMock(side_effect=ValueError("exists"))

        executor._worktree_path = MagicMock(return_value="/project/.forge/worktrees/task-1")
        executor._rebase_worktree = AsyncMock()

        with patch("os.path.isdir", side_effect=isdir_side_effect):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
                return_value=_make_proc(returncode=0),
            ) as mock_git:
                result = await executor._prepare_worktree(
                    worktree_mgr,
                    "task-1",
                    "pipe-1",
                    AsyncMock(),
                    base_ref="main",
                    repo_id="default",
                )

        assert result == "/project/.forge/worktrees/task-1"
        # Verify rebase --abort was called
        abort_calls = [c for c in mock_git.call_args_list if c.args[0] == ["rebase", "--abort"]]
        assert len(abort_calls) == 1

    async def test_no_rebase_abort_when_clean(self):
        """No rebase abort when worktree is clean (no rebase-merge dir)."""
        executor = ExecutorMixin.__new__(ExecutorMixin)
        executor._repos = {"default": MagicMock(path="/project")}
        executor._emit = AsyncMock()

        def isdir_side_effect(path):
            if path == "/project/.forge/worktrees/task-1":
                return True
            return False

        worktree_mgr = MagicMock()
        worktree_mgr.async_create = AsyncMock(side_effect=ValueError("exists"))

        executor._worktree_path = MagicMock(return_value="/project/.forge/worktrees/task-1")
        executor._rebase_worktree = AsyncMock()

        with patch("os.path.isdir", side_effect=isdir_side_effect):
            with patch(
                "forge.core.daemon_executor._run_git",
                new_callable=AsyncMock,
            ) as mock_git:
                result = await executor._prepare_worktree(
                    worktree_mgr,
                    "task-1",
                    "pipe-1",
                    AsyncMock(),
                    base_ref="main",
                    repo_id="default",
                )

        assert result == "/project/.forge/worktrees/task-1"
        # _run_git should NOT have been called for rebase abort
        for call in mock_git.call_args_list:
            assert call.args[0] != ["rebase", "--abort"]


@pytest.mark.asyncio
class TestStreamAgentAllowedDirs:
    async def test_stream_agent_uses_build_allowed_dirs_when_available(self):
        mixin = ExecutorMixin()
        mixin._settings = MagicMock(
            allowed_dirs=["/settings-only"],
            agent_timeout_seconds=600,
            autonomy="balanced",
            question_limit=3,
            agent_max_turns=75,
            test_cmd=None,
            build_cmd=None,
            lint_cmd=None,
            lint_fix_cmd=None,
        )
        mixin._settings.resolve_reasoning_effort = MagicMock(return_value="medium")
        mixin._project_dir = "/workspace/root"
        mixin._snapshot = None
        mixin._contracts = None
        mixin._template_config = None
        mixin._emit = AsyncMock()
        mixin._build_project_context = MagicMock(return_value="")
        mixin._build_allowed_dirs = MagicMock(
            return_value=["/workspace/root/backend", "/workspace/root/frontend"]
        )

        db = AsyncMock()
        db.get_pipeline = AsyncMock(return_value=None)
        db.get_relevant_lessons = AsyncMock(return_value=[])
        db.get_pipeline_contracts = AsyncMock(return_value=None)

        runtime = MagicMock()
        runtime.run_task = AsyncMock(
            return_value=AgentResult(
                success=True,
                files_changed=[],
                summary="done",
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
            )
        )

        task = MagicMock()
        task.files = ["backend/app.py"]
        task.depends_on = []
        task.complexity = "medium"

        result = await mixin._stream_agent(
            runtime,
            "agent-1",
            "test prompt",
            "/workspace/root/.forge/worktrees/backend/task-1",
            task,
            "task-1",
            "pipe-1",
            db,
            "claude:sonnet",
        )

        assert result.success is True
        runtime.run_task.assert_awaited_once()
        assert runtime.run_task.await_args.kwargs["allowed_dirs"] == [
            "/workspace/root/backend",
            "/workspace/root/frontend",
        ]


@pytest.mark.asyncio
async def test_scope_clean_noop_marks_done_when_scoped_tests_pass():
    """A stale task should resolve as done when scoped verification is already green."""
    mixin = ExecutorMixin()
    mixin._settings = MagicMock(agent_timeout_seconds=600)
    mixin._pipeline_test_cmd = None
    mixin._resolve_test_cmd = MagicMock(return_value="pytest -q")
    mixin._gate_test = AsyncMock(
        return_value=GateResult(passed=True, gate="gate1_5_test", details="ok")
    )
    mixin._emit = AsyncMock()

    db = AsyncMock()
    db.get_pipeline = AsyncMock(return_value=MagicMock(test_cmd="pytest -q"))
    task = MagicMock()
    task.id = "t1"
    task.files = ["forge/agents/adapter.py", "forge/agents/adapter_test.py"]
    task.repo_id = "default"

    accepted = await mixin._maybe_accept_scope_clean_noop(
        db,
        task,
        "/wt/task-1",
        "pipe-1",
        pipeline_branch="forge/pipeline-test",
    )

    assert accepted is True
    mixin._gate_test.assert_awaited_once()
    gate_call = mixin._gate_test.await_args
    assert gate_call.args[:3] == ("/wt/task-1", "pytest -q", 300)
    assert gate_call.kwargs["changed_files"] == task.files
    assert gate_call.kwargs["allowed_files"] == task.files
    assert gate_call.kwargs["pipeline_branch"] == "forge/pipeline-test"
    db.update_task_state.assert_awaited_once_with("t1", "done")
    mixin._emit.assert_awaited_once()
    assert mixin._pipeline_test_cmd is None


@pytest.mark.asyncio
async def test_scope_clean_noop_does_not_mark_done_when_scoped_tests_fail():
    """Failed scoped verification should fall back to the normal retry path."""
    mixin = ExecutorMixin()
    mixin._settings = MagicMock(agent_timeout_seconds=600)
    mixin._pipeline_test_cmd = None
    mixin._resolve_test_cmd = MagicMock(return_value="pytest -q")
    mixin._gate_test = AsyncMock(
        return_value=GateResult(passed=False, gate="gate1_5_test", details="1 failed")
    )
    mixin._emit = AsyncMock()

    db = AsyncMock()
    db.get_pipeline = AsyncMock(return_value=MagicMock(test_cmd="pytest -q"))
    task = MagicMock()
    task.id = "t1"
    task.files = ["forge/tui/screens/pipeline.py"]
    task.repo_id = "default"

    accepted = await mixin._maybe_accept_scope_clean_noop(
        db,
        task,
        "/wt/task-1",
        "pipe-1",
        pipeline_branch="forge/pipeline-test",
    )

    assert accepted is False
    db.update_task_state.assert_not_awaited()
    mixin._emit.assert_not_awaited()
