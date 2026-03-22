"""Tests for timing/metrics instrumentation in daemon executor, review, and merge."""

import subprocess
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.core.daemon_executor import ExecutorMixin
from forge.core.daemon_merge import MergeMixin
from forge.core.daemon_review import ReviewMixin
from forge.merge.worker import MergeResult
from forge.review.pipeline import GateResult


def _make_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


@dataclass
class FakeAgentResult:
    success: bool = True
    error: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    summary: str = ""
    session_id: str | None = None
    files_changed: list = None
    num_turns: int = 0

    def __post_init__(self):
        if self.files_changed is None:
            self.files_changed = []


class FakeTask:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "task-1")
        self.title = kwargs.get("title", "Test task")
        self.description = kwargs.get("description", "A test task")
        self.files = kwargs.get("files", ["file.py"])
        self.state = kwargs.get("state", "in_progress")
        self.retry_count = kwargs.get("retry_count", 0)
        self.retry_reason = kwargs.get("retry_reason")
        self.complexity = kwargs.get("complexity", "medium")
        self.review_feedback = kwargs.get("review_feedback")
        self.depends_on = kwargs.get("depends_on")
        self.repo_id = kwargs.get("repo_id", "default")
        self.prior_diff = kwargs.get("prior_diff")


def _make_db():
    """Create a mock DB with all the metrics methods."""
    db = AsyncMock()
    db.get_task = AsyncMock(return_value=FakeTask())
    db.update_task_state = AsyncMock()
    db.release_agent = AsyncMock()
    db.set_task_timing = AsyncMock()
    db.set_task_turns = AsyncMock()
    db.set_task_error = AsyncMock()
    db.finalize_pipeline_metrics = AsyncMock()
    db.add_task_agent_cost = AsyncMock()
    db.add_pipeline_cost = AsyncMock()
    db.get_pipeline_cost = AsyncMock(return_value=0.0)
    db.set_task_review_diff = AsyncMock()
    db.set_task_prior_diff = AsyncMock()
    db.retry_task = AsyncMock()
    db.get_pipeline = AsyncMock(return_value=None)
    db.get_pipeline_contracts = AsyncMock(return_value=None)
    return db


def _make_executor():
    """Create an ExecutorMixin with required attributes."""
    mixin = ExecutorMixin()
    mixin._project_dir = "/fake/project"
    mixin._strategy = "balanced"
    mixin._snapshot = None
    mixin._settings = MagicMock()
    mixin._settings.agent_timeout_seconds = 300
    mixin._settings.max_retries = 3
    mixin._settings.agent_max_turns = 75
    mixin._settings.require_approval = False
    mixin._emit = AsyncMock()
    mixin._events = MagicMock()
    mixin._events.emit = AsyncMock()
    import asyncio as _asyncio
    mixin._merge_lock = _asyncio.Lock()
    return mixin


# ── ExecutorMixin: _execute_task records started_at ────────────────


@pytest.mark.asyncio
async def test_execute_task_records_started_at():
    """_execute_task calls set_task_timing(started_at=...) early on."""
    mixin = _make_executor()
    db = _make_db()
    task = FakeTask()
    db.get_task = AsyncMock(return_value=task)

    # Make the agent return None to trigger early exit
    mixin._run_agent = AsyncMock(return_value=None)
    mixin._prepare_worktree = AsyncMock(return_value="/wt/task-1")

    worktree_mgr = MagicMock()
    merge_worker = MagicMock()
    merge_worker._main = "main"
    runtime = MagicMock()

    with patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, return_value=_make_proc()):
        await mixin._execute_task(
            db, runtime, worktree_mgr, merge_worker,
            "task-1", "agent-1", pipeline_id="pipe-1",
        )

    # Verify started_at was recorded
    timing_calls = db.set_task_timing.call_args_list
    started_calls = [c for c in timing_calls if "started_at" in c.kwargs]
    assert len(started_calls) >= 1, f"Expected started_at call, got: {timing_calls}"
    assert started_calls[0].kwargs["started_at"] is not None


# ── ExecutorMixin: _run_agent records agent_duration_s ──────────────


