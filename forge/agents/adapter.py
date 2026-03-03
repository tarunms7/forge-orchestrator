"""Agent adapter interface. Claude primary, others pluggable."""

import asyncio
import logging
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from claude_code_sdk import ClaudeCodeOptions

from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.agents")

AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a coding agent working on a specific task within the Forge orchestration system.

Your working directory is {cwd}. Do NOT read, write, or execute anything outside this directory{extra_dirs_clause}.

You have access to a git worktree isolated to your task. Write clean, tested code.

{project_context}

Rules:
- Only modify files listed in your task specification
- Follow existing code style and patterns
- Write tests for any new functionality
- Commit your changes with a clear commit message when done
- If you encounter an error, fix it rather than giving up
- If image file paths are mentioned in the task description, use the Read tool to view them (images are readable)"""


@dataclass
class AgentResult:
    """Outcome of an agent task execution."""

    success: bool
    files_changed: list[str]
    summary: str
    cost_usd: float = 0.0
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
        allowed_dirs: list[str] | None = None,
        model: str = "sonnet",
        on_message: Callable | None = None,
        project_context: str = "",
    ) -> AgentResult:
        """Execute a task and return the result."""


class ClaudeAdapter(AgentAdapter):
    """Claude Code agent via claude-code-sdk."""

    def _build_options(
        self, worktree_path: str, allowed_dirs: list[str], model: str = "sonnet",
        project_context: str = "",
    ) -> ClaudeCodeOptions:
        """Build ClaudeCodeOptions with directory boundary enforcement."""
        if allowed_dirs:
            extra_dirs_clause = " and the following allowed directories: " + ", ".join(
                allowed_dirs
            )
        else:
            extra_dirs_clause = ""
        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=worktree_path, extra_dirs_clause=extra_dirs_clause,
            project_context=project_context,
        )
        return ClaudeCodeOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
            cwd=worktree_path,
            model=model,
            max_turns=25,
        )

    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
        allowed_dirs: list[str] | None = None,
        model: str = "sonnet",
        on_message: Callable | None = None,
        project_context: str = "",
    ) -> AgentResult:
        options = self._build_options(worktree_path, allowed_dirs or [], model=model, project_context=project_context)

        try:
            result = await asyncio.wait_for(
                sdk_query(prompt=task_prompt, options=options, on_message=on_message),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Agent timed out after %ds for worktree %s", timeout_seconds, worktree_path)
            files_changed = _get_changed_files(worktree_path)
            return AgentResult(
                success=False,
                files_changed=files_changed,
                summary=f"Agent timed out after {timeout_seconds}s",
                error=f"Timeout after {timeout_seconds}s",
            )
        files_changed = _get_changed_files(worktree_path)

        if result is None:
            return AgentResult(
                success=False,
                files_changed=files_changed,
                summary="No response from Claude SDK",
                error="SDK returned no result",
            )

        result_text = result.result or ""
        cost_usd = result.total_cost_usd or 0.0

        if result.is_error:
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
            cost_usd=cost_usd,
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
