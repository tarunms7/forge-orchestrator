"""Shared pytest fixtures for integration tests."""
import subprocess
import pytest


@pytest.fixture
def make_git_repo(tmp_path):
    """Factory fixture that creates temporary git repositories."""

    def _make(name: str, files: dict[str, str] | None = None):
        repo_path = tmp_path / name
        repo_path.mkdir(parents=True, exist_ok=True)

        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True,
        )

        if files:
            for rel_path, content in files.items():
                file_path = repo_path / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)
        else:
            (repo_path / "README.md").write_text(f"# {name}\n")

        subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=repo_path, capture_output=True, check=True,
        )

        return str(repo_path)

    yield _make


@pytest.fixture
def workspace_dir(tmp_path):
    """Fixture that creates a workspace directory with .forge/ structure."""
    (tmp_path / ".forge").mkdir()
    (tmp_path / ".forge" / "worktrees").mkdir()
    return str(tmp_path)
