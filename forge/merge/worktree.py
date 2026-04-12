"""Git worktree lifecycle management. One worktree per task for isolation."""

import fcntl
import logging
import os
import shutil
import subprocess
import tempfile

from forge.core.daemon_helpers import compute_worktree_name
from forge.core.sanitize import validate_task_id

logger = logging.getLogger("forge.merge.worktree")


class WorktreeManager:
    """Creates, tracks, and removes git worktrees for tasks."""

    def __init__(
        self,
        repo_path: str,
        worktrees_dir: str,
        *,
        repo_id: str = "default",
        repo_count: int = 1,
    ) -> None:
        self._repo = repo_path
        self._worktrees_dir = worktrees_dir
        self._repo_id = repo_id
        self._repo_count = repo_count

    def _task_name(self, task_id: str) -> str:
        return compute_worktree_name(
            self._repo_id,
            task_id,
            repo_count=self._repo_count,
        )

    def _task_path(self, task_id: str) -> str:
        return os.path.join(self._worktrees_dir, self._task_name(task_id))

    def _branch_name(self, task_id: str) -> str:
        return f"forge/{task_id}"

    # Directories that must never be committed — virtual environments,
    # dependency caches, and build artifacts that agents may create.
    _GITIGNORE_REQUIRED_ENTRIES = (
        ".forge",
        ".venv",
        "venv",
        ".env",
        "node_modules",
        "__pycache__",
        "*.pyc",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
    )

    def _ensure_forge_gitignored(self) -> None:
        """Ensure the repo .gitignore contains entries for .forge, .venv, etc.

        Uses fcntl.flock() to serialize concurrent read-modify-write cycles
        and an atomic write-to-temp-then-rename pattern so concurrent
        worktree creations don't lose each other's entries.
        """
        gitignore = os.path.join(self._repo, ".gitignore")
        lock_path = os.path.join(self._repo, ".gitignore.lock")

        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            if os.path.isfile(gitignore):
                with open(gitignore, encoding="utf-8") as f:
                    content = f.read()
                existing = {line.strip().rstrip("/") for line in content.splitlines()}
            else:
                content = ""
                existing = set()

            missing = [
                e
                for e in self._GITIGNORE_REQUIRED_ENTRIES
                if e not in existing and f"/{e}" not in existing and f"{e}/" not in existing
            ]
            if not missing:
                return

            new_content = content.rstrip("\n") + "\n" + "\n".join(missing) + "\n"

            # Atomic write: write to a temp file in the same directory, then
            # rename (rename is atomic on POSIX when src and dst are on the
            # same filesystem).
            fd, tmp_path = tempfile.mkstemp(
                dir=self._repo, prefix=".gitignore.tmp", suffix=""
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_content)
                os.replace(tmp_path, gitignore)
            except BaseException:
                # Clean up the temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

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

        Note: git subprocess calls use ``run_in_executor`` internally
        (via ``_run_git``) to avoid blocking the event loop when called
        from async code.
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

        # Delete stale branch from a previous attempt (retry scenario).
        # Without this, `git worktree add -b` fails with "branch already exists".
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
            cwd=self._repo,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("Deleting stale branch %s from previous attempt", branch)
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self._repo,
                capture_output=True,
                timeout=10,
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

        # Ensure the worktree's .gitignore has the required exclusions.
        # The base ref may predate the repo-root .gitignore update above,
        # so we must also patch the worktree copy directly.
        self._ensure_worktree_gitignore(path)

        return path

    def _ensure_worktree_gitignore(self, worktree_path: str) -> None:
        """Ensure the worktree has a .gitignore with env/cache exclusions.

        This prevents agents from committing .venv, node_modules, etc.
        even if the base ref has no .gitignore.
        """
        gitignore = os.path.join(worktree_path, ".gitignore")
        if os.path.isfile(gitignore):
            with open(gitignore, encoding="utf-8") as f:
                content = f.read()
            existing = {line.strip().rstrip("/") for line in content.splitlines()}
        else:
            content = ""
            existing = set()

        missing = [
            e
            for e in self._GITIGNORE_REQUIRED_ENTRIES
            if e not in existing and f"/{e}" not in existing and f"{e}/" not in existing
        ]
        if not missing:
            return

        new_content = content.rstrip("\n") + "\n" + "\n".join(missing) + "\n"
        with open(gitignore, "w", encoding="utf-8") as f:
            f.write(new_content)

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

    async def async_create(self, task_id: str, base_ref: str | None = None) -> str:
        """Async version of ``create()`` — runs in an executor to avoid blocking."""
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.create, task_id, base_ref)

    async def async_remove(self, task_id: str) -> None:
        """Async version of ``remove()`` — runs in an executor to avoid blocking."""
        import asyncio

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.remove, task_id)

    def list_active(self) -> list[str]:
        """Return task IDs with active worktrees."""
        if not os.path.isdir(self._worktrees_dir):
            return []
        return [
            name
            for name in os.listdir(self._worktrees_dir)
            if os.path.isdir(os.path.join(self._worktrees_dir, name))
        ]
