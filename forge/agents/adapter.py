"""Agent adapter interface. Claude primary, others pluggable."""

import asyncio
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from claude_code_sdk import ClaudeCodeOptions

from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.agents")


# ── Agent permission rules ──────────────────────────────────────────
# Passed directly to ClaudeCodeOptions as allowed_tools / disallowed_tools.
# No file is written to the worktree — permissions flow through the SDK,
# keeping the git working tree clean for rebase.
#
# Design: allowlist only.  The agent runs in an isolated worktree so
# there's nothing dangerous to hit.  We allow the tools agents actually
# need (git, file ops, common project commands) and deny only the
# clearly dangerous ones (network, sudo, permissions, process killing).

AGENT_ALLOWED_TOOLS = [
            # Git operations — agents must be able to commit their own work
            "Bash(git *)",
            # File operations — refactoring needs rm, mv, cp, mkdir
            "Bash(rm *)",
            "Bash(mv *)",
            "Bash(cp *)",
            "Bash(mkdir *)",
            "Bash(touch *)",
            # Read/inspect — agents need to read files and search
            "Bash(ls *)",
            "Bash(cat *)",
            "Bash(head *)",
            "Bash(tail *)",
            "Bash(find *)",
            "Bash(wc *)",
            "Bash(pwd)",
            "Bash(echo *)",
            "Bash(which *)",
            # Build/test tools — agents verify their own work
            "Bash(python *)",
            "Bash(python3 *)",
            "Bash(pip *)",
            "Bash(pytest *)",
            "Bash(npm *)",
            "Bash(npx *)",
            "Bash(node *)",
            "Bash(make *)",
            "Bash(cargo *)",
            "Bash(go *)",
            "Bash(yarn *)",
            "Bash(pnpm *)",
            "Bash(bun *)",
            "Bash(ruff *)",
            "Bash(eslint *)",
            "Bash(tsc *)",
            "Bash(javac *)",
            "Bash(gradle *)",
            "Bash(mvn *)",
            "Bash(dotnet *)",
            "Bash(swift *)",
            "Bash(rustc *)",
            "Bash(ruby *)",
            "Bash(bundle *)",
            "Bash(rake *)",
            # Shell utilities — agents chain commands, source venvs
            "Bash(source *)",
            "Bash(cd *)",
            "Bash(sort *)",
            "Bash(uniq *)",
            "Bash(xargs *)",
            "Bash(sed *)",
            "Bash(awk *)",
            "Bash(tr *)",
            "Bash(cut *)",
            "Bash(diff *)",
            "Bash(grep *)",
            "Bash(jq *)",
            "Bash(basename *)",
            "Bash(dirname *)",
            "Bash(realpath *)",
            "Bash(readlink *)",
]

