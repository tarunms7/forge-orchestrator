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

    def merge(self, branch: str) -> MergeResult:
        """Attempt to rebase branch onto main and fast-forward merge."""
        try:
            self._rebase(branch)
        except _RebaseConflict as e:
            self._abort_rebase()
            return MergeResult(success=False, conflicting_files=e.files)
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        try:
            self._fast_forward(branch)
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        return MergeResult(success=True)

    def _rebase(self, branch: str) -> None:
        result = subprocess.run(
            ["git", "rebase", self._main, branch],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            conflicts = self._find_conflicts()
            raise _RebaseConflict(files=conflicts)

    def _abort_rebase(self) -> None:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=self._repo,
            capture_output=True,
        )
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
