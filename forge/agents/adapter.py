"""Agent adapter interface. Claude primary, others pluggable."""

import asyncio
import json
import logging
import os
import re
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from claude_code_sdk import ClaudeCodeOptions

from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.agents")


def _load_claude_md(project_dir: str) -> str | None:
    """Load CLAUDE.md from standard locations.

    Searches:
      1. {project_dir}/CLAUDE.md
      2. {project_dir}/.claude/CLAUDE.md

    Returns content as string, or None if not found.
    """
    for rel_path in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md")):
        full_path = os.path.join(project_dir, rel_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r") as f:
                    return f.read()
            except OSError:
                continue
    return None


def _build_question_protocol(autonomy: str = "balanced", remaining: int = 3) -> str:
    """Build the human interaction protocol section for agent system prompts."""
    if autonomy == "full":
        when_to_ask = "NEVER ask questions. Make your best judgment on all decisions."
    elif autonomy == "supervised":
        when_to_ask = (
            "Ask when uncertain about ANY implementation choice.\n"
            "This includes architecture, naming, patterns, and ambiguous requirements."
        )
    else:  # balanced
        when_to_ask = (
            "Ask when you are less than 80% confident about a decision that\n"
            "affects correctness. It is always better to pause for 30 seconds\n"
            "than to build the wrong thing for 10 minutes.\n\n"
            "ASK when:\n"
            "- The spec is ambiguous and you see multiple valid interpretations\n"
            "- You're about to make an architectural choice the spec doesn't specify\n"
            "- You found conflicting patterns in the codebase and aren't sure which to follow\n"
            "- You're about to delete, rename, or restructure something that other code depends on\n\n"
            "DON'T ASK when:\n"
            "- The spec is clear and you know exactly what to do\n"
            "- It's a naming, formatting, or minor style choice\n"
            "- You can verify your assumption by reading existing code\n\n"
            "EXAMPLES:\n"
            "- Spec says \"add caching\" but doesn't mention TTL or eviction strategy → ASK\n"
            "- Spec says \"add a login button to the nav bar\" and you can see the nav component → DON'T ASK\n"
            "- You're about to change a function signature that 12 other files import → ASK\n"
            "- You need to pick between two equivalent testing patterns → DON'T ASK"
        )

    return f"""## Human Interaction Protocol

Autonomy level: {autonomy} | Questions remaining: {remaining}

### When to ask:
{when_to_ask}

### Before asking:
Before emitting a question, briefly explain:
1. What you're working on
2. What you found that created the uncertainty
3. What options you see

Then ask your specific question with concrete suggestions.
This context helps the human give you a useful answer.

### How to ask:
When you need human input, output this JSON block as your FINAL message, then STOP:

FORGE_QUESTION:
{{
  "question": "Your specific question here",
  "context": "What you found that led to this question",
  "suggestions": ["Option A", "Option B"],
  "impact": "high"
}}

### Rules:
- You have {remaining} questions left. Use them wisely.
- ALWAYS provide 2-3 concrete suggestions.
- ALWAYS explain what you found that led to the question.
- NEVER ask open-ended "what should I do?" questions.
- If you hit 0 remaining, proceed with best judgment."""


AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a coding agent working on a specific task within the Forge orchestration system.

Your working directory is {cwd}. Do NOT read, write, or execute anything outside this directory{extra_dirs_clause}.

You have access to a git worktree isolated to your task. Write clean, tested code.

{project_context}

{conventions_block}

{claude_md_block}

{contracts_block}

{dependency_context}

{file_scope_block}

{question_protocol}

{working_effectively}

Rules:
- You MUST ONLY modify files listed in the File Scope section above (plus their related test files). Changes to other files are automatically reverted by the system.
- If Interface Contracts are provided above, you MUST implement them EXACTLY as specified. Do NOT rename fields, change types, or alter response shapes.
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
    session_id: str | None = None


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
        contracts_block: str = "",
        resume: str | None = None,
        autonomy: str = "balanced",
        questions_remaining: int = 3,
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
        allowed_files: list[str] | None = None,
        contracts_block: str = "",
        autonomy: str = "balanced",
        questions_remaining: int = 3,
        resume: str | None = None,
        project_dir: str | None = None,
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
        if allowed_files:
            files_list = "\n".join(f"- {f}" for f in allowed_files)
            file_scope_block = (
                "## File Scope (STRICT — enforced by the system)\n\n"
                "You are ONLY allowed to modify these files:\n"
                f"{files_list}\n\n"
                "ANY changes to files outside this list will be AUTOMATICALLY REVERTED "
                "before review. Do NOT modify, create, or delete any other files — "
                "it wastes your time and tokens.\n\n"
                "**Exception**: Test files that correspond to the source files above "
                "(e.g. `tests/test_<name>.py` or `<name>_test.py`) ARE allowed. "
                "You may create or modify test files for your in-scope source files."
            )
        else:
            file_scope_block = ""
        question_protocol = _build_question_protocol(autonomy, questions_remaining)

        # Load project instructions from CLAUDE.md
        claude_md_block = ""
        if project_dir:
            claude_md_content = _load_claude_md(project_dir)
            if claude_md_content:
                claude_md_block = (
                    "## Project Instructions (from CLAUDE.md)\n\n"
                    f"{claude_md_content}"
                )

        working_effectively = """## Working Effectively

- Use all available tools. If you need to look up API docs, use WebSearch.
  If you need to understand a library, read its source. Be resourceful.
- If tests fail, read the full error output. Diagnose the root cause.
  Fix it. Re-run. Don't guess — verify.
- Before editing a file, read it first. Understand the existing patterns.
  Follow them. Don't introduce new conventions.
- If you're unsure about something, explore first. Grep the codebase.
  Read related files. Build understanding before making changes.
- Commit your work when you reach a stable point. Small, focused commits
  are better than one giant commit at the end."""

        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=worktree_path, extra_dirs_clause=extra_dirs_clause,
            project_context=project_context,
            conventions_block=conventions_block,
            contracts_block=contracts_block,
            dependency_context=dependency_context,
            file_scope_block=file_scope_block,
            question_protocol=question_protocol,
            claude_md_block=claude_md_block,
            working_effectively=working_effectively,
        )
        return ClaudeCodeOptions(
            system_prompt=system_prompt,
            permission_mode="acceptEdits",
            cwd=worktree_path,
            model=model,
            max_turns=25,
            resume=resume,
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
        contracts_block: str = "",
        resume: str | None = None,
        autonomy: str = "balanced",
        questions_remaining: int = 3,
        project_dir: str | None = None,
    ) -> AgentResult:
        options = self._build_options(
            worktree_path, allowed_dirs or [], model=model,
            project_context=project_context,
            conventions_json=conventions_json,
            conventions_md=conventions_md,
            completed_deps=completed_deps,
            allowed_files=allowed_files,
            contracts_block=contracts_block,
            autonomy=autonomy,
            questions_remaining=questions_remaining,
            resume=resume,
            project_dir=project_dir,
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
                cost_usd=cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                session_id=result.session_id,
            )

        return AgentResult(
            success=True,
            files_changed=files_changed,
            summary=result_text[:500] if result_text else "Task completed",
            cost_usd=cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            session_id=result.session_id,
        )


