"""Agent adapter interface. Claude primary, others pluggable."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentResult:
    """Outcome of an agent task execution."""

    success: bool
    files_changed: list[str]
    summary: str
    token_usage: int = 0
    error: str | None = None


class AgentAdapter(ABC):
    """Interface for agent backends. Implement for each supported agent."""

    @abstractmethod
    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
    ) -> AgentResult:
        """Execute a task and return the result."""


class ClaudeAdapter(AgentAdapter):
    """Claude Code agent via claude_agent_sdk."""

    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
    ) -> AgentResult:
        raise NotImplementedError("Claude adapter not yet wired to SDK")