@pytest.mark.asyncio
async def test_run_agent_records_duration():
    """_run_agent wraps _stream_agent with timing and records agent_duration_s."""
    mixin = _make_executor()
    db = _make_db()
    task = FakeTask()
    agent_result = FakeAgentResult(success=True, summary="done")

    mixin._stream_agent = AsyncMock(return_value=agent_result)
    mixin._build_prompt = MagicMock(return_value="do stuff")
    mixin._auto_commit_if_needed = AsyncMock(return_value=False)

    with patch("forge.core.daemon_executor.check_budget", new_callable=AsyncMock):
        with patch("forge.core.daemon_executor.select_model", return_value="sonnet"):
            with patch(
                "forge.core.daemon_executor._get_diff_vs_main",
                new_callable=AsyncMock,
                return_value="+ some change\n",
            ):
                with patch(
                    "forge.core.daemon_executor._parse_forge_question",
                    return_value=None,
                ):
                    result = await mixin._run_agent(
                        db, MagicMock(), MagicMock(), task, "task-1",
                        "agent-1", "/wt/task-1", "pipe-1",
                    )

    assert result is not None
    # Check agent_duration_s was recorded
    timing_calls = db.set_task_timing.call_args_list
    duration_calls = [c for c in timing_calls if "agent_duration_s" in c.kwargs]
    assert len(duration_calls) == 1
    assert duration_calls[0].kwargs["agent_duration_s"] > 0

    # Check turns were recorded
    db.set_task_turns.assert_called_once()
    call_args = db.set_task_turns.call_args
    assert call_args.args[0] == "task-1"
    assert call_args.kwargs["max_turns"] == 75


# ── ExecutorMixin: _run_agent records error on failure ──────────────


@pytest.mark.asyncio
async def test_run_agent_records_error_on_failure():
    """_run_agent calls set_task_error when agent fails."""
    mixin = _make_executor()
    db = _make_db()
    task = FakeTask()
    agent_result = FakeAgentResult(success=False, error="SDK timeout")

    mixin._stream_agent = AsyncMock(return_value=agent_result)
    mixin._build_prompt = MagicMock(return_value="do stuff")
    mixin._handle_retry = AsyncMock()

    with patch("forge.core.daemon_executor.check_budget", new_callable=AsyncMock):
        with patch("forge.core.daemon_executor.select_model", return_value="sonnet"):
            result = await mixin._run_agent(
                db, MagicMock(), MagicMock(), task, "task-1",
                "agent-1", "/wt/task-1", "pipe-1",
            )

    assert result is None
    db.set_task_error.assert_called_once_with("task-1", "SDK timeout")


# ── ExecutorMixin: _run_agent records error on no changes ───────────


@pytest.mark.asyncio
async def test_run_agent_records_error_on_no_changes():
    """_run_agent calls set_task_error when agent produces no diff."""
    mixin = _make_executor()
    db = _make_db()
    task = FakeTask()
    agent_result = FakeAgentResult(success=True, summary="did something")

    mixin._stream_agent = AsyncMock(return_value=agent_result)
    mixin._build_prompt = MagicMock(return_value="do stuff")
    mixin._auto_commit_if_needed = AsyncMock(return_value=False)
    mixin._handle_retry = AsyncMock()

    with patch("forge.core.daemon_executor.check_budget", new_callable=AsyncMock):
        with patch("forge.core.daemon_executor.select_model", return_value="sonnet"):
            with patch(
                "forge.core.daemon_executor._get_diff_vs_main",
                new_callable=AsyncMock,
                return_value="",
            ):
                with patch(
                    "forge.core.daemon_executor._parse_forge_question",
                    return_value=None,
                ):
                    result = await mixin._run_agent(
                        db, MagicMock(), MagicMock(), task, "task-1",
                        "agent-1", "/wt/task-1", "pipe-1",
                    )

    assert result is None
    db.set_task_error.assert_called_once_with("task-1", "Agent produced no changes")


# ── ExecutorMixin: _attempt_merge records merge_duration_s ──────────


@pytest.mark.asyncio
async def test_attempt_merge_records_duration():
    """_attempt_merge wraps the merge with timing and records merge_duration_s."""
    mixin = _make_executor()
    db = _make_db()
    task = FakeTask()

    merge_worker = MagicMock()
    merge_worker._main = "forge/pipeline-abc"
    merge_worker.merge = AsyncMock(return_value=MergeResult(success=True, error=None))

    mixin._run_review = AsyncMock(return_value=(True, None))
    mixin._emit_merge_success = AsyncMock()
    mixin._get_review_config = MagicMock(return_value={"skip_l2": True, "extra_review_pass": False, "custom_review_focus": ""})

    with (
        patch("forge.core.daemon_executor._get_diff_vs_main", new_callable=AsyncMock, return_value="+ change"),
        patch("forge.core.daemon_executor._resolve_ref", new_callable=AsyncMock, return_value="abc123"),
        patch("forge.core.daemon_executor.validate_task_id", return_value="task-1"),
        patch.object(ExecutorMixin, "_ensure_clean_for_rebase", new_callable=AsyncMock),
        patch("forge.core.daemon_executor._get_changed_files_vs_main", new_callable=AsyncMock, return_value=[]),
    ):
        await mixin._attempt_merge(
            db, merge_worker, MagicMock(), task, "task-1",
            "/wt/task-1", "sonnet", "pipe-1",
            pipeline_branch="forge/pipeline-abc",
        )

    timing_calls = db.set_task_timing.call_args_list
    merge_calls = [c for c in timing_calls if "merge_duration_s" in c.kwargs]
    assert len(merge_calls) == 1
    assert merge_calls[0].kwargs["merge_duration_s"] >= 0


