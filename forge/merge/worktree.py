"""Git worktree lifecycle management. One worktree per task for isolation."""

import os
import subprocess


class WorktreeManager:
    """Creates, tracks, and removes git worktrees for tasks."""

    def __init__(self, repo_path: str, worktrees_dir: str) -> None:
        self._repo = repo_path
        self._worktrees_dir = worktrees_dir

    def _task_path(self, task_id: str) -> str:
        return os.path.join(self._worktrees_dir, task_id)

    def _branch_name(self, task_id: str) -> str:
        return f"forge/{task_id}"

    def create(self, task_id: str) -> str:
        """Create a worktree for a task. Returns the worktree path."""
        path = self._task_path(task_id)
        if os.path.exists(path):
            raise ValueError(f"Worktree for '{task_id}' already exists: {path}")

        branch = self._branch_name(task_id)
        os.makedirs(self._worktrees_dir, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, path],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        return path

    def remove(self, task_id: str) -> None:
        """Remove a task's worktree and its branch."""
        path = self._task_path(task_id)
        if not os.path.exists(path):
            raise ValueError(f"Worktree for '{task_id}' does not exist")

        subprocess.run(
            ["git", "worktree", "remove", path, "--force"],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        branch = self._branch_name(task_id)
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=self._repo,
            capture_output=True,
        )

    def list_active(self) -> list[str]:
        """Return task IDs with active worktrees."""
        if not os.path.isdir(self._worktrees_dir):
            return []
        return [
            name for name in os.listdir(self._worktrees_dir)
            if os.path.isdir(os.path.join(self._worktrees_dir, name))
        ]