def _get_changed_files(worktree_path: str) -> list[str]:
    """Get list of files changed in the worktree vs its base.

    Detects both uncommitted changes (working tree / staged) AND committed
    changes made by the agent.  Claude Code commits its work, so after an
    agent finishes all changes are typically committed — we need to diff
    against the branch point to find them.
    """
    files: set[str] = set()

    # 1. Uncommitted changes (working tree vs HEAD)
    unstaged = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=worktree_path, capture_output=True, text=True,
    )
    # 2. Staged but uncommitted changes (index vs HEAD)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=worktree_path, capture_output=True, text=True,
    )
    for output in (unstaged.stdout, staged.stdout):
        for line in output.strip().split("\n"):
            if line.strip():
                files.add(line.strip())

    # 3. Committed changes — agent's commits vs the branch point.
    #    Use the same rev-list heuristic as _get_diff_stats: count how many
    #    commits exist locally that aren't on any remote, then diff against
    #    HEAD~N to capture the agent's own files.
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path, capture_output=True, text=True,
    )
    try:
        commit_count = max(int(count_result.stdout.strip()), 1)
    except (ValueError, AttributeError):
        commit_count = 1

    base_ref = f"HEAD~{commit_count}"
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=worktree_path, capture_output=True, text=True,
    )
    if verify.returncode == 0:
        committed = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "HEAD"],
            cwd=worktree_path, capture_output=True, text=True,
        )
        for line in committed.stdout.strip().split("\n"):
            if line.strip():
                files.add(line.strip())

    return sorted(files)
