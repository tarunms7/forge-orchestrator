"""Executor interface and implementations for running Claude commands."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecutorHealth:
    """Health status of an executor."""

    available: bool
    error: str | None = None


class Executor(ABC):
    """Abstract base class for Claude executors."""

    @abstractmethod
    async def check_claude(self) -> bool:
        """Check whether the Claude CLI is reachable.

        Returns True if Claude CLI responds successfully, False otherwise.
        """

    @abstractmethod
    async def health_check(self) -> ExecutorHealth:
        """Perform a health check and return detailed status."""


class LocalExecutor(Executor):
    """Executor that runs Claude CLI on the local machine."""

    async def check_claude(self) -> bool:
        """Check if Claude CLI is available locally by running ``claude --version``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    async def health_check(self) -> ExecutorHealth:
        """Return health status of the local Claude CLI."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return ExecutorHealth(available=True)
            return ExecutorHealth(
                available=False,
                error=stderr.decode().strip() or "non-zero exit code",
            )
        except Exception as exc:
            return ExecutorHealth(available=False, error=str(exc))
