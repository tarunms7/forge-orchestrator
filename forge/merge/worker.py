"""Merge worker. Rebases task branch onto main, detects conflicts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from forge.core.daemon_helpers import _run_git

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Outcome of a merge attempt."""

    success: bool
    conflicting_files: list[str] = field(default_factory=list)
    error: str | None = None


class MergeWorker:
    """Handles rebasing and merging task branches into main."""

    def __init__(self, repo_path: str, main_branch: str = "master") -> None:
        self._repo = repo_path
        self._main = main_branch

    async def retry_merge(self, branch: str, worktree_path: str | None = None) -> MergeResult:
        """Retry a merge: abort any in-progress rebase, re-attempt rebase + ff merge.

        This is Tier 1 retry — no agent re-run, just git operations.
        Used when a merge failed due to a conflict that may resolve after
        another task has merged (making main advance).
        """
        # Abort any lingering rebase state
        await self._abort_rebase(worktree_path)

        # Re-attempt the full merge sequence
        return await self.merge(branch, worktree_path=worktree_path)

    async def merge(self, branch: str, worktree_path: str | None = None) -> MergeResult:
        """Attempt to rebase branch onto main and fast-forward merge.

        Args:
            branch: The branch name to merge (e.g. "forge/task-1").
            worktree_path: If provided, run the rebase inside this worktree
                           (where the branch is already checked out) instead of
                           the main repo. This avoids "already checked out" errors.
        """
        try:
            await self._rebase(branch, worktree_path)
        except _RebaseConflict as e:
            await self._abort_rebase(worktree_path)
            if e.files:
                conflict_desc = ", ".join(e.files)
            elif e.stderr:
                conflict_desc = f"(stderr) {e.stderr[:200]}"
            else:
                conflict_desc = (
                    f"unknown files (raw: {e.stderr[:150]})"
                    if e.stderr
                    else "unknown files (no stderr)"
                )
            return MergeResult(
                success=False,
                conflicting_files=e.files,
                error=f"Rebase conflict in: {conflict_desc}",
            )
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        try:
            await self._fast_forward(branch)
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        return MergeResult(success=True)

    async def prepare_for_resolution(
        self, branch: str, worktree_path: str | None = None
    ) -> MergeResult:
        """Start a rebase, leaving it paused on conflict for Tier 2 resolution.

        Unlike ``merge()``, this does **not** abort the rebase when conflicts
        occur.  The Tier 2 resolver agent needs the rebase to be in-progress
        so that conflict markers (``<<<<<<<``, ``=======``, ``>>>>>>>``) are
        present in the working-tree files.

        After the resolver does ``git add`` + ``git rebase --continue``,
        call ``merge()`` again — the rebase will be a no-op (already
        completed) and ``_fast_forward()`` will advance the merge target.
        """
        # Clean any stale rebase state first
        await self._abort_rebase(worktree_path)

        try:
            await self._rebase(branch, worktree_path)
        except _RebaseConflict as e:
            # DON'T abort — leave rebase paused so the resolver can work
            if e.files:
                conflict_desc = ", ".join(e.files)
            elif e.stderr:
                conflict_desc = f"(stderr) {e.stderr[:200]}"
            else:
                conflict_desc = (
                    f"unknown files (raw: {e.stderr[:150]})"
                    if e.stderr
                    else "unknown files (no stderr)"
                )
            return MergeResult(
                success=False,
                conflicting_files=e.files,
                error=f"Rebase paused for resolution: {conflict_desc}",
            )
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        # Rebase completed cleanly — no resolution needed
        return MergeResult(success=True)

    async def _rebase(self, branch: str, worktree_path: str | None = None) -> None:
        if worktree_path:
            result = await _run_git(
                ["rebase", self._main],
                cwd=worktree_path,
                check=False,
                description="rebase in worktree",
            )
        else:
            result = await _run_git(
                ["rebase", self._main, branch],
                cwd=self._repo,
                check=False,
                description="rebase branch",
            )
        if result.returncode != 0:
            conflicts = await self._find_conflicts(worktree_path)
            if not conflicts:
                # git diff --diff-filter=U found nothing — parse stderr
                # for conflict file names as fallback
                conflicts = _parse_conflict_files_from_stderr(result.stderr)
            stderr_snippet = result.stderr.strip()[:300] if result.stderr else ""
            raise _RebaseConflict(
                files=conflicts,
                stderr=stderr_snippet,
            )

    async def _abort_rebase(self, worktree_path: str | None = None) -> None:
        cwd = worktree_path or self._repo
        await _run_git(["rebase", "--abort"], cwd=cwd, check=False, description="abort rebase")

    async def _fast_forward(self, branch: str) -> None:
        """Advance the merge-target branch ref to the task branch tip.

        Uses ``git update-ref`` instead of ``git checkout + merge`` so the
        user's working directory is never mutated.  This only works for
        fast-forward merges — which is guaranteed after a successful rebase.
        """
        task_sha = (
            await _run_git(
                ["rev-parse", branch],
                cwd=self._repo,
                check=True,
                description="resolve branch SHA",
            )
        ).stdout.strip()
        await _run_git(
            ["update-ref", f"refs/heads/{self._main}", task_sha],
            cwd=self._repo,
            check=True,
            description="fast-forward merge target",
        )

    async def _find_conflicts(self, worktree_path: str | None = None) -> list[str]:
        result = await _run_git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=worktree_path or self._repo,
            check=False,
            description="find conflict files",
        )
        return [f for f in result.stdout.strip().split("\n") if f]


def _parse_conflict_files_from_stderr(stderr: str) -> list[str]:
    """Extract conflicting file paths from git rebase stderr output.

    Git outputs several CONFLICT formats depending on the type:
        CONFLICT (content): Merge conflict in path/to/file.py
        CONFLICT (add/add): Merge conflict in path/to/other.py
        CONFLICT (modify/delete): file.py deleted in HEAD and modified in <sha>
        CONFLICT (rename/delete): old.py renamed ... in HEAD, deleted in <sha>
        CONFLICT (rename/rename): file.py renamed to a.py in ... and to b.py in ...

    We use multiple patterns to catch all variants.
    """
    if not stderr:
        return []

    patterns = [
        # "Merge conflict in <path>"  (content, add/add)
        re.compile(r"CONFLICT\s*\([^)]*\):\s*Merge conflict in\s+(.+)"),
        # "modify/delete: <path> deleted in ..."
        re.compile(r"CONFLICT\s*\(modify/delete\):\s+(\S+)\s+deleted in"),
        # "rename/delete: <path> renamed ..."
        re.compile(r"CONFLICT\s*\(rename/delete\):\s+(\S+)"),
        # "rename/rename: <path> renamed to ..."
        re.compile(r"CONFLICT\s*\(rename/rename\):\s+(\S+)"),
    ]
    files: list[str] = []
    seen: set[str] = set()
    for line in stderr.splitlines():
        for pattern in patterns:
            m = pattern.search(line)
            if m:
                path = m.group(1).strip()
                if path not in seen:
                    seen.add(path)
                    files.append(path)
                break  # one match per line
    return files


class _RebaseConflict(Exception):
    def __init__(self, files: list[str], stderr: str = "") -> None:
        self.files = files
        self.stderr = stderr
