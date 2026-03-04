"""Agent adapter interface. Claude primary, others pluggable."""

import asyncio
import json
import logging
import re
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

{conventions_block}

{dependency_context}

Rules:
- Only modify files listed in your task specification
- Follow existing code style and patterns — see the conventions section above
- Write tests for any new functionality
- Commit your changes with a SHORT conventional commit message (max 72 chars) — use feat/fix/refactor/test/docs/chore prefix and describe WHAT changed, not the task title
- If you encounter an error, fix it rather than giving up
- If image file paths are mentioned in the task description, use the Read tool to view them (images are readable)"""


def _build_conventions_block(
    conventions_json: str | None, conventions_md: str | None,
) -> str:
    """Merge user conventions (.forge/conventions.md) with planner-extracted conventions.

    User conventions (conventions_md) have highest authority and come first.
    Planner entries (conventions_json) are only appended for categories not already
    covered by user conventions. Checks both ``## heading`` and ``**heading**``
    patterns case-insensitively.

    Returns empty string if both inputs are None/empty.
    """
    sections: list[str] = []

    # Collect user-convention headings for dedup
    covered_headings: set[str] = set()
    if conventions_md and conventions_md.strip():
        sections.append(conventions_md.strip())
        # Extract headings from ## Heading and **Heading** patterns
        for match in re.finditer(r"^##\s+(.+)$", conventions_md, re.MULTILINE):
            covered_headings.add(match.group(1).strip().lower())
        for match in re.finditer(r"\*\*(.+?)\*\*", conventions_md):
            covered_headings.add(match.group(1).strip().lower())

    if conventions_json and conventions_json.strip():
        try:
            parsed = json.loads(conventions_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse conventions_json, skipping planner conventions")
            parsed = None

        if isinstance(parsed, dict):
            for category, content in parsed.items():
                if category.strip().lower() not in covered_headings:
                    sections.append(f"**{category}**: {content}")
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    for category, content in item.items():
                        if category.strip().lower() not in covered_headings:
                            sections.append(f"**{category}**: {content}")
                elif isinstance(item, str):
                    sections.append(f"- {item}")

    if not sections:
        return ""

    body = "\n\n".join(sections)
    return f"## Project Conventions\n\n{body}"


def _build_dependency_context(completed_deps: list[dict] | None) -> str:
    """Build a formatted section describing completed dependency tasks.

    Each dict should have keys: task_id, title, implementation_summary (str|None),
    files_changed (list[str]).

    Returns empty string if no deps provided.
    """
    if not completed_deps:
        return ""

    parts: list[str] = [
        "## Completed Dependencies",
        "",
        "The following tasks have already been completed and may affect your work:",
    ]

    for dep in completed_deps:
        task_id = dep.get("task_id", "unknown")
        title = dep.get("title", "Untitled")
        summary = dep.get("implementation_summary")
        files = dep.get("files_changed", [])

        parts.append("")
        parts.append(f"### Task: {title} ({task_id})")
        parts.append(f"**What was done:** {summary or 'No summary available'}")
        parts.append("**Files modified:**")
        if files:
            for f in files:
                parts.append(f"- {f}")
        else:
            parts.append("- (none)")

    return "\n".join(parts)


@dataclass
class AgentResult:
    """Outcome of an agent task execution."""

    success: bool
    files_changed: list[str]
    summary: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
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
        conventions_json: str | None = None,
        conventions_md: str | None = None,
        completed_deps: list[dict] | None = None,
    ) -> AgentResult:
        """Execute a task and return the result."""


class ClaudeAdapter(AgentAdapter):
    """Claude Code agent via claude-code-sdk."""

    def _build_options(
        self, worktree_path: str, allowed_dirs: list[str], model: str = "sonnet",
        project_context: str = "",
        conventions_json: str | None = None,
        conventions_md: str | None = None,
        completed_deps: list[dict] | None = None,
    ) -> ClaudeCodeOptions:
        """Build ClaudeCodeOptions with directory boundary enforcement."""
        if allowed_dirs:
            extra_dirs_clause = " and the following allowed directories: " + ", ".join(
                allowed_dirs
            )
        else:
            extra_dirs_clause = ""
        conventions_block = _build_conventions_block(conventions_json, conventions_md)
        dependency_context = _build_dependency_context(completed_deps)
        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=worktree_path, extra_dirs_clause=extra_dirs_clause,
            project_context=project_context,
            conventions_block=conventions_block,
            dependency_context=dependency_context,
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
        conventions_json: str | None = None,
        conventions_md: str | None = None,
        completed_deps: list[dict] | None = None,
    ) -> AgentResult:
        options = self._build_options(
            worktree_path, allowed_dirs or [], model=model,
            project_context=project_context,
            conventions_json=conventions_json,
            conventions_md=conventions_md,
            completed_deps=completed_deps,
        )

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

        result_text = result.result_text
        cost_usd = result.cost_usd

        if result.is_error:
            return AgentResult(
                success=False,
                files_changed=files_changed,
                summary=result_text,
                error=result_text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

        return AgentResult(
            success=True,
            files_changed=files_changed,
            summary=result_text[:500] if result_text else "Task completed",
            cost_usd=cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
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
