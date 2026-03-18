"""Tests for agent release retry logic in _safe_execute_task."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon


def _make_daemon(tmp_path, **settings_kwargs):
    settings = ForgeSettings(**settings_kwargs)
    return ForgeDaemon(project_dir=str(tmp_path), settings=settings)


def _make_task(state: str, task_id: str = "task-1") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.state = state
    t.title = f"Task {task_id}"
    t.description = "test task"
    t.files = []
    t.depends_on = []
    t.complexity = "medium"
    t.assigned_agent = None
    t.retry_count = 0
    return t


@pytest.mark.asyncio
class TestAgentReleaseRetry:

    async def test_release_retries_on_db_error(self, tmp_path):
        """release_agent fails twice then succeeds on the third call."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock(
            side_effect=[Exception("fail-1"), Exception("fail-2"), None]
        )
        db.force_release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(return_value=None)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await daemon._safe_execute_task(
                db, MagicMock(), MagicMock(), MagicMock(),
                "task-1", "agent-1",
            )

        assert db.release_agent.call_count == 3
        db.force_release_agent.assert_not_called()

    async def test_force_release_after_max_retries(self, tmp_path):
        """After 3 failures, force_release_agent is called as fallback."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock(side_effect=Exception("always fails"))
        db.force_release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(return_value=None)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await daemon._safe_execute_task(
                db, MagicMock(), MagicMock(), MagicMock(),
                "task-1", "agent-1",
            )

        assert db.release_agent.call_count == 3
        db.force_release_agent.assert_called_once_with("agent-1")

    async def test_release_succeeds_first_try(self, tmp_path):
        """Normal path: single call, no retries."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()
        db.force_release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(return_value=None)

        await daemon._safe_execute_task(
            db, MagicMock(), MagicMock(), MagicMock(),
            "task-1", "agent-1",
        )

        db.release_agent.assert_called_once_with("agent-1")
        db.force_release_agent.assert_not_called()

    async def test_release_failure_does_not_mask_original_error(self, tmp_path):
        """RuntimeError from _execute_task propagates even when release fails."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock(side_effect=Exception("DB down"))
        db.force_release_agent = AsyncMock()
        daemon._execute_task = AsyncMock(side_effect=RuntimeError("task crash"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="task crash"):
                await daemon._safe_execute_task(
                    db, MagicMock(), MagicMock(), MagicMock(),
                    "task-1", "agent-1",
                )

    async def test_slot_leak_logged_when_all_release_methods_fail(self, tmp_path):
        """CRITICAL log emitted when both release_agent and force_release_agent fail."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock(side_effect=Exception("DB down"))
        db.force_release_agent = AsyncMock(side_effect=Exception("raw SQL failed"))
        daemon._execute_task = AsyncMock(return_value=None)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch("forge.core.daemon.logger") as mock_logger:
                await daemon._safe_execute_task(
                    db, MagicMock(), MagicMock(), MagicMock(),
                    "task-1", "agent-1",
                )

                mock_logger.critical.assert_called_once()
                assert "SLOT LEAK" in mock_logger.critical.call_args[0][0]