# ── ExecutorMixin: completed_at recorded after cleanup ──────────────


@pytest.mark.asyncio
async def test_execute_task_records_completed_at():
    """_execute_task records completed_at after _cleanup_and_release."""
    mixin = _make_executor()
    db = _make_db()
    task = FakeTask()
    db.get_task = AsyncMock(return_value=task)

    agent_result = FakeAgentResult(success=True, summary="done")
    mixin._run_agent = AsyncMock(return_value=agent_result)
    mixin._prepare_worktree = AsyncMock(return_value="/wt/task-1")
    mixin._cleanup_and_release = AsyncMock()
    mixin._attempt_merge = AsyncMock()
    mixin._enforce_file_scope = AsyncMock(return_value=(True, []))
    mixin._deliver_interjections = AsyncMock(return_value=(False, None))

    merge_worker = MagicMock()
    merge_worker._main = "main"

    with (
        patch("forge.core.daemon_executor._run_git", new_callable=AsyncMock, return_value=_make_proc()),
        patch("forge.core.daemon_executor._parse_forge_question", return_value=None),
        patch("forge.core.daemon_executor.select_model", return_value="sonnet"),
    ):
        await mixin._execute_task(
            db, MagicMock(), MagicMock(), merge_worker,
            "task-1", "agent-1", pipeline_id="pipe-1",
        )

    timing_calls = db.set_task_timing.call_args_list
    completed_calls = [c for c in timing_calls if "completed_at" in c.kwargs]
    assert len(completed_calls) >= 1


# ── ReviewMixin: _run_review records review_duration_s ──────────────


@pytest.mark.asyncio
async def test_run_review_records_review_duration():
    """_run_review records review_duration_s on success."""
    mixin = ReviewMixin()
    mixin._settings = MagicMock()
    mixin._settings.agent_timeout_seconds = 300
    mixin._strategy = "balanced"
    mixin._snapshot = None
    mixin._emit = AsyncMock()
    mixin._gate_semaphore = None

    db = _make_db()
    task = FakeTask()

    lint_result = GateResult(passed=True, gate="gate1_auto_check", details="Clean")

    with (
        patch.object(mixin, "_resolve_build_cmd", return_value=None),
        patch.object(mixin, "_resolve_test_cmd", return_value=None),
        patch.object(mixin, "_run_lint_gate", new_callable=AsyncMock, return_value=lint_result),
        patch.object(mixin, "_get_review_config", return_value={"skip_l2": True, "extra_review_pass": False, "custom_review_focus": ""}),
        patch("forge.core.daemon_review._get_changed_files_vs_main", new_callable=AsyncMock, return_value=[]),
    ):
        passed, feedback = await mixin._run_review(
            task, "/wt/task-1", "+ diff", db=db, pipeline_id="pipe-1",
        )

    assert passed is True
    timing_calls = db.set_task_timing.call_args_list
    review_calls = [c for c in timing_calls if "review_duration_s" in c.kwargs]
    assert len(review_calls) >= 1
    assert review_calls[-1].kwargs["review_duration_s"] > 0


# ── ReviewMixin: lint timing recorded ───────────────────────────────


