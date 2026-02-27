"""Tests for ProjectManager service."""

import pytest
from pathlib import Path


@pytest.fixture
def projects_dir(tmp_path):
    """Provide a temporary directory for project storage."""
    return tmp_path / "projects"


@pytest.fixture
def manager(projects_dir):
    """Create a ProjectManager with a temp projects directory."""
    from forge.api.services.project_manager import ProjectManager

    return ProjectManager(projects_dir=projects_dir)


class TestCreateProject:
    """Tests for ProjectManager.create_project."""

    async def test_create_project_creates_git_dir(self, manager, projects_dir):
        """create_project should initialise a git repo (.git directory)."""
        result = await manager.create_project("my-project")
        project_path = projects_dir / "my-project"

        assert result == project_path
        assert (project_path / ".git").exists()

    async def test_create_project_creates_forge_dir(self, manager, projects_dir):
        """create_project should create a .forge metadata directory."""
        await manager.create_project("my-project")
        project_path = projects_dir / "my-project"

        assert (project_path / ".forge").is_dir()

    async def test_create_project_makes_initial_commit(self, manager, projects_dir):
        """create_project should make an initial commit."""
        import subprocess

        await manager.create_project("my-project")
        project_path = projects_dir / "my-project"

        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "initial" in result.stdout.lower() or len(result.stdout.strip()) > 0

    async def test_create_duplicate_project_raises(self, manager):
        """Creating a project with the same name twice should raise ValueError."""
        await manager.create_project("dup-project")
        with pytest.raises(ValueError, match="already exists"):
            await manager.create_project("dup-project")


class TestListProjects:
    """Tests for ProjectManager.list_projects."""

    async def test_list_projects_empty(self, manager):
        """list_projects on empty dir should return empty list."""
        result = await manager.list_projects()
        assert result == []

    async def test_list_projects_returns_created(self, manager):
        """list_projects should return names of created projects."""
        await manager.create_project("alpha")
        await manager.create_project("beta")

        result = await manager.list_projects()
        assert sorted(result) == ["alpha", "beta"]

    async def test_list_projects_ignores_non_repos(self, manager, projects_dir):
        """list_projects should skip directories that are not git repos."""
        projects_dir.mkdir(parents=True, exist_ok=True)
        (projects_dir / "not-a-repo").mkdir()

        await manager.create_project("real-repo")
        result = await manager.list_projects()
        assert result == ["real-repo"]


class TestValidateRepo:
    """Tests for ProjectManager.validate_repo."""

    async def test_validate_valid_repo(self, manager):
        """validate_repo should return True for a valid git repo."""
        path = await manager.create_project("valid-repo")
        assert await manager.validate_repo(path) is True

    async def test_validate_nonexistent_path(self, manager):
        """validate_repo should return False for a path that doesn't exist."""
        assert await manager.validate_repo(Path("/nonexistent/path")) is False

    async def test_validate_non_repo_directory(self, manager, projects_dir):
        """validate_repo should return False for a dir without .git."""
        plain_dir = projects_dir / "plain"
        plain_dir.mkdir(parents=True)
        assert await manager.validate_repo(plain_dir) is False


class TestCloneProject:
    """Tests for ProjectManager.clone_project."""

    async def test_clone_project_creates_repo(self, manager, projects_dir):
        """clone_project should clone a repo and create .forge dir."""
        # First create a bare repo to clone from
        import subprocess

        source = projects_dir / "_source"
        source.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare", str(source)], check=True,
                       capture_output=True)

        result = await manager.clone_project(str(source), "cloned-project")
        assert (result / ".git").exists()
        assert (result / ".forge").is_dir()
