"""Project management service — git init, clone, list, validate."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("forge.project_manager")


class ProjectManager:
    """Manages Forge project directories.

    Each project is a git repository with a ``.forge`` metadata directory
    inside the configured ``projects_dir``.

    Args:
        projects_dir: Root directory where projects are stored.
    """

    def __init__(self, projects_dir: Path) -> None:
        self.projects_dir = Path(projects_dir)

    async def create_project(self, name: str) -> Path:
        """Create a new project with git init, .forge dir, and initial commit.

        Args:
            name: Project directory name.

        Returns:
            Path to the created project directory.

        Raises:
            ValueError: If a project with the same name already exists.
        """
        project_path = self.projects_dir / name

        if project_path.exists():
            raise ValueError(f"Project '{name}' already exists")

        project_path.mkdir(parents=True, exist_ok=True)

        # Initialise git repo
        await self._run_git(["git", "init"], cwd=project_path)

        # Create .forge metadata directory
        forge_dir = project_path / ".forge"
        forge_dir.mkdir(exist_ok=True)

        # Initial commit so the repo has a valid HEAD
        await self._run_git(
            ["git", "add", "."],
            cwd=project_path,
        )
        await self._run_git(
            ["git", "commit", "-m", "Initial commit", "--allow-empty"],
            cwd=project_path,
            env_override={"GIT_AUTHOR_NAME": "Forge", "GIT_AUTHOR_EMAIL": "forge@local",
                          "GIT_COMMITTER_NAME": "Forge", "GIT_COMMITTER_EMAIL": "forge@local"},
        )

        logger.info("Created project: %s", project_path)
        return project_path

    async def clone_project(self, url: str, name: str) -> Path:
        """Clone a remote repository and set up .forge metadata.

        Args:
            url: Git remote URL (or local path) to clone from.
            name: Name for the cloned project directory.

        Returns:
            Path to the cloned project directory.

        Raises:
            ValueError: If a project with the same name already exists.
            RuntimeError: If the git clone command fails.
        """
        project_path = self.projects_dir / name

        if project_path.exists():
            raise ValueError(f"Project '{name}' already exists")

        self.projects_dir.mkdir(parents=True, exist_ok=True)

        await self._run_git(
            ["git", "clone", url, str(project_path)],
            cwd=self.projects_dir,
        )

        # Create .forge metadata directory
        forge_dir = project_path / ".forge"
        forge_dir.mkdir(exist_ok=True)

        logger.info("Cloned project: %s from %s", project_path, url)
        return project_path

    async def list_projects(self) -> list[str]:
        """List all valid project names in the projects directory.

        Returns only directories that contain a ``.git`` directory (i.e.,
        valid git repositories).

        Returns:
            Sorted list of project directory names.
        """
        if not self.projects_dir.exists():
            return []

        projects: list[str] = []
        for entry in sorted(self.projects_dir.iterdir()):
            if entry.is_dir() and (entry / ".git").exists():
                projects.append(entry.name)

        return projects

    async def validate_repo(self, path: Path) -> bool:
        """Check whether a path is a valid git repository.

        Args:
            path: Directory to validate.

        Returns:
            True if the path exists and is a git repo, False otherwise.
        """
        path = Path(path)
        if not path.exists():
            return False
        if not (path / ".git").exists():
            return False
        return True

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    async def _run_git(
        cmd: list[str],
        *,
        cwd: Path,
        env_override: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command asynchronously.

        Args:
            cmd: Command and arguments.
            cwd: Working directory.
            env_override: Extra environment variables to set.

        Returns:
            CompletedProcess result.

        Raises:
            RuntimeError: If the command exits with a non-zero code.
        """
        import os

        env = os.environ.copy()
        if env_override:
            env.update(env_override)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                env=env,
            ),
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Git command failed: {' '.join(cmd)}\n"
                f"stderr: {result.stderr.strip()}"
            )

        return result