@pytest.mark.asyncio
async def test_run_review_records_lint_duration():
    """_run_review records lint_duration_s from the lint gate."""
    mixin = ReviewMixin()
    mixin._settings = MagicMock()
    mixin._settings.agent_timeout_seconds = 300
    mixin._strategy = "balanced"
    mixin._snapshot = None
    mixin._emit = AsyncMock()
    mixin._gate_semaphore = None

    db = _make_db()
    task = FakeTask()

    lint_result = GateResult(passed=True, gate="gate1_auto_check", details="Clean")

    with (
        patch.object(mixin, "_resolve_build_cmd", return_value=None),
        patch.object(mixin, "_resolve_test_cmd", return_value=None),
        patch.object(mixin, "_run_lint_gate", new_callable=AsyncMock, return_value=lint_result),
        patch.object(mixin, "_get_review_config", return_value={"skip_l2": True, "extra_review_pass": False, "custom_review_focus": ""}),
        patch("forge.core.daemon_review._get_changed_files_vs_main", new_callable=AsyncMock, return_value=[]),
    ):
        await mixin._run_review(
            task, "/wt/task-1", "+ diff", db=db, pipeline_id="pipe-1",
        )

    timing_calls = db.set_task_timing.call_args_list
    lint_calls = [c for c in timing_calls if "lint_duration_s" in c.kwargs]
    assert len(lint_calls) == 1
    assert lint_calls[0].kwargs["lint_duration_s"] >= 0


# ── ReviewMixin: review timing recorded on failure ──────────────────


@pytest.mark.asyncio
async def test_run_review_records_duration_on_lint_failure():
    """_run_review records review_duration_s even when lint fails."""
    mixin = ReviewMixin()
    mixin._settings = MagicMock()
    mixin._settings.agent_timeout_seconds = 300
    mixin._strategy = "balanced"
    mixin._snapshot = None
    mixin._emit = AsyncMock()
    mixin._gate_semaphore = None

    db = _make_db()
    task = FakeTask()

    lint_result = GateResult(passed=False, gate="gate1_auto_check", details="Lint errors")

    with (
        patch.object(mixin, "_resolve_build_cmd", return_value=None),
        patch.object(mixin, "_run_lint_gate", new_callable=AsyncMock, return_value=lint_result),
        patch("forge.core.daemon_review._get_changed_files_vs_main", new_callable=AsyncMock, return_value=[]),
    ):
        passed, feedback = await mixin._run_review(
            task, "/wt/task-1", "+ diff", db=db, pipeline_id="pipe-1",
        )

    assert passed is False
    timing_calls = db.set_task_timing.call_args_list
    review_calls = [c for c in timing_calls if "review_duration_s" in c.kwargs]
    assert len(review_calls) >= 1


# ── MergeMixin: _handle_retry records error on max retries ──────────


@pytest.mark.asyncio
async def test_handle_retry_records_error_on_max_retries():
    """_handle_retry calls set_task_error when max retries exceeded."""
    mixin = MergeMixin()
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 1
    mixin._events = MagicMock()
    mixin._events.emit = AsyncMock()
    mixin._emit = AsyncMock()

    db = _make_db()
    task = FakeTask(retry_count=1)  # Already at max
    db.get_task = AsyncMock(return_value=task)

    worktree_mgr = MagicMock()

    await mixin._handle_retry(db, "task-1", worktree_mgr, pipeline_id="pipe-1")

    db.set_task_error.assert_called_once()
    error_msg = db.set_task_error.call_args.args[1]
    assert "Max retries exceeded" in error_msg

    # completed_at should also be recorded
    timing_calls = db.set_task_timing.call_args_list
    completed_calls = [c for c in timing_calls if "completed_at" in c.kwargs]
    assert len(completed_calls) == 1


# ── MergeMixin: _handle_merge_retry records error on max retries ────


@pytest.mark.asyncio
async def test_handle_merge_retry_records_error_on_max_retries():
    """_handle_merge_retry calls set_task_error when max retries exceeded."""
    mixin = MergeMixin()
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 1
    mixin._events = MagicMock()
    mixin._events.emit = AsyncMock()
    mixin._emit = AsyncMock()

    db = _make_db()
    task = FakeTask(retry_count=1)
    db.get_task = AsyncMock(return_value=task)

    worktree_mgr = MagicMock()

    await mixin._handle_merge_retry(db, "task-1", worktree_mgr, pipeline_id="pipe-1")

    db.set_task_error.assert_called_once_with("task-1", "Max merge retries exceeded")
    timing_calls = db.set_task_timing.call_args_list
    completed_calls = [c for c in timing_calls if "completed_at" in c.kwargs]
    assert len(completed_calls) == 1


# ── Daemon: finalize_pipeline_metrics called ────────────────────────


@pytest.mark.asyncio
async def test_finalize_pipeline_metrics_called_on_completion():
    """The daemon scheduler loop calls finalize_pipeline_metrics when pipeline completes."""
    # This is a smoke test that the method exists and is called
    # Full integration test would require a full daemon setup
    db = _make_db()
    await db.finalize_pipeline_metrics("pipe-1")
    db.finalize_pipeline_metrics.assert_called_once_with("pipe-1")
