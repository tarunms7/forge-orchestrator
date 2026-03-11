"""Module-level helper functions extracted from forge/core/daemon.py.

These utilities handle git operations, prompt construction, diff analysis,
and console output used by the Forge daemon orchestration loop.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

from rich.console import Console
from rich.table import Table

logger = logging.getLogger("forge")
console = Console()


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
        return message.result if message.result else None
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
        return message.result if message.result else None
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


def _get_current_branch(repo_path: str) -> str:
    """Get the current branch name of the repo.

    Falls back to 'main' if the branch can't be determined (e.g. detached
    HEAD or empty repo). Never returns the literal string 'HEAD' since
    that's not a valid branch name for merge targets.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    branch = result.stdout.strip()
    # "HEAD" is returned for detached HEAD — not a valid branch name.
    # Empty string means the command failed (no commits yet).
    if branch and branch != "HEAD":
        return branch
    # Try symbolic-ref as fallback (works even before first commit)
    sym = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    sym_branch = sym.stdout.strip()
    return sym_branch if sym_branch else "main"


def _build_agent_prompt(title: str, description: str, files: list[str], agent_prompt_modifier: str = "") -> str:
    prompt = (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"Files you MUST ONLY modify (changes to other files will be auto-reverted): {', '.join(files)}\n\n"
        "Instructions:\n"
        "1. Implement this task completely\n"
        "2. Write clean, working code\n"
        "3. When done, stage and commit all changes with: git add -A && git commit -m '<type>: <short summary>'\n"
        "   - Use conventional commit types: feat, fix, refactor, test, docs, chore\n"
        "   - Write a SHORT commit message (max 72 chars) that describes WHAT you actually changed — do NOT copy the task title or description verbatim\n"
        "   - Good: 'feat: add JWT token refresh endpoint'\n"
        "   - Bad: 'feat: Build a REST API with JWT auth, user registration, and integration tests'\n"
        "4. Make sure you actually commit — the system checks for committed changes"
    )
    if agent_prompt_modifier:
        prompt += agent_prompt_modifier
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
    prompt = (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"=== IMPORTANT: This is RETRY #{retry_number} ===\n\n"
        f"Your previous implementation was reviewed and REJECTED.\n"
        f"The worktree still contains your previous in-scope changes. "
        f"Fix the specific issues the reviewer flagged — do NOT start over.\n\n"
        f"CRITICAL FILE SCOPE: You MUST ONLY modify these files: {', '.join(files)}\n"
        f"Changes to any other file will be AUTOMATICALLY REVERTED by the system.\n\n"
        f"Review feedback from the reviewer:\n"
        f"---\n"
        f"{review_feedback}\n"
        f"---\n\n"
        "Instructions:\n"
        "1. Read the review feedback above carefully\n"
        "2. Look at your existing code in the worktree and fix the reviewer's issues\n"
        "3. Fix ONLY the issues the reviewer flagged — do NOT modify files outside your scope\n"
        "4. Make sure your code actually works — run it if possible\n"
        "5. Stage and commit your fixes with a short, descriptive message:\n"
        "   git add -A && git commit -m '<type>: <short summary of what you fixed>'\n"
        "   - Use conventional commit types: feat, fix, refactor, test, docs, chore\n"
        "   - Write a SHORT commit message (max 72 chars) describing WHAT you actually fixed — do NOT use the generic 'fix: address review feedback'\n"
        "   - Good: 'fix: handle None return in token refresh'\n"
        "   - Bad: 'fix: address review feedback'\n"
        "6. Make sure you actually commit — the system checks for committed changes"
    )
    if agent_prompt_modifier:
        prompt += agent_prompt_modifier
    return prompt


