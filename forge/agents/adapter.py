"""Agent adapter interface. Claude primary, others pluggable."""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from claude_code_sdk import (
    ClaudeCodeOptions,
    ResultMessage,
    query,
)

AGENT_SYSTEM_PROMPT = """You are a coding agent working on a specific task within the Forge orchestration system.

You have access to a git worktree isolated to your task. Write clean, tested code.

Rules:
- Only modify files listed in your task specification
- Follow existing code style and patterns
- Write tests for any new functionality
- Commit your changes with a clear commit message when done
- If you encounter an error, fix it rather than giving up"""


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
    """Claude Code agent via claude-code-sdk."""

    def __init__(self, model: str = "sonnet") -> None:
        self._model = model

    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
    ) -> AgentResult:
        options = ClaudeCodeOptions(
            system_prompt=AGENT_SYSTEM_PROMPT,
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            permission_mode="bypassPermissions",
            cwd=worktree_path,
            model=self._model,
        )

        result_text = ""
        is_error = False
        cost_usd = 0.0

        async for message in query(prompt=task_prompt, options=options):
            if isinstance(message, ResultMessage):
                result_text = message.result or ""
                is_error = message.is_error
                cost_usd = message.total_cost_usd or 0.0

        files_changed = _get_changed_files(worktree_path)

        if is_error:
            return AgentResult(
                success=False,
                files_changed=files_changed,
                summary=result_text,
                error=result_text,
            )

        return AgentResult(
            success=True,
            files_changed=files_changed,
            summary=result_text[:500] if result_text else "Task completed",
            token_usage=int(cost_usd * 1_000_000),
        )


def _get_changed_files(worktree_path: str) -> list[str]:
    """Get list of files changed in the worktree vs its base."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    files = set()
    for output in (result.stdout, staged.stdout):
        for line in output.strip().split("\n"):
            if line.strip():
                files.add(line.strip())
    return sorted(files)
