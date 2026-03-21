"""Module-level helper functions extracted from forge/core/daemon.py.

These utilities handle git operations, prompt construction, diff analysis,
and console output used by the Forge daemon orchestration loop.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import subprocess

from rich.table import Table

from forge.core.logging_config import make_console

logger = logging.getLogger("forge")
console = make_console()

_FORGE_QUESTION_MARKER = "FORGE_QUESTION:"


async def async_subprocess(
    cmd: list[str],
    cwd: str,
    *,
    timeout: float = 30,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command asynchronously, returning a CompletedProcess for API compat.

    On timeout, kills the process and raises ``asyncio.TimeoutError`` with a
    descriptive message.  Does **not** raise on non-zero exit — callers handle
    check logic themselves.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise asyncio.TimeoutError(
            f"Command {cmd} timed out after {timeout}s",
        )

    stdout_str = stdout_bytes.decode() if stdout_bytes else ""
    stderr_str = stderr_bytes.decode() if stderr_bytes else ""

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,  # type: ignore[arg-type]
        stdout=stdout_str,
        stderr=stderr_str,
    )


def _parse_forge_question(text: str | None) -> dict | None:
    """Parse a FORGE_QUESTION block from agent output.

    Returns dict with at least 'question' and 'suggestions' keys, or None.
    Only matches if the marker appears near the end of output (agent stopped to ask).
    """
    if not text:
        return None

    marker_idx = text.rfind(_FORGE_QUESTION_MARKER)
    if marker_idx == -1:
        return None

    after_marker = text[marker_idx + len(_FORGE_QUESTION_MARKER):].strip()

    # Check nothing substantial follows the JSON (agent continued working)
    # Strip markdown fences if present
    json_text = after_marker
    fence_match = re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", json_text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
    else:
        # Check if there's significant text after the JSON block
        # Find the closing brace
        brace_depth = 0
        json_end = -1
        for i, ch in enumerate(json_text):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        if json_end == -1:
            return None
        trailing = json_text[json_end:].strip()
        if len(trailing) > 20:  # significant trailing text = agent continued
            return None
        json_text = json_text[:json_end]

    try:
        data = _json.loads(json_text)
    except (_json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    if "question" not in data or not isinstance(data["question"], str):
        return None

    return data


def _extract_text(message) -> str | None:
    """Extract human-readable text from a claude-code-sdk message."""
    try:
        from claude_code_sdk import AssistantMessage, ResultMessage
    except ImportError:
        return None
    if isinstance(message, AssistantMessage):
        parts = []
        for block in (message.content or []):
            if hasattr(block, "text"):
                text = block.text.strip()
                # Skip empty, JSON blobs, and tool metadata
                if not text:
                    continue
                if text.startswith("{") or text.startswith("["):
                    continue
                parts.append(text)
        return "\n".join(parts) if parts else None
    if isinstance(message, ResultMessage):
        # ResultMessage.result is the final plan JSON — not human-readable.
        # Don't stream it to the TUI; the plan is consumed by the planner's parser.
        return None
    return None


def _extract_activity(message) -> str | None:
    """Extract human-readable activity from a claude-code-sdk message.

    Unlike ``_extract_text`` which only returns TextBlock content, this
    also formats ToolUseBlock messages as short activity descriptions
    (e.g. "📖 Reading src/models/user.py").  This makes planner and agent
    progress visible in the TUI even during tool-heavy exploration phases
    where no text is produced.
    """
    try:
        from claude_code_sdk import AssistantMessage, ResultMessage
    except ImportError:
        return None

    if isinstance(message, AssistantMessage):
        parts: list[str] = []
        for block in (message.content or []):
            # Text blocks — same filtering as _extract_text
            if hasattr(block, "text"):
                text = block.text.strip()
                if not text:
                    continue
                if text.startswith("{") or text.startswith("["):
                    continue
                parts.append(text)
            # Tool use blocks — show what tool is being called
            elif hasattr(block, "name"):
                tool = block.name
                inp = getattr(block, "input", {}) or {}
                label = _format_tool_activity(tool, inp)
                if label:
                    parts.append(label)
        return "\n".join(parts) if parts else None

    if isinstance(message, ResultMessage):
        # ResultMessage.result is the final plan JSON — not human-readable.
        return None
    return None


_TOOL_ICONS = {
    "Read": "📖",
    "Glob": "🔍",
    "Grep": "🔎",
    "Bash": "⚡",
    "Write": "✏️",
    "Edit": "✏️",
}


def _format_tool_activity(tool: str, inp: dict) -> str | None:
    """Format a tool use block as a short human-readable string."""
    icon = _TOOL_ICONS.get(tool, "🔧")
    if tool == "Read":
        path = inp.get("file_path") or inp.get("path", "")
        if path:
            # Show just filename and parent dir for brevity
            short = "/".join(path.rsplit("/", 2)[-2:]) if "/" in path else path
            return f"{icon} Reading {short}"
        return f"{icon} Reading file"
    if tool == "Glob":
        pattern = inp.get("pattern", "")
        return f"{icon} Searching: {pattern}" if pattern else f"{icon} Searching files"
    if tool == "Grep":
        pattern = inp.get("pattern", "")
        return f"{icon} Grep: {pattern[:60]}" if pattern else f"{icon} Searching code"
    if tool == "Bash":
        cmd = inp.get("command", "")
        if cmd:
            short = cmd[:80] + ("..." if len(cmd) > 80 else "")
            return f"{icon} {short}"
        return f"{icon} Running command"
    if tool in ("Write", "Edit"):
        path = inp.get("file_path", "")
        short = "/".join(path.rsplit("/", 2)[-2:]) if "/" in path else path
        return f"{icon} Editing {short}" if path else f"{icon} Editing file"
    return f"🔧 {tool}"


async def _get_current_branch(repo_path: str) -> str:
    """Get the current branch name of the repo.

    Falls back to 'main' if the branch can't be determined (e.g. detached
    HEAD or empty repo). Never returns the literal string 'HEAD' since
    that's not a valid branch name for merge targets.
    """
    result = await async_subprocess(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
    )
    branch = result.stdout.strip()
    # "HEAD" is returned for detached HEAD — not a valid branch name.
    # Empty string means the command failed (no commits yet).
    if branch and branch != "HEAD":
        return branch
    # Try symbolic-ref as fallback (works even before first commit)
    sym = await async_subprocess(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
    )
    sym_branch = sym.stdout.strip()
    return sym_branch if sym_branch else "main"


def _build_agent_prompt(title: str, description: str, files: list[str], agent_prompt_modifier: str = "") -> str:
    files_str = ", ".join(files) if files else "(no file restrictions)"
    prompt = (
        f"## Task: {title}\n\n"
        f"{description}\n\n"
        f"**Files in scope:** {files_str}\n"
    )
    if agent_prompt_modifier:
        prompt += "\n" + agent_prompt_modifier
    return prompt


def _build_retry_prompt(
    title: str, description: str, files: list[str],
    review_feedback: str, retry_number: int,
    agent_prompt_modifier: str = "",
) -> str:
    """Build a prompt for a retry that includes the review failure feedback.

    The agent gets the original task spec PLUS the reviewer's notes so it
    can fix the specific issues instead of starting from scratch.
    """
    files_str = ", ".join(files) if files else "(no file restrictions)"
    prompt = (
        f"## Task: {title} (Retry #{retry_number})\n\n"
        f"{description}\n\n"
        f"**Files in scope:** {files_str}\n\n"
        f"## Review Feedback\n\n"
        f"Your previous attempt was reviewed and needs fixes. "
        f"The worktree has your previous code — fix the issues, don't start over.\n\n"
        f"{review_feedback}\n"
    )
    if agent_prompt_modifier:
        prompt += "\n" + agent_prompt_modifier
    return prompt


async def _get_diff_vs_main(worktree_path: str, *, base_ref: str | None = None) -> str:
    """Get diff of the worktree branch vs its merge-base with the parent branch.

    Args:
        worktree_path: Path to the agent's git worktree.
        base_ref: Explicit base ref (e.g. the pipeline branch name) to diff
            against.  When provided, diffs ``base_ref..HEAD`` directly —
            this is reliable regardless of remote state and avoids the
            ``--not --remotes`` heuristic which breaks when the user's
            workflow (squash-merge + delete remote branch) leaves local
            commits unreachable from any remote.

    Falls back to the commit-count heuristic (``HEAD~N``) when *base_ref*
    is ``None`` or cannot be resolved.  Handles root commits (orphan
    branches from repos with no prior history) by diffing against the
    empty tree.

    Forge infrastructure files (.claude/, .forge/, .gitignore) are excluded
    from the diff so the LLM reviewer only sees agent work product.
    """
    # Pathspec exclusions: hide Forge infrastructure files from review diffs.
    # The agent may write .claude/settings.json (permissions) or modify
    # .gitignore — these are orchestrator artifacts, not task output.
    _DIFF_EXCLUDES = ["--", ":(exclude).claude/", ":(exclude).forge/", ":(exclude).gitignore"]

    # ── Fast path: explicit base ref ──────────────────────────────────
    if base_ref is not None:
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            result = await async_subprocess(
                ["git", "diff", base_ref, "HEAD"] + _DIFF_EXCLUDES,
                cwd=worktree_path,
            )
            return result.stdout
        logger.warning(
            "_get_diff_vs_main: base_ref %r not found in %s — "
            "falling back to commit-count heuristic",
            base_ref, worktree_path,
        )

    # ── Fallback: commit-count heuristic ──────────────────────────────
    count_result = await async_subprocess(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    # Check if HEAD~{commit_count} exists (won't if this is a root commit)
    heuristic_ref = f"HEAD~{commit_count}"
    verify = await async_subprocess(
        ["git", "rev-parse", "--verify", heuristic_ref],
        cwd=worktree_path,
    )

    if verify.returncode == 0:
        # Normal case: diff the agent's commits against their base
        result = await async_subprocess(
            ["git", "diff", heuristic_ref, "HEAD"] + _DIFF_EXCLUDES,
            cwd=worktree_path,
        )
    else:
        # Root commit (orphan branch / new repo): diff against empty tree
        empty_tree_result = await async_subprocess(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
        )
        empty_tree = empty_tree_result.stdout.strip()
        result = await async_subprocess(
            ["git", "diff", empty_tree, "HEAD"] + _DIFF_EXCLUDES,
            cwd=worktree_path,
        )

    return result.stdout


async def _resolve_ref(repo_path: str, ref: str) -> str | None:
    """Resolve a git ref (branch name) to its immutable commit SHA.

    Used to snapshot the pipeline branch *before* a merge so that
    ``_get_diff_stats`` can compute per-task stats against a fixed
    point rather than the (now-moved) branch tip.
    """
    result = await async_subprocess(
        ["git", "rev-parse", ref],
        cwd=repo_path,
    )
    return result.stdout.strip() if result.returncode == 0 else None


async def _get_diff_stats(worktree_path: str, pipeline_branch: str | None = None) -> dict[str, int]:
    """Get lines added/removed for this task's commits in its worktree.

    When ``pipeline_branch`` is provided the diff is computed as
    ``git diff --shortstat <pipeline_branch> HEAD``, which returns only the
    delta between the pipeline branch tip and this task's HEAD — i.e. only
    this task's own changes, not the cumulative total of all previously merged
    tasks.

    Falls back to the commit-count heuristic (``HEAD~N``) when the pipeline
    branch ref cannot be resolved, logging a warning so the degradation is
    visible in the logs.
    """
    if pipeline_branch is not None:
        # Verify that the pipeline branch ref exists in this worktree
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", pipeline_branch],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            result = await async_subprocess(
                ["git", "diff", "--shortstat", pipeline_branch, "HEAD"],
                cwd=worktree_path,
            )
            added, removed, files = 0, 0, 0
            if result.returncode == 0 and result.stdout.strip():
                m_files = re.search(r"(\d+) file", result.stdout)
                m_add = re.search(r"(\d+) insertion", result.stdout)
                m_del = re.search(r"(\d+) deletion", result.stdout)
                if m_files:
                    files = int(m_files.group(1))
                if m_add:
                    added = int(m_add.group(1))
                if m_del:
                    removed = int(m_del.group(1))
            return {"linesAdded": added, "linesRemoved": removed, "filesChanged": files}
        else:
            logger.warning(
                "_get_diff_stats: pipeline branch %r not found in %s — "
                "falling back to commit-count heuristic",
                pipeline_branch,
                worktree_path,
            )

    # Fallback: find how many commits the agent added on top of the base
    count_result = await async_subprocess(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    base_ref = f"HEAD~{commit_count}"
    verify = await async_subprocess(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=worktree_path,
    )

    if verify.returncode == 0:
        result = await async_subprocess(
            ["git", "diff", "--shortstat", base_ref, "HEAD"],
            cwd=worktree_path,
        )
    else:
        # Root commit (orphan branch / new repo): diff against empty tree
        empty_tree_result = await async_subprocess(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
        )
        empty_tree = empty_tree_result.stdout.strip()
        result = await async_subprocess(
            ["git", "diff", "--shortstat", empty_tree, "HEAD"],
            cwd=worktree_path,
        )

    added, removed, files = 0, 0, 0
    if result.returncode == 0 and result.stdout.strip():
        m_files = re.search(r"(\d+) file", result.stdout)
        m_add = re.search(r"(\d+) insertion", result.stdout)
        m_del = re.search(r"(\d+) deletion", result.stdout)
        if m_files:
            files = int(m_files.group(1))
        if m_add:
            added = int(m_add.group(1))
        if m_del:
            removed = int(m_del.group(1))
    return {"linesAdded": added, "linesRemoved": removed, "filesChanged": files}


async def _get_changed_files_vs_main(worktree_path: str, *, base_ref: str | None = None) -> list[str]:
    """Get list of files changed by the agent (not the entire feature branch).

    Args:
        worktree_path: Path to the agent's git worktree.
        base_ref: Explicit base ref (e.g. the pipeline branch name) to diff
            against.  See :func:`_get_diff_vs_main` for why this is preferred
            over the ``--not --remotes`` heuristic.
    """
    # ── Fast path: explicit base ref ──────────────────────────────────
    if base_ref is not None:
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            result = await async_subprocess(
                ["git", "diff", "--name-only", base_ref, "HEAD"],
                cwd=worktree_path,
            )
            return [f for f in result.stdout.strip().split("\n") if f.strip()]
        logger.warning(
            "_get_changed_files_vs_main: base_ref %r not found in %s — "
            "falling back to commit-count heuristic",
            base_ref, worktree_path,
        )

    # ── Fallback: commit-count heuristic ──────────────────────────────
    count_result = await async_subprocess(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    heuristic_ref = f"HEAD~{commit_count}"
    verify = await async_subprocess(
        ["git", "rev-parse", "--verify", heuristic_ref],
        cwd=worktree_path,
    )

    if verify.returncode == 0:
        result = await async_subprocess(
            ["git", "diff", "--name-only", heuristic_ref, "HEAD"],
            cwd=worktree_path,
        )
    else:
        # Root commit: diff against empty tree
        empty_tree_result = await async_subprocess(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
        )
        empty_tree = empty_tree_result.stdout.strip()
        result = await async_subprocess(
            ["git", "diff", "--name-only", empty_tree, "HEAD"],
            cwd=worktree_path,
        )

    return [f for f in result.stdout.strip().split("\n") if f.strip()]


def _load_conventions_md(project_dir: str) -> str | None:
    """Read ``.forge/conventions.md`` from the project directory.

    Returns the stripped file content, or ``None`` if the file doesn't
    exist or is empty.
    """
    filepath = os.path.join(project_dir, ".forge", "conventions.md")
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            content = fh.read().strip()
            return content if content else None
    except (OSError, FileNotFoundError):
        return None


async def _extract_implementation_summary(
    worktree_path: str,
    agent_summary: str,
    pipeline_branch: str | None = None,
) -> str:
    """Extract a brief (≤300 char) summary from completed agent work.

    Combines git commit messages with the agent's summary text to produce
    a concise description of what was implemented.  Falls back to a generic
    message when no useful information is available.
    """
    commit_messages: list[str] = []

    # Try explicit base-ref first (most accurate)
    if pipeline_branch is not None:
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", pipeline_branch],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            result = await async_subprocess(
                ["git", "log", "--format=%s", f"{pipeline_branch}..HEAD"],
                cwd=worktree_path,
            )
            if result.returncode == 0 and result.stdout.strip():
                commit_messages = [
                    line.strip()
                    for line in result.stdout.strip().splitlines()
                    if line.strip()
                ]

    # Fallback: recent local-only commits
    if not commit_messages:
        result = await async_subprocess(
            ["git", "log", "--format=%s", "--not", "--remotes", "-5"],
            cwd=worktree_path,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit_messages = [
                line.strip()
                for line in result.stdout.strip().splitlines()
                if line.strip()
            ]

    parts: list[str] = []
    if commit_messages:
        parts.append("; ".join(commit_messages))

    # Only include agent summary if it's not generic
    if agent_summary and agent_summary.strip().lower() != "task completed":
        parts.append(agent_summary.strip())

    if parts:
        summary = " | ".join(parts)
        return summary[:300]

    return "Task completed (no detailed summary available)"[:300]


def _print_status_table(tasks) -> None:
    table = Table(title="Forge Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("State")
    table.add_column("Agent")
    table.add_column("Retries")

    state_colors = {
        "todo": "white",
        "in_progress": "yellow",
        "in_review": "blue",
        "merging": "magenta",
        "done": "green",
        "error": "red",
        "cancelled": "dim",
    }

    for t in tasks:
        color = state_colors.get(t.state, "white")
        table.add_row(
            t.id,
            t.title,
            f"[{color}]{t.state}[/{color}]",
            t.assigned_agent or "-",
            str(t.retry_count),
        )

    console.print(table)


def _is_pytest_cmd(cmd: str) -> bool:
    """Check if a test command is pytest-based (can be scoped to specific files)."""
    return "pytest" in cmd.lower()


async def _find_related_test_files(
    worktree_path: str,
    changed_files: list[str],
    *,
    allowed_files: list[str] | None = None,
    base_ref: str | None = None,
) -> list[str] | tuple[list[str], list[str]]:
    """Find test files related to the changed source files.

    Handles two common Python test naming conventions:
    - Co-located: ``foo.py`` → ``foo_test.py`` (same directory)
    - Test directory: ``src/foo.py`` → ``tests/test_foo.py``

    Changed files that ARE test files are included directly.

    When *allowed_files* is provided, returns ``(in_scope, out_of_scope)``
    tuple. A test is in-scope if it appears in *allowed_files* OR was
    newly created (not on *base_ref*).

    When *allowed_files* is None (default), returns a flat list for backward compat.
    """
    test_files: set[str] = set()
    for f in changed_files:
        if not f.endswith(".py"):
            continue
        basename = os.path.basename(f)

        if basename.startswith("test_") or basename.endswith("_test.py"):
            if os.path.isfile(os.path.join(worktree_path, f)):
                test_files.add(f)
            continue

        co_located = f"{f[:-3]}_test.py"
        if os.path.isfile(os.path.join(worktree_path, co_located)):
            test_files.add(co_located)

        dirname = os.path.dirname(f)
        test_dir_path = os.path.join(dirname, "tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, test_dir_path)):
            test_files.add(test_dir_path)

        root_test_path = os.path.join("tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, root_test_path)):
            test_files.add(root_test_path)

    all_tests = sorted(test_files)

    if allowed_files is None:
        return all_tests

    allowed_set = set(allowed_files)

    new_files: set[str] = set()
    if base_ref:
        try:
            result = await async_subprocess(
                ["git", "diff", "--name-only", "--diff-filter=A", f"{base_ref}...HEAD"],
                cwd=worktree_path,
                timeout=10,
            )
            if result.returncode == 0:
                new_files = set(result.stdout.strip().splitlines())
        except Exception:
            logger.warning("Failed to detect newly created files for scope filtering")

    in_scope: list[str] = []
    out_of_scope: list[str] = []
    for tf in all_tests:
        if tf in allowed_set or tf in new_files:
            in_scope.append(tf)
        else:
            out_of_scope.append(tf)

    return in_scope, out_of_scope


def compute_worktree_path(
    workspace_dir: str, repo_id: str, task_id: str, *, repo_count: int = 1,
) -> str:
    """Compute worktree path for a task.

    Single-repo (repo_count=1, repo_id='default'): <workspace_dir>/.forge/worktrees/<task_id>
    Multi-repo (repo_count > 1): <workspace_dir>/.forge/worktrees/<repo_id>/<task_id>
    """
    if repo_count <= 1 and repo_id == "default":
        return os.path.join(workspace_dir, ".forge", "worktrees", task_id)
    return os.path.join(workspace_dir, ".forge", "worktrees", repo_id, task_id)


async def _run_git(
    args: list[str],
    cwd: str,
    *,
    check: bool = True,
    description: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run a git command with consistent logging and error handling.

    Args:
        args: Git arguments (e.g. ["rev-parse", "HEAD"]).
        cwd: Working directory.
        check: If True (default), raise on non-zero exit. If False, log
            a warning and return the result.
        description: Human-readable description for log messages.
    """
    cmd = ["git"] + args
    result = await async_subprocess(cmd, cwd=cwd)
    desc = description or " ".join(args[:3])
    if result.returncode != 0:
        if check:
            logger.error(
                "git %s failed (exit %d) in %s: %s",
                desc, result.returncode, cwd, result.stderr.strip(),
            )
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr,
            )
        else:
            logger.warning(
                "git %s returned %d in %s: %s",
                desc, result.returncode, cwd, result.stderr.strip(),
            )
    return result
