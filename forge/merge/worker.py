"""Merge worker. Rebases task branch onto main, detects conflicts."""

from __future__ import annotations

import logging
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

    def retry_merge(self, branch: str, worktree_path: str | None = None) -> MergeResult:
        """Retry a merge: abort any in-progress rebase, re-attempt rebase + ff merge.

        This is Tier 1 retry — no agent re-run, just git operations.
        Used when a merge failed due to a conflict that may resolve after
        another task has merged (making main advance).
        """
        # Abort any lingering rebase state
        self._abort_rebase(worktree_path)

        # Re-attempt the full merge sequence
        return self.merge(branch, worktree_path=worktree_path)

    def merge(self, branch: str, worktree_path: str | None = None) -> MergeResult:
        """Attempt to rebase branch onto main and fast-forward merge.

        Args:
            branch: The branch name to merge (e.g. "forge/task-1").
            worktree_path: If provided, run the rebase inside this worktree
                           (where the branch is already checked out) instead of
                           the main repo. This avoids "already checked out" errors.
        """
        try:
            self._rebase(branch, worktree_path)
        except _RebaseConflict as e:
            self._abort_rebase(worktree_path)
            conflict_desc = ", ".join(e.files) if e.files else "unknown files"
            return MergeResult(
                success=False,
                conflicting_files=e.files,
                error=f"Rebase conflict in: {conflict_desc}",
            )
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        try:
            self._fast_forward(branch)
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        return MergeResult(success=True)

    def prepare_for_resolution(
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
        self._abort_rebase(worktree_path)

        try:
            self._rebase(branch, worktree_path)
        except _RebaseConflict as e:
            # DON'T abort — leave rebase paused so the resolver can work
            conflict_desc = ", ".join(e.files) if e.files else "unknown files"
            return MergeResult(
                success=False,
                conflicting_files=e.files,
                error=f"Rebase paused for resolution: {conflict_desc}",
            )
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        # Rebase completed cleanly — no resolution needed
        return MergeResult(success=True)

    def _rebase(self, branch: str, worktree_path: str | None = None) -> None:
        if worktree_path:
            result = _run_git(
                ["rebase", self._main], cwd=worktree_path,
                check=False, description="rebase in worktree",
            )
        else:
            result = _run_git(
                ["rebase", self._main, branch], cwd=self._repo,
                check=False, description="rebase branch",
            )
        if result.returncode != 0:
            conflicts = self._find_conflicts(worktree_path)
            raise _RebaseConflict(files=conflicts)

    def _abort_rebase(self, worktree_path: str | None = None) -> None:
        cwd = worktree_path or self._repo
        _run_git(["rebase", "--abort"], cwd=cwd, check=False, description="abort rebase")

    def _fast_forward(self, branch: str) -> None:
        """Advance the merge-target branch ref to the task branch tip.

        Uses ``git update-ref`` instead of ``git checkout + merge`` so the
        user's working directory is never mutated.  This only works for
        fast-forward merges — which is guaranteed after a successful rebase.
        """
        task_sha = _run_git(
            ["rev-parse", branch], cwd=self._repo,
            check=True, description="resolve branch SHA",
        ).stdout.strip()
        _run_git(
            ["update-ref", f"refs/heads/{self._main}", task_sha],
            cwd=self._repo, check=True, description="fast-forward merge target",
        )

    def _find_conflicts(self, worktree_path: str | None = None) -> list[str]:
        result = _run_git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=worktree_path or self._repo,
            check=False, description="find conflict files",
        )
        return [f for f in result.stdout.strip().split("\n") if f]


class _RebaseConflict(Exception):
    def __init__(self, files: list[str]) -> None:
        self.files = files