AGENT_DISALLOWED_TOOLS = [
            # Network — no exfiltration or downloads
            "Bash(curl *)",
            "Bash(wget *)",
            "Bash(ssh *)",
            "Bash(scp *)",
            "Bash(rsync *)",
            "Bash(nc *)",
            "Bash(ncat *)",
            "Bash(telnet *)",
            "Bash(ftp *)",
            # Privilege escalation
            "Bash(sudo *)",
            "Bash(su *)",
            "Bash(doas *)",
            # Permission changes
            "Bash(chmod *)",
            "Bash(chown *)",
            "Bash(chgrp *)",
            # Process management
            "Bash(kill *)",
            "Bash(pkill *)",
            "Bash(killall *)",
            # Container/VM escape
            "Bash(docker *)",
            "Bash(podman *)",
            # System modification
            "Bash(systemctl *)",
            "Bash(service *)",
            "Bash(mount *)",
            "Bash(umount *)",
            # Environment pollution (could affect other agents)
    "Bash(export *)",
    "Bash(unset *)",
    # Sensitive file reads
    "Read(.env)",
    "Read(.env.*)",
]


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
            "You SHOULD ask questions when uncertain. It is always better to\n"
            "pause for 30 seconds than to build the wrong thing for 10 minutes.\n"
            "Asking a good question is a sign of strength, not weakness.\n\n"
            "ASK when:\n"
            "- The spec is ambiguous and you see multiple valid interpretations\n"
            "- You're about to make an architectural choice the spec doesn't specify\n"
            "- You found conflicting patterns in the codebase and aren't sure which to follow\n"
            "- You're about to delete, rename, or restructure something that other code depends on\n"
            "- The task description is vague or underspecified — ask for clarification\n"
            "- You're unsure about the intended behavior or expected output\n"
            "- There are multiple reasonable approaches and the choice meaningfully affects the result\n\n"
            "DON'T ASK when:\n"
            "- The spec is clear and you know exactly what to do\n"
            "- It's a naming, formatting, or minor style choice\n"
            "- You can verify your assumption by reading existing code\n\n"
            "EXAMPLES:\n"
            "- Spec says \"add caching\" but doesn't mention TTL or eviction strategy → ASK\n"
            "- Spec says \"add a login button to the nav bar\" and you can see the nav component → DON'T ASK\n"
            "- You're about to change a function signature that 12 other files import → ASK\n"
            "- You need to pick between two equivalent testing patterns → DON'T ASK\n"
            "- Task says \"improve error handling\" but doesn't say which errors → ASK\n"
            "- Task says \"add tests for the auth module\" and the module is clear → DON'T ASK"
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
- You have {remaining} questions available. Don't hesitate to use them — an answered question prevents wasted work.
- ALWAYS provide 2-3 concrete suggestions.
- ALWAYS explain what you found that led to the question.
- NEVER ask open-ended "what should I do?" questions.
- If you hit 0 remaining, proceed with best judgment."""


AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a senior software engineer working on a focused task.

Your working directory is {cwd}.{extra_dirs_clause}

{project_context}

{conventions_block}

{lessons_block}

{claude_md_block}

{contracts_block}

{dependency_context}

{file_scope_block}

{question_protocol}

## Boundaries
- Only modify files listed in File Scope above (plus their test files). Out-of-scope changes are auto-reverted.
- If contracts are specified above, implement them exactly as defined.
- Follow existing code style — read before you write.
- Do NOT run: git push, git branch, git rebase, git checkout, git reset. The orchestrator manages branches.
- You CAN and SHOULD run: git diff, git status, git log to verify your own work.

## Command Retry Discipline
- If a shell command fails, READ the error message before retrying. Understand WHY it failed.
- NEVER retry the same command (or trivial variation) more than 3 times. If it failed 3 times, the approach is wrong.
- After 2 failures of the same command: STOP, diagnose the root cause, and try a fundamentally different approach.
- Common traps to avoid:
  - A CLI flag doesn't exist? Don't try flag variations — read the tool's --help or docs.
  - Tests failing? Read the error output. Don't just re-run hoping for a different result.
  - Import errors? Check what's actually installed, don't retry the same import.
- If you cannot make something work after 3 attempts, document what you tried and move on. An honest "this didn't work because X" is infinitely better than burning 20 retries on the same dead end.

## Turn Budget
You have {max_turns} turns for this task. Manage them wisely:
- If you're past turn {wrap_up_turn} and not done, STOP coding and write a status summary of what's done, what's remaining, and what the next agent should do.
- An honest handoff is better than a half-finished hack.

## Before You Finish
1. Run git diff to review all your changes
2. Run tests if a test command is available
3. Stage and commit: git add -A && git commit --no-verify -m '<type>: <short summary>'
   Use conventional commits (feat, fix, refactor, test, docs, chore).
   Max 72 chars. Describe WHAT changed, don't copy the task title.
   ALWAYS use --no-verify to skip pre-commit hooks (the orchestrator runs its own review).
   If the commit fails for any reason, don't worry — the orchestrator will auto-commit your changes.
4. If nothing meaningful to do (files don't exist, task already done), make no changes."""


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
        lessons_block: str = "",
        resume: str | None = None,
        autonomy: str = "balanced",
        questions_remaining: int = 3,
        agent_max_turns: int = 75,
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
        agent_max_turns: int = 75,
        lessons_block: str = "",
    ) -> ClaudeCodeOptions:
        """Build ClaudeCodeOptions with directory boundary enforcement."""
        if allowed_dirs:
            extra_dirs_clause = " Also allowed: " + ", ".join(allowed_dirs)
        else:
            extra_dirs_clause = ""
        conventions_block = _build_conventions_block(conventions_json, conventions_md)
        dependency_context = _build_dependency_context(completed_deps)
        if allowed_files:
            files_list = "\n".join(f"- {f}" for f in allowed_files)
            file_scope_block = (
                "## File Scope\n\n"
                "You may only modify these files:\n"
                f"{files_list}\n\n"
                "Out-of-scope changes are automatically reverted before review.\n\n"
                "**Exception**: Test files for the source files above "
                "(e.g. `tests/test_<name>.py` or `<name>_test.py`) are also allowed."
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

        max_turns = agent_max_turns
        wrap_up_turn = max(max_turns - 5, max_turns * 3 // 4)

        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=worktree_path, extra_dirs_clause=extra_dirs_clause,
            project_context=project_context,
            conventions_block=conventions_block,
            contracts_block=contracts_block,
            dependency_context=dependency_context,
            file_scope_block=file_scope_block,
            question_protocol=question_protocol,
            claude_md_block=claude_md_block,
            lessons_block=lessons_block,
            max_turns=max_turns,
            wrap_up_turn=wrap_up_turn,
        )
        return ClaudeCodeOptions(
            system_prompt=system_prompt,
            permission_mode="acceptEdits",
            allowed_tools=list(AGENT_ALLOWED_TOOLS),
            disallowed_tools=list(AGENT_DISALLOWED_TOOLS),
            cwd=worktree_path,
            model=model,
            max_turns=max_turns,
            resume=resume,
        )

    # NOTE: Permissions are passed directly via ClaudeCodeOptions
    # (allowed_tools / disallowed_tools).  No file is written to the
    # worktree, so there is nothing to pollute git status or block rebase.

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
        agent_max_turns: int = 75,
        lessons_block: str = "",
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
            agent_max_turns=agent_max_turns,
            lessons_block=lessons_block,
        )

        try:
            result = await asyncio.wait_for(
                sdk_query(prompt=task_prompt, options=options, on_message=on_message),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Agent timed out after %ds for worktree %s", timeout_seconds, worktree_path)
            # Re-raise so the caller's finally block can clean up the
            # worktree via worktree_mgr.remove(task_id).  Swallowing the
            # timeout here previously left zombie worktrees on disk.
            raise
        files_changed = await _get_changed_files(worktree_path)

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

        # Keep full result text — truncating here silently drops FORGE_QUESTION
        # markers that appear after 500 chars, preventing question detection.
        return AgentResult(
            success=True,
            files_changed=files_changed,
            summary=result_text or "Task completed",
            cost_usd=cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            session_id=result.session_id,
        )


async def _get_changed_files(worktree_path: str) -> list[str]:
    """Get list of files changed in the worktree vs its base.

    Detects both uncommitted changes (working tree / staged) AND committed
    changes made by the agent.  Claude Code commits its work, so after an
    agent finishes all changes are typically committed — we need to diff
    against the branch point to find them.
    """
    files: set[str] = set()

    async def _run_git(*args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode or 0, stdout.decode()

    # 1. Uncommitted changes (working tree vs HEAD)
    _, unstaged_out = await _run_git("diff", "--name-only", "HEAD")
    # 2. Staged but uncommitted changes (index vs HEAD)
    _, staged_out = await _run_git("diff", "--cached", "--name-only")
    for output in (unstaged_out, staged_out):
        for line in output.strip().split("\n"):
            if line.strip():
                files.add(line.strip())

    # 3. Committed changes — agent's commits vs the branch point.
    #    Use the same rev-list heuristic as _get_diff_stats: count how many
    #    commits exist locally that aren't on any remote, then diff against
    #    HEAD~N to capture the agent's own files.
    _, count_out = await _run_git("rev-list", "--count", "HEAD", "--not", "--remotes")
    try:
        commit_count = max(int(count_out.strip()), 1)
    except (ValueError, AttributeError):
        commit_count = 1

    base_ref = f"HEAD~{commit_count}"
    rc, _ = await _run_git("rev-parse", "--verify", base_ref)
    if rc == 0:
        _, committed_out = await _run_git("diff", "--name-only", base_ref, "HEAD")
        for line in committed_out.strip().split("\n"):
            if line.strip():
                files.add(line.strip())

    return sorted(files)
