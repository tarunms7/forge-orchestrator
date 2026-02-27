"""Tests for Executor interface and LocalExecutor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.api.services.executor import (
    Executor,
    ExecutorHealth,
    LocalExecutor,
)


class TestExecutorHealth:
    """Tests for the ExecutorHealth dataclass."""

    def test_healthy(self):
        health = ExecutorHealth(available=True, error=None)
        assert health.available is True
        assert health.error is None

    def test_unhealthy(self):
        health = ExecutorHealth(available=False, error="claude not found")
        assert health.available is False
        assert health.error == "claude not found"


class TestExecutorABC:
    """Executor ABC should not be instantiable directly."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            Executor()  # type: ignore[abstract]


class TestLocalExecutor:
    """Tests for LocalExecutor."""

    async def test_check_claude_returns_true_when_installed(self):
        executor = LocalExecutor()
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"claude-code 1.0.0\n", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await executor.check_claude()

        assert result is True
        mock_exec.assert_called_once()

    async def test_check_claude_returns_false_when_not_installed(self):
        executor = LocalExecutor()
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"command not found")
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor.check_claude()

        assert result is False

    async def test_check_claude_returns_false_on_exception(self):
        executor = LocalExecutor()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("claude not found"),
        ):
            result = await executor.check_claude()

        assert result is False

    async def test_health_check_healthy(self):
        executor = LocalExecutor()
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"claude-code 1.0.0\n", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            health = await executor.health_check()

        assert isinstance(health, ExecutorHealth)
        assert health.available is True
        assert health.error is None

    async def test_health_check_unhealthy(self):
        executor = LocalExecutor()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ):
            health = await executor.health_check()

        assert isinstance(health, ExecutorHealth)
        assert health.available is False
        assert health.error is not None
        assert "not found" in health.error
