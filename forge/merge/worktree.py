"""Git worktree lifecycle management. One worktree per task for isolation."""

import logging
import os
import shutil
import subprocess

from forge.core.sanitize import validate_task_id

logger = logging.getLogger("forge.merge.worktree")


class WorktreeManager:
    """Creates, tracks, and removes git worktrees for tasks."""

    def __init__(self, repo_path: str, worktrees_dir: str) -> None:
        self._repo = repo_path
        self._worktrees_dir = worktrees_dir

    def _task_path(self, task_id: str) -> str:
        return os.path.join(self._worktrees_dir, task_id)

    def _branch_name(self, task_id: str) -> str:
        return f"forge/{task_id}"

    def _ensure_forge_gitignored(self) -> None:
        """Add .forge to the repo's .gitignore if not already present.

        Uses an atomic read-check-write pattern to avoid race conditions
        when multiple worktrees are created concurrently.
        """
        gitignore = os.path.join(self._repo, ".gitignore")
        entry = ".forge"
        if os.path.isfile(gitignore):
            with open(gitignore, encoding="utf-8") as f:
                content = f.read()
            lines = {line.strip() for line in content.splitlines()}
            if entry in lines or f"/{entry}" in lines or f"{entry}/" in lines:
                return
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write(f"\n{entry}\n")
        else:
            with open(gitignore, "w", encoding="utf-8") as f:
                f.write(f"{entry}\n")

    def create(self, task_id: str, base_ref: str | None = None) -> str:
        """Create a worktree for a task. Returns the worktree path.

        Args:
            base_ref: The git ref (branch or commit SHA) to base the new
                worktree on.  When running inside a pipeline this should be
                the **pipeline branch** (e.g. ``forge/pipeline-abc123``) so
                that dependent tasks see files created by already-merged
                dependencies.  If ``None``, Git defaults to the repo HEAD
                (which is typically ``main``).

        Handles repos with no commits by using ``--orphan`` flag so that
        each worktree branch starts as an independent root.
        """
        validate_task_id(task_id)
        path = self._task_path(task_id)
        if os.path.exists(path):
            raise ValueError(f"Worktree for '{task_id}' already exists: {path}")

        branch = self._branch_name(task_id)
        os.makedirs(self._worktrees_dir, exist_ok=True)

        # Ensure .forge is gitignored in the repo so worktrees don't show as untracked
        self._ensure_forge_gitignored()

        # Check if the repo has any commits — orphan worktrees needed if not
        has_commits = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self._repo,
                capture_output=True,
                timeout=60,
            ).returncode
            == 0
        )

        if has_commits:
            cmd = ["git", "worktree", "add", "-b", branch, path]
            # Base on the pipeline branch so dependent tasks inherit merged files
            if base_ref:
                cmd.append(base_ref)
        else:
            cmd = ["git", "worktree", "add", "--orphan", "-b", branch, path]

        try:
            subprocess.run(cmd, cwd=self._repo, check=True, capture_output=True, timeout=60)
        except subprocess.CalledProcessError:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            raise

        # Symlink dependency directories (node_modules, .venv, etc.) from the
        # source repo into the worktree so tools like eslint, pytest work
        # without a full install.
        for dep_dir in ("node_modules", ".venv", "venv"):
            src = os.path.join(self._repo, dep_dir)
            dst = os.path.join(path, dep_dir)
            if os.path.isdir(src) and not os.path.exists(dst):
                os.symlink(src, dst)

        return path

    def remove(self, task_id: str) -> None:
        """Remove a task's worktree and its branch.

        Handles already-removed worktrees gracefully (no-op if path is gone).
        Branch deletion failures are logged instead of raising so that
        callers always get a clean return even when the branch was already
        deleted or never created.
        """
        validate_task_id(task_id)
        path = self._task_path(task_id)

        # Gracefully handle already-removed worktrees
        if not os.path.exists(path):
            logger.debug("Worktree for '%s' already removed at %s", task_id, path)
        else:
            subprocess.run(
                ["git", "worktree", "remove", path, "--force"],
                cwd=self._repo,
                check=True,
                capture_output=True,
                timeout=60,
            )

        branch = self._branch_name(task_id)
        try:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self._repo,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "Failed to delete branch '%s' for task '%s': %s",
                branch,
                task_id,
                exc,
            )

    def list_active(self) -> list[str]:
        """Return task IDs with active worktrees."""
        if not os.path.isdir(self._worktrees_dir):
            return []
        return [
            name
            for name in os.listdir(self._worktrees_dir)
            if os.path.isdir(os.path.join(self._worktrees_dir, name))
        ]
