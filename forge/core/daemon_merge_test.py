"""Tests for MergeMixin — _cascade_blocked and its wiring into retry handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_cascade_blocked_marks_dependents():
    """When task-1 fails, task-3 (depends on task-1) should be marked BLOCKED."""
    from forge.core.daemon_merge import MergeMixin

    mixin = MergeMixin.__new__(MergeMixin)
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 3
    mixin._events = AsyncMock()
    mixin._emit = AsyncMock()

    db = AsyncMock()
    task1 = MagicMock(id="t1", state="error", depends_on=[])
    task2 = MagicMock(id="t2", state="done", depends_on=[])
    task3 = MagicMock(id="t3", state="todo", depends_on=["t1"])
    task4 = MagicMock(id="t4", state="todo", depends_on=["t3"])  # transitive
    db.list_tasks_by_pipeline = AsyncMock(return_value=[task1, task2, task3, task4])

    await mixin._cascade_blocked(db, "t1", "pipe-1")

    calls = db.update_task_state.call_args_list
    assert any(c.args == ("t3", "blocked") for c in calls)
    assert any(c.args == ("t4", "blocked") for c in calls)
    assert not any(c.args[0] == "t2" for c in calls)


@pytest.mark.asyncio
async def test_handle_retry_cascades_on_max_retries():
    from forge.core.daemon_merge import MergeMixin

    mixin = MergeMixin.__new__(MergeMixin)
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 2
    mixin._events = AsyncMock()
    mixin._emit = AsyncMock()
    mixin._cascade_blocked = AsyncMock()

    db = AsyncMock()
    task = MagicMock(id="t1", retry_count=2)
    db.get_task = AsyncMock(return_value=task)

    worktree_mgr = MagicMock()
    worktree_mgr.remove = MagicMock()

    await mixin._handle_retry(db, "t1", worktree_mgr, pipeline_id="pipe-1")

    db.update_task_state.assert_called_once_with("t1", "error")
    mixin._cascade_blocked.assert_called_once_with(db, "t1", "pipe-1")


@pytest.mark.asyncio
async def test_handle_merge_retry_cascades_on_max_retries():
    from forge.core.daemon_merge import MergeMixin

    mixin = MergeMixin.__new__(MergeMixin)
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 2
    mixin._events = AsyncMock()
    mixin._emit = AsyncMock()
    mixin._cascade_blocked = AsyncMock()

    db = AsyncMock()
    task = MagicMock(id="t1", retry_count=2)
    db.get_task = AsyncMock(return_value=task)

    worktree_mgr = MagicMock()
    worktree_mgr.remove = MagicMock()

    await mixin._handle_merge_retry(db, "t1", worktree_mgr, pipeline_id="pipe-1")

    db.update_task_state.assert_called_once_with("t1", "error")
    mixin._cascade_blocked.assert_called_once_with(db, "t1", "pipe-1")


@pytest.mark.asyncio
async def test_repos_json_updated_after_merge(tmp_path):
    """update_repos_json_branches writes branch_name and pr_url into repos_json."""
    import json

    from forge.core.daemon_helpers import update_repos_json_branches
    from forge.storage.db import Database

    pipeline_id = "test-pipe-001"
    initial_repos = [
        {"id": "backend", "path": "/path/to/backend", "base_branch": "main"},
        {"id": "frontend", "path": "/path/to/frontend", "base_branch": "main"},
    ]

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()

    await db.create_pipeline(
        pipeline_id,
        description="test",
        project_dir=str(tmp_path),
        repos_json=json.dumps(initial_repos),
    )

    pipeline_branches = {
        "backend": "forge/pipeline-test-pipe",
        "frontend": "forge/pipeline-test-pipe",
    }
    await update_repos_json_branches(db, pipeline_id, pipeline_branches)

    row = await db.get_pipeline(pipeline_id)
    assert row is not None
    updated_repos = json.loads(row.repos_json)

    assert len(updated_repos) == 2
    for entry in updated_repos:
        assert entry["branch_name"] == "forge/pipeline-test-pipe"
        assert entry["pr_url"] == ""
        # Original fields preserved
        assert "id" in entry
        assert "path" in entry
        assert "base_branch" in entry
