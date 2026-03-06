"""Tests for daemon_review — sibling context builder."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from forge.core.daemon_review import ReviewMixin


def _make_task(task_id="task-2", title="Create webhook", files=None, state="todo"):
    """Create a mock task object."""
    t = MagicMock()
    t.id = task_id
    t.title = title
    t.files = files if files is not None else ["webhooks.py"]
    t.state = state
    return t


class TestBuildSiblingContext:
    """ReviewMixin._build_sibling_context() provides DAG awareness to the reviewer."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._emit = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_with_siblings(self):
        """Returns formatted context when pipeline has multiple tasks."""
        mixin = self._make_mixin()
        current_task = _make_task("task-2", "Create webhook", ["webhooks.py"])

        sibling1 = _make_task("task-1", "Add DB schema", ["db.py", "models.py"], "done")
        sibling2 = _make_task("task-3", "Register router", ["app.py"], "todo")

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [sibling1, current_task, sibling2]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-123")

        assert result is not None
        assert "Pipeline Task Context" in result
        assert "task-1" in result
        assert "Add DB schema" in result
        assert "db.py, models.py" in result
        assert "done" in result
        assert "task-3" in result
        assert "Register router" in result
        assert "app.py" in result
        # Current task should NOT be listed
        assert "task-2" not in result
        # Should include the important instruction
        assert "do not fail the review" in result.lower()

    @pytest.mark.asyncio
    async def test_solo_task_returns_none(self):
        """Returns None when pipeline has only one task."""
        mixin = self._make_mixin()
        current_task = _make_task("task-1", "Solo task")

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [current_task]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_pipeline_returns_none(self):
        """Returns None when pipeline_id is empty/falsy."""
        mixin = self._make_mixin()
        current_task = _make_task()

        db = AsyncMock()

        result = await mixin._build_sibling_context(current_task, db, "")

        assert result is None
        db.list_tasks_by_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_many_files_truncated(self):
        """Tasks with >5 files show truncation indicator."""
        mixin = self._make_mixin()
        current_task = _make_task("task-1", "Current")

        many_files_task = _make_task(
            "task-2", "Big task",
            ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
            "in_progress",
        )

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [current_task, many_files_task]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-1")

        assert result is not None
        assert "+2 more" in result

    @pytest.mark.asyncio
    async def test_sibling_with_no_files(self):
        """Handles siblings with empty file lists."""
        mixin = self._make_mixin()
        current_task = _make_task("task-1", "Current")
        no_files_task = _make_task("task-2", "No files", [], "todo")

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [current_task, no_files_task]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-1")

        assert result is not None
        assert "(none)" in result
