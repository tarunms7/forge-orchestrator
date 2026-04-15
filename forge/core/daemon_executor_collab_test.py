"""Tests for collaboration broker wiring in ExecutorMixin and ForgeDaemon."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.collaboration import AgentCollaborationBroker
from forge.core.daemon_executor import ExecutorMixin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(*, with_broker: bool = True) -> ExecutorMixin:
    """Create an ExecutorMixin with optional broker attached."""
    mixin = ExecutorMixin.__new__(ExecutorMixin)
    mixin._repos = {"default": MagicMock(path="/repo")}
    if with_broker:
        mixin._collaboration_broker = AgentCollaborationBroker()
    return mixin


def _make_task(
    task_id: str = "task-1",
    *,
    files: list[str] | None = None,
    state: str = "done",
    title: str = "Test task",
    description: str = "- Used adapter pattern\n- Added retry logic",
    depends_on: list[str] | None = None,
    implementation_summary: str | None = "Implemented auth",
):
    """Return a mock task object."""
    task = MagicMock()
    task.id = task_id
    task.files = files or ["forge/core/daemon.py"]
    task.state = state
    task.title = title
    task.description = description
    task.depends_on = depends_on or []
    task.implementation_summary = implementation_summary
    task.retry_count = 0
    task.review_feedback = None
    return task


# ---------------------------------------------------------------------------
# _emit_merge_success — broker registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEmitMergeSuccessBrokerRegistration:
    """Broker.register_completion is called inside _emit_merge_success."""

    async def test_register_completion_called_with_diff(self):
        """When diff is passed, broker stores a CompletionRecord."""
        executor = _make_executor(with_broker=True)
        broker = executor._collaboration_broker

        db = AsyncMock()
        task = _make_task(task_id="task-1", files=["auth.py"])
        db.get_task.return_value = task
        db.update_task_state = AsyncMock()
        db.update_task_implementation_summary = AsyncMock()

        executor._emit = AsyncMock()
        executor._record_health_activity = MagicMock()

        with (
            patch(
                "forge.core.daemon_executor._extract_implementation_summary",
                new_callable=AsyncMock,
                return_value="Added auth",
            ),
            patch(
                "forge.core.daemon_executor._get_diff_stats",
                new_callable=AsyncMock,
                return_value={"files": 1, "insertions": 10, "deletions": 2},
            ),
        ):
            await executor._emit_merge_success(
                db,
                "task-1",
                "pipeline-1",
                "/wt/task-1",
                diff="diff --git a/auth.py b/auth.py\n+new code",
            )

        record = broker.get_completion("pipeline-1", "task-1")
        assert record is not None
        assert record.task_id == "task-1"
        assert record.files_changed == ["auth.py"]
        assert record.implementation_summary == "Added auth"
        assert "new code" in record.diff

    async def test_register_completion_computes_diff_when_not_provided(self):
        """When diff is empty, _emit_merge_success computes it from worktree."""
        executor = _make_executor(with_broker=True)
        broker = executor._collaboration_broker

        db = AsyncMock()
        task = _make_task(task_id="task-2", files=["api.py"])
        db.get_task.return_value = task
        db.update_task_state = AsyncMock()
        db.update_task_implementation_summary = AsyncMock()

        executor._emit = AsyncMock()
        executor._record_health_activity = MagicMock()

        with (
            patch(
                "forge.core.daemon_executor._extract_implementation_summary",
                new_callable=AsyncMock,
                return_value="Built API",
            ),
            patch(
                "forge.core.daemon_executor._get_diff_stats",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "forge.core.daemon_executor._get_diff_vs_main",
                new_callable=AsyncMock,
                return_value="diff --git a/api.py b/api.py\n+api code",
            ),
        ):
            await executor._emit_merge_success(
                db,
                "task-2",
                "pipeline-1",
                "/wt/task-2",
            )

        record = broker.get_completion("pipeline-1", "task-2")
        assert record is not None
        assert "api code" in record.diff

    async def test_register_completion_extracts_key_decisions(self):
        """Key decisions are extracted from agent summary (task description)."""
        executor = _make_executor(with_broker=True)
        broker = executor._collaboration_broker

        db = AsyncMock()
        task = _make_task(
            task_id="task-3",
            description="- Used adapter pattern\n- Added retry logic\n* Chose SQLite",
        )
        db.get_task.return_value = task
        db.update_task_state = AsyncMock()
        db.update_task_implementation_summary = AsyncMock()

        executor._emit = AsyncMock()
        executor._record_health_activity = MagicMock()

        with (
            patch(
                "forge.core.daemon_executor._extract_implementation_summary",
                new_callable=AsyncMock,
                return_value="summary",
            ),
            patch(
                "forge.core.daemon_executor._get_diff_stats",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            await executor._emit_merge_success(
                db,
                "task-3",
                "pipeline-1",
                "/wt/task-3",
                diff="some diff",
            )

        record = broker.get_completion("pipeline-1", "task-3")
        assert record is not None
        assert "Used adapter pattern" in record.key_decisions
        assert "Added retry logic" in record.key_decisions
        assert "Chose SQLite" in record.key_decisions

    async def test_no_broker_graceful(self):
        """When broker is not set, _emit_merge_success still completes."""
        executor = _make_executor(with_broker=False)

        db = AsyncMock()
        task = _make_task()
        db.get_task.return_value = task
        db.update_task_state = AsyncMock()
        db.update_task_implementation_summary = AsyncMock()

        executor._emit = AsyncMock()
        executor._record_health_activity = MagicMock()

        with (
            patch(
                "forge.core.daemon_executor._extract_implementation_summary",
                new_callable=AsyncMock,
                return_value="summary",
            ),
            patch(
                "forge.core.daemon_executor._get_diff_stats",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            # Should not raise
            await executor._emit_merge_success(
                db,
                "task-1",
                "pipeline-1",
                "/wt/task-1",
                diff="diff content",
            )

        # Verify the rest of the method still ran
        db.update_task_state.assert_called_once()
        db.update_task_implementation_summary.assert_called_once()

    async def test_broker_registration_exception_does_not_propagate(self):
        """If broker.register_completion raises, the merge still succeeds."""
        executor = _make_executor(with_broker=True)
        executor._collaboration_broker.register_completion = MagicMock(
            side_effect=RuntimeError("boom")
        )

        db = AsyncMock()
        task = _make_task()
        db.get_task.return_value = task
        db.update_task_state = AsyncMock()
        db.update_task_implementation_summary = AsyncMock()

        executor._emit = AsyncMock()
        executor._record_health_activity = MagicMock()

        with (
            patch(
                "forge.core.daemon_executor._extract_implementation_summary",
                new_callable=AsyncMock,
                return_value="summary",
            ),
            patch(
                "forge.core.daemon_executor._get_diff_stats",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            # Should not raise despite broker error
            await executor._emit_merge_success(
                db,
                "task-1",
                "pipeline-1",
                "/wt/task-1",
                diff="diff content",
            )

        db.update_task_state.assert_called_once()


# ---------------------------------------------------------------------------
# _run_agent — completed_deps enrichment
# ---------------------------------------------------------------------------


class TestCompletedDepsEnrichment:
    """Broker enriches completed_deps with diff and key_decisions."""

    def test_deps_enriched_with_broker_data(self):
        """When broker has completion records, deps gain broker_diff and key_decisions."""
        broker = AgentCollaborationBroker()
        broker.register_completion(
            pipeline_id="p-1",
            task_id="task-1",
            files_changed=["auth.py"],
            implementation_summary="Added auth",
            agent_summary="- Used JWT tokens\n- Added refresh flow",
            diff="diff --git a/auth.py b/auth.py\n+jwt code",
        )

        completed_deps: list[dict] = [
            {
                "task_id": "task-1",
                "title": "Auth task",
                "implementation_summary": "Added auth",
                "files_changed": ["auth.py"],
            }
        ]

        # Simulate the enrichment logic from _run_agent
        for dep in completed_deps:
            completion = broker.get_completion("p-1", dep["task_id"])
            if completion:
                dep["broker_diff"] = completion.diff
                dep["key_decisions"] = completion.key_decisions

        assert "broker_diff" in completed_deps[0]
        assert "jwt code" in completed_deps[0]["broker_diff"]
        assert "Used JWT tokens" in completed_deps[0]["key_decisions"]
        assert "Added refresh flow" in completed_deps[0]["key_decisions"]

    def test_deps_not_enriched_when_broker_none(self):
        """When broker is None, deps remain unchanged."""
        completed_deps: list[dict] = [
            {
                "task_id": "task-1",
                "title": "Auth task",
                "implementation_summary": "Added auth",
                "files_changed": ["auth.py"],
            }
        ]

        broker = None
        if broker is not None:
            for dep in completed_deps:
                completion = broker.get_completion("p-1", dep["task_id"])
                if completion:
                    dep["broker_diff"] = completion.diff
                    dep["key_decisions"] = completion.key_decisions

        assert "broker_diff" not in completed_deps[0]
        assert "key_decisions" not in completed_deps[0]

    def test_deps_not_enriched_when_no_completion(self):
        """When broker has no record for a dep, that dep is left untouched."""
        broker = AgentCollaborationBroker()

        completed_deps: list[dict] = [
            {
                "task_id": "task-99",
                "title": "Unknown task",
                "implementation_summary": None,
                "files_changed": [],
            }
        ]

        for dep in completed_deps:
            completion = broker.get_completion("p-1", dep["task_id"])
            if completion:
                dep["broker_diff"] = completion.diff
                dep["key_decisions"] = completion.key_decisions

        assert "broker_diff" not in completed_deps[0]


# ---------------------------------------------------------------------------
# Broker cleanup
# ---------------------------------------------------------------------------


class TestBrokerCleanup:
    """Broker.cleanup removes all data for a pipeline."""

    def test_cleanup_removes_pipeline_data(self):
        broker = AgentCollaborationBroker()
        broker.register_completion(
            pipeline_id="p-1",
            task_id="task-1",
            files_changed=["a.py"],
            implementation_summary="summary",
            agent_summary="- decision",
            diff="diff",
        )
        assert broker.get_completion("p-1", "task-1") is not None

        broker.cleanup("p-1")
        assert broker.get_completion("p-1", "task-1") is None

    def test_cleanup_noop_for_unknown_pipeline(self):
        """cleanup on a pipeline that doesn't exist should not raise."""
        broker = AgentCollaborationBroker()
        broker.cleanup("nonexistent")  # no-op, no error

    def test_cleanup_preserves_other_pipelines(self):
        broker = AgentCollaborationBroker()
        broker.register_completion(
            pipeline_id="p-1",
            task_id="task-1",
            files_changed=["a.py"],
            implementation_summary="s1",
            agent_summary="- d1",
            diff="diff1",
        )
        broker.register_completion(
            pipeline_id="p-2",
            task_id="task-2",
            files_changed=["b.py"],
            implementation_summary="s2",
            agent_summary="- d2",
            diff="diff2",
        )

        broker.cleanup("p-1")

        assert broker.get_completion("p-1", "task-1") is None
        assert broker.get_completion("p-2", "task-2") is not None
