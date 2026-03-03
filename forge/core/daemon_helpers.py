"""Module-level helper functions extracted from forge/core/daemon.py.

These utilities handle git operations, prompt construction, diff analysis,
and console output used by the Forge daemon orchestration loop.
"""

import logging
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
                parts.append(block.text)
        return "\n".join(parts) if parts else None
    if isinstance(message, ResultMessage):
        return message.result if message.result else None
    return None


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


def _build_agent_prompt(title: str, description: str, files: list[str]) -> str:
    return (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"Files to create/modify: {', '.join(files)}\n\n"
        "Instructions:\n"
        "1. Implement this task completely\n"
        "2. Write clean, working code\n"
        "3. When done, stage and commit all changes with: git add -A && git commit -m 'feat: <description>'\n"
        "4. Make sure you actually commit — the system checks for committed changes"
    )


def _build_retry_prompt(
    title: str, description: str, files: list[str],
    review_feedback: str, retry_number: int,
) -> str:
    """Build a prompt for a retry that includes the review failure feedback.

    The agent gets the original task spec PLUS the reviewer's notes so it
    can fix the specific issues instead of starting from scratch.
    """
    return (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"Files to create/modify: {', '.join(files)}\n\n"
        f"=== IMPORTANT: This is RETRY #{retry_number} ===\n\n"
        f"Your previous implementation was reviewed and REJECTED. "
        f"The worktree already contains your previous changes. "
        f"DO NOT start from scratch — fix the specific issues below.\n\n"
        f"Review feedback from the reviewer:\n"
        f"---\n"
        f"{review_feedback}\n"
        f"---\n\n"
        "Instructions:\n"
        "1. Read the review feedback above carefully\n"
        "2. Look at your existing code (it's already in the worktree)\n"
        "3. Fix ONLY the issues the reviewer flagged\n"
        "4. Make sure your code actually works — run it if possible\n"
        "5. Stage and commit your fixes: git add -A && git commit -m 'fix: address review feedback'\n"
        "6. Make sure you actually commit — the system checks for committed changes"
    )


def _get_diff_vs_main(worktree_path: str) -> str:
    """Get diff of the worktree branch vs its merge-base with the parent branch.

    Uses ``git rev-list`` to count how many commits the agent added,
    then diffs against ``HEAD~N``.  Handles root commits (orphan branches
    from repos with no prior history) by diffing against the empty tree.
    """
    # Try to find how many commits the agent added on top of the base
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
    base_ref = f"HEAD~{commit_count}"
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    if verify.returncode == 0:
        # Normal case: diff the agent's commits against their base
        result = subprocess.run(
            ["git", "diff", base_ref, "HEAD"],
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


def _get_changed_files_vs_main(worktree_path: str) -> list[str]:
    """Get list of files changed by the agent (not the entire feature branch)."""
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
            ["git", "diff", "--name-only", base_ref, "HEAD"],
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
