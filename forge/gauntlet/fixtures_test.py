"""Tests for forge.gauntlet.fixtures."""

import os

import pytest

from forge.gauntlet.fixtures import (
    create_fixture_workspace,
    create_workspace_toml,
    setup_forge_config,
)


@pytest.fixture
def workspace(tmp_path):
    return create_fixture_workspace(str(tmp_path))


class TestCreateFixtureWorkspace:
    def test_returns_three_repos(self, workspace):
        assert set(workspace.keys()) == {"backend", "frontend", "shared-types"}

    def test_all_paths_exist(self, workspace):
        for path in workspace.values():
            assert os.path.isdir(path)

    def test_backend_files(self, workspace):
        backend = workspace["backend"]
        assert os.path.isfile(os.path.join(backend, "app.py"))
        assert os.path.isfile(os.path.join(backend, "test_app.py"))
        assert os.path.isfile(os.path.join(backend, "pyproject.toml"))

    def test_backend_has_bug(self, workspace):
        with open(os.path.join(workspace["backend"], "app.py")) as f:
            content = f.read()
        assert "a / b" in content  # division by zero bug present

    def test_frontend_files(self, workspace):
        frontend = workspace["frontend"]
        assert os.path.isfile(os.path.join(frontend, "index.js"))
        assert os.path.isfile(os.path.join(frontend, "package.json"))

    def test_frontend_has_bug(self, workspace):
        with open(os.path.join(workspace["frontend"], "index.js")) as f:
            content = f.read()
        assert "data.value" in content  # wrong field name

    def test_shared_types_files(self, workspace):
        shared = workspace["shared-types"]
        assert os.path.isfile(os.path.join(shared, "types.py"))
        assert os.path.isfile(os.path.join(shared, "__init__.py"))

    def test_shared_types_models(self, workspace):
        with open(os.path.join(workspace["shared-types"], "types.py")) as f:
            content = f.read()
        assert "CalculationRequest" in content
        assert "CalculationResponse" in content

    def test_repos_are_git_repos(self, workspace):
        for path in workspace.values():
            assert os.path.isdir(os.path.join(path, ".git"))

    def test_repos_have_initial_commit(self, workspace):
        import subprocess

        for path in workspace.values():
            result = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=path,
                capture_output=True,
                text=True,
            )
            assert "Initial commit" in result.stdout


class TestCreateWorkspaceToml:
    def test_creates_toml(self, workspace, tmp_path):
        toml_path = create_workspace_toml(str(tmp_path), workspace)
        assert os.path.isfile(toml_path)
        assert toml_path.endswith("workspace.toml")

    def test_toml_content(self, workspace, tmp_path):
        toml_path = create_workspace_toml(str(tmp_path), workspace)
        with open(toml_path) as f:
            content = f.read()
        assert "[workspace]" in content
        for repo_id in workspace:
            assert repo_id in content


class TestSetupForgeConfig:
    def test_creates_forge_toml(self, workspace):
        backend = workspace["backend"]
        setup_forge_config(backend)
        forge_toml = os.path.join(backend, ".forge", "forge.toml")
        assert os.path.isfile(forge_toml)

    def test_disables_checks(self, workspace):
        backend = workspace["backend"]
        setup_forge_config(backend)
        with open(os.path.join(backend, ".forge", "forge.toml")) as f:
            content = f.read()
        assert 'test_cmd = ""' in content
        assert 'lint_cmd = ""' in content
