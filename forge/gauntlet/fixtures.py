"""Fixture workspace creation for gauntlet scenarios."""

from __future__ import annotations

import os
import subprocess
import textwrap


def _run_git(cwd: str, *args: str) -> None:
    """Run a git command in the given directory."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _init_repo(path: str) -> None:
    """Initialize a git repo with user config and 'main' as default branch."""
    os.makedirs(path, exist_ok=True)
    _run_git(path, "init", "-b", "main")
    _run_git(path, "config", "user.email", "gauntlet@forge.test")
    _run_git(path, "config", "user.name", "Forge Gauntlet")


def _write(path: str, content: str) -> None:
    """Write content to a file, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))


def create_fixture_workspace(base_dir: str) -> dict[str, str]:
    """Create the canonical 3-repo fixture workspace for gauntlet testing.

    Returns a dict mapping repo_id -> absolute path:
        backend, frontend, shared-types
    """
    repos: dict[str, str] = {}

    # --- backend ---
    backend_path = os.path.join(base_dir, "backend")
    _init_repo(backend_path)
    repos["backend"] = backend_path

    _write(
        os.path.join(backend_path, "app.py"),
        """\
        from flask import Flask, request, jsonify

        app = Flask(__name__)


        @app.route("/calculate", methods=["POST"])
        def calculate():
            data = request.get_json()
            a = data["a"]
            b = data["b"]
            # BUG: division by zero when b is 0
            result = a / b
            return jsonify({"result": result})


        if __name__ == "__main__":
            app.run()
        """,
    )

    _write(
        os.path.join(backend_path, "test_app.py"),
        """\
        import pytest
        from app import app


        @pytest.fixture
        def client():
            app.config["TESTING"] = True
            with app.test_client() as c:
                yield c


        def test_calculate(client):
            resp = client.post("/calculate", json={"a": 10, "b": 2})
            assert resp.status_code == 200
            assert resp.get_json()["result"] == 5.0


        def test_calculate_zero_division(client):
            \"\"\"This test exposes the division-by-zero bug.\"\"\"
            resp = client.post("/calculate", json={"a": 10, "b": 0})
            assert resp.status_code == 400
        """,
    )

    _write(
        os.path.join(backend_path, "pyproject.toml"),
        """\
        [project]
        name = "backend"
        version = "0.1.0"
        requires-python = ">=3.10"
        dependencies = ["flask"]

        [tool.pytest.ini_options]
        testpaths = ["."]
        """,
    )

    _run_git(backend_path, "add", ".")
    _run_git(backend_path, "commit", "-m", "Initial commit")

    # --- frontend ---
    frontend_path = os.path.join(base_dir, "frontend")
    _init_repo(frontend_path)
    repos["frontend"] = frontend_path

    _write(
        os.path.join(frontend_path, "index.js"),
        """\
        // BUG: imports 'value' but the shared type uses 'result'
        import { CalculationRequest } from '../shared-types/types';

        async function calculate(a, b) {
          const resp = await fetch('/calculate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ a, b }),
          });
          const data = await resp.json();
          return data.value; // BUG: should be data.result
        }

        module.exports = { calculate };
        """,
    )

    _write(
        os.path.join(frontend_path, "package.json"),
        """\
        {
          "name": "frontend",
          "version": "0.1.0",
          "main": "index.js"
        }
        """,
    )

    _run_git(frontend_path, "add", ".")
    _run_git(frontend_path, "commit", "-m", "Initial commit")

    # --- shared-types ---
    shared_path = os.path.join(base_dir, "shared-types")
    _init_repo(shared_path)
    repos["shared-types"] = shared_path

    _write(
        os.path.join(shared_path, "types.py"),
        """\
        from pydantic import BaseModel


        class CalculationRequest(BaseModel):
            a: float
            b: float


        class CalculationResponse(BaseModel):
            result: float
        """,
    )

    _write(
        os.path.join(shared_path, "__init__.py"),
        """\
        from .types import CalculationRequest, CalculationResponse

        __all__ = ["CalculationRequest", "CalculationResponse"]
        """,
    )

    _run_git(shared_path, "add", ".")
    _run_git(shared_path, "commit", "-m", "Initial commit")

    return repos


def create_workspace_toml(base_dir: str, repos: dict[str, str]) -> str:
    """Create a workspace.toml pointing to the fixture repos.

    Returns the absolute path to the created workspace.toml.
    """
    lines = ["[workspace]\n"]
    for repo_id, repo_path in repos.items():
        lines.append(f'[workspace.repos."{repo_id}"]\n')
        lines.append(f'path = "{repo_path}"\n')
        lines.append('base_branch = "main"\n\n')

    toml_path = os.path.join(base_dir, "workspace.toml")
    with open(toml_path, "w") as f:
        f.writelines(lines)
    return toml_path


def setup_forge_config(repo_path: str) -> None:
    """Create .forge/forge.toml with test/lint checks disabled."""
    forge_dir = os.path.join(repo_path, ".forge")
    os.makedirs(forge_dir, exist_ok=True)

    _write(
        os.path.join(forge_dir, "forge.toml"),
        """\
        [forge]
        test_cmd = ""
        lint_cmd = ""

        [forge.preflight]
        enabled = false

        [forge.review]
        auto_approve = true
        """,
    )