def _get_diff_vs_main(worktree_path: str, *, base_ref: str | None = None) -> str:
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
    """
    # ── Fast path: explicit base ref ──────────────────────────────────
    if base_ref is not None:
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if verify.returncode == 0:
            result = subprocess.run(
                ["git", "diff", base_ref, "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
            return result.stdout
        logger.warning(
            "_get_diff_vs_main: base_ref %r not found in %s — "
            "falling back to commit-count heuristic",
            base_ref, worktree_path,
        )

    # ── Fallback: commit-count heuristic ──────────────────────────────
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    # Check if HEAD~{commit_count} exists (won't if this is a root commit)
    heuristic_ref = f"HEAD~{commit_count}"
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", heuristic_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    if verify.returncode == 0:
        # Normal case: diff the agent's commits against their base
        result = subprocess.run(
            ["git", "diff", heuristic_ref, "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
    else:
        # Root commit (orphan branch / new repo): diff against empty tree
        empty_tree = subprocess.run(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        ).stdout.strip()
        result = subprocess.run(
            ["git", "diff", empty_tree, "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )

    return result.stdout


def _resolve_ref(repo_path: str, ref: str) -> str | None:
    """Resolve a git ref (branch name) to its immutable commit SHA.

    Used to snapshot the pipeline branch *before* a merge so that
    ``_get_diff_stats`` can compute per-task stats against a fixed
    point rather than the (now-moved) branch tip.
    """
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _get_diff_stats(worktree_path: str, pipeline_branch: str | None = None) -> dict[str, int]:
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
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", pipeline_branch],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if verify.returncode == 0:
            result = subprocess.run(
                ["git", "diff", "--shortstat", pipeline_branch, "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
            added, removed = 0, 0
            if result.returncode == 0 and result.stdout.strip():
                m_add = re.search(r"(\d+) insertion", result.stdout)
                m_del = re.search(r"(\d+) deletion", result.stdout)
                if m_add:
                    added = int(m_add.group(1))
                if m_del:
                    removed = int(m_del.group(1))
            return {"linesAdded": added, "linesRemoved": removed}
        else:
            logger.warning(
                "_get_diff_stats: pipeline branch %r not found in %s — "
                "falling back to commit-count heuristic",
                pipeline_branch,
                worktree_path,
            )

    # Fallback: find how many commits the agent added on top of the base
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    base_ref = f"HEAD~{commit_count}"
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    if verify.returncode == 0:
        result = subprocess.run(
            ["git", "diff", "--shortstat", base_ref, "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
    else:
        # Root commit (orphan branch / new repo): diff against empty tree
        empty_tree = subprocess.run(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        ).stdout.strip()
        result = subprocess.run(
            ["git", "diff", "--shortstat", empty_tree, "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )

    added, removed = 0, 0
    if result.returncode == 0 and result.stdout.strip():
        m_add = re.search(r"(\d+) insertion", result.stdout)
        m_del = re.search(r"(\d+) deletion", result.stdout)
        if m_add:
            added = int(m_add.group(1))
        if m_del:
            removed = int(m_del.group(1))
    return {"linesAdded": added, "linesRemoved": removed}


def _get_changed_files_vs_main(worktree_path: str, *, base_ref: str | None = None) -> list[str]:
    """Get list of files changed by the agent (not the entire feature branch).

    Args:
        worktree_path: Path to the agent's git worktree.
        base_ref: Explicit base ref (e.g. the pipeline branch name) to diff
            against.  See :func:`_get_diff_vs_main` for why this is preferred
            over the ``--not --remotes`` heuristic.
    """
    # ── Fast path: explicit base ref ──────────────────────────────────
    if base_ref is not None:
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if verify.returncode == 0:
            result = subprocess.run(
                ["git", "diff", "--name-only", base_ref, "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
            return [f for f in result.stdout.strip().split("\n") if f.strip()]
        logger.warning(
            "_get_changed_files_vs_main: base_ref %r not found in %s — "
            "falling back to commit-count heuristic",
            base_ref, worktree_path,
        )

    # ── Fallback: commit-count heuristic ──────────────────────────────
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    heuristic_ref = f"HEAD~{commit_count}"
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", heuristic_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    if verify.returncode == 0:
        result = subprocess.run(
            ["git", "diff", "--name-only", heuristic_ref, "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
    else:
        # Root commit: diff against empty tree
        empty_tree = subprocess.run(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        ).stdout.strip()
        result = subprocess.run(
            ["git", "diff", "--name-only", empty_tree, "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
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


def _extract_implementation_summary(
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
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", pipeline_branch],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if verify.returncode == 0:
            result = subprocess.run(
                ["git", "log", "--format=%s", f"{pipeline_branch}..HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                commit_messages = [
                    line.strip()
                    for line in result.stdout.strip().splitlines()
                    if line.strip()
                ]

    # Fallback: recent local-only commits
    if not commit_messages:
        result = subprocess.run(
            ["git", "log", "--format=%s", "--not", "--remotes", "-5"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
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


def _find_related_test_files(worktree_path: str, changed_files: list[str]) -> list[str]:
    """Find test files related to the changed source files.

    Handles two common Python test naming conventions:
    - Co-located: ``foo.py`` → ``foo_test.py`` (same directory)
    - Test directory: ``src/foo.py`` → ``tests/test_foo.py``

    Changed files that ARE test files are included directly.
    """
    test_files: set[str] = set()
    for f in changed_files:
        if not f.endswith(".py"):
            continue
        basename = os.path.basename(f)

        # If the changed file IS a test file, include it directly
        if basename.startswith("test_") or basename.endswith("_test.py"):
            if os.path.isfile(os.path.join(worktree_path, f)):
                test_files.add(f)
            continue

        # Co-located convention: foo.py → foo_test.py
        co_located = f"{f[:-3]}_test.py"
        if os.path.isfile(os.path.join(worktree_path, co_located)):
            test_files.add(co_located)

        # Test directory convention: src/foo.py → src/tests/test_foo.py
        dirname = os.path.dirname(f)
        test_dir_path = os.path.join(dirname, "tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, test_dir_path)):
            test_files.add(test_dir_path)

        # Root tests/ convention: src/foo.py → tests/test_foo.py
        root_test_path = os.path.join("tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, root_test_path)):
            test_files.add(root_test_path)

    return sorted(test_files)


def _run_git(
    args: list[str],
    cwd: str,
    *,
    check: bool = True,
    description: str = "",
) -> subprocess.CompletedProcess:
    """Run a git command with consistent logging and error handling.

    Args:
        args: Git arguments (e.g. ["rev-parse", "HEAD"]).
        cwd: Working directory.
        check: If True (default), raise on non-zero exit. If False, log
            a warning and return the result.
        description: Human-readable description for log messages.
    """
    cmd = ["git"] + args
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
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
