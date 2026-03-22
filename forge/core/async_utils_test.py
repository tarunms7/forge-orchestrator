"""Tests for forge.core.async_utils."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from forge.core.async_utils import _log_task_exception, safe_create_task


@pytest.fixture
def event_loop_policy():
    """Ensure we get a fresh event loop for each test."""
    pass


# ---------------------------------------------------------------------------
# safe_create_task: logs exceptions from failed tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_create_task_logs_exception(caplog):
    """Failed background tasks should log an error with the exception."""

    async def _boom():
        raise ValueError("kaboom")

    with caplog.at_level(logging.ERROR, logger="forge"):
        task = safe_create_task(_boom(), name="test-boom")
        # Let the task complete
        with pytest.raises(ValueError):
            await task

    assert any("Background task" in r.message and "kaboom" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# safe_create_task: handles cancelled tasks without logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_create_task_cancelled_no_log(caplog):
    """Cancelled tasks should not produce error logs."""

    async def _hang():
        await asyncio.sleep(999)

    with caplog.at_level(logging.ERROR, logger="forge"):
        task = safe_create_task(_hang(), name="test-cancel")
        # Give task a moment to start, then cancel
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 0, f"Unexpected error logs: {error_records}"


# ---------------------------------------------------------------------------
# Successful tasks don't trigger error logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_create_task_success_no_log(caplog):
    """Successful tasks should not produce any error logs."""

    async def _ok():
        return 42

    with caplog.at_level(logging.ERROR, logger="forge"):
        task = safe_create_task(_ok(), name="test-ok")
        result = await task

    assert result == 42
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 0


# ---------------------------------------------------------------------------
# Custom logger is used when provided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_create_task_custom_logger():
    """When a custom logger is provided, it should be used for error logging."""
    custom_logger = MagicMock(spec=logging.Logger)

    async def _fail():
        raise RuntimeError("custom-fail")

    task = safe_create_task(_fail(), logger=custom_logger, name="test-custom")
    with pytest.raises(RuntimeError):
        await task

    custom_logger.error.assert_called_once()
    args = custom_logger.error.call_args
    assert "Background task" in args[0][0]
    assert "custom-fail" in str(args[0])


# ---------------------------------------------------------------------------
# Name parameter is passed through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_create_task_name_passthrough():
    """The name parameter should be forwarded to asyncio.create_task."""

    async def _noop():
        pass

    task = safe_create_task(_noop(), name="my-task-name")
    assert task.get_name() == "my-task-name"
    await task


# ---------------------------------------------------------------------------
# _log_task_exception: unit tests for the callback directly
# ---------------------------------------------------------------------------


def test_log_task_exception_cancelled():
    """Cancelled tasks should return immediately without logging."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = True
    mock_logger = MagicMock(spec=logging.Logger)

    _log_task_exception(mock_task, mock_logger)

    mock_logger.error.assert_not_called()


def test_log_task_exception_no_exception():
    """Tasks that completed without exception should not log."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = False
    mock_task.exception.return_value = None
    mock_logger = MagicMock(spec=logging.Logger)

    _log_task_exception(mock_task, mock_logger)

    mock_logger.error.assert_not_called()


def test_log_task_exception_with_exception():
    """Tasks that raised an exception should log at error level."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = False
    exc = ValueError("test-error")
    mock_task.exception.return_value = exc
    mock_task.get_name.return_value = "failing-task"
    mock_logger = MagicMock(spec=logging.Logger)

    _log_task_exception(mock_task, mock_logger)

    mock_logger.error.assert_called_once()
    call_args = mock_logger.error.call_args
    assert "Background task" in call_args[0][0]
    assert call_args[1]["exc_info"] is exc


def test_log_task_exception_fallback_logger():
    """When no logger is provided, the 'forge' logger should be used."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = False
    exc = RuntimeError("fallback-test")
    mock_task.exception.return_value = exc
    mock_task.get_name.return_value = "fb-task"

    # Patch the forge logger to verify it's used
    forge_logger = logging.getLogger("forge")
    handler = logging.Handler()
    handler.emit = MagicMock()
    forge_logger.addHandler(handler)
    forge_logger.setLevel(logging.ERROR)

    try:
        _log_task_exception(mock_task, None)
        assert handler.emit.called
    finally:
        forge_logger.removeHandler(handler)
