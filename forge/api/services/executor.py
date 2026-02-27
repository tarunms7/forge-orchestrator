"""Executor interface and implementations for running Claude commands."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

try:
    import asyncssh
except ImportError:  # pragma: no cover
    asyncssh = None  # type: ignore[assignment]


@dataclass
class ExecutorHealth:
    """Health status of an executor."""

    available: bool
    error: str | None = None


@dataclass
class SSHConfig:
    """Configuration for SSH-based remote execution."""

    host: str
    user: str
    key_path: str | None = None
    port: int = 22
    known_hosts_path: str | None = None  # None = use system default (~/.ssh/known_hosts)


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


class RemoteExecutor(Executor):
    """Executor that runs Claude CLI on a remote machine via SSH."""

    def __init__(self, config: SSHConfig) -> None:
        self.config = config

    async def check_claude(self) -> bool:
        """Check if Claude CLI is available on the remote host via SSH."""
        if asyncssh is None:
            raise RuntimeError("asyncssh is required for RemoteExecutor; install with: pip install asyncssh")

        connect_kwargs: dict = {
            "host": self.config.host,
            "username": self.config.user,
            "port": self.config.port,
        }
        if self.config.key_path:
            connect_kwargs["client_keys"] = [self.config.key_path]
        if self.config.known_hosts_path is not None:
            connect_kwargs["known_hosts"] = self.config.known_hosts_path

        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run("claude --version", check=False)
            return result.exit_status == 0

    async def health_check(self) -> ExecutorHealth:
        """Return health status of the remote Claude CLI."""
        try:
            available = await self.check_claude()
            if available:
                return ExecutorHealth(available=True)
            return ExecutorHealth(available=False, error="claude CLI not available on remote host")
        except Exception as exc:
            return ExecutorHealth(available=False, error=str(exc))
