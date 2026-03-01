"""Merge worker. Rebases task branch onto main, detects conflicts."""

import subprocess
from dataclasses import dataclass, field


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

    def _rebase(self, branch: str, worktree_path: str | None = None) -> None:
        if worktree_path:
            # Rebase from within the worktree (branch is already checked out)
            result = subprocess.run(
                ["git", "rebase", self._main],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )
        else:
            result = subprocess.run(
                ["git", "rebase", self._main, branch],
                cwd=self._repo,
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            conflicts = self._find_conflicts()
            raise _RebaseConflict(files=conflicts)

    def _abort_rebase(self, worktree_path: str | None = None) -> None:
        cwd = worktree_path or self._repo
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=cwd,
            capture_output=True,
        )
        if not worktree_path:
            subprocess.run(
                ["git", "checkout", self._main],
                cwd=self._repo,
                capture_output=True,
            )

    def _fast_forward(self, branch: str) -> None:
        subprocess.run(
            ["git", "checkout", self._main],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "merge", "--ff-only", branch],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )

    def _find_conflicts(self) -> list[str]:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        return [f for f in result.stdout.strip().split("\n") if f]


class _RebaseConflict(Exception):
    def __init__(self, files: list[str]) -> None:
        self.files = files
