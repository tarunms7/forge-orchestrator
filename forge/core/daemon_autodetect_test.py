"""Tests for ForgeDaemon._auto_detect_commands().

Note: _auto_detect_commands is synchronous (filesystem reads only) but is called
from the async _preflight_checks method.  These tests verify it works correctly
in both sync and async contexts after the subprocess→async_subprocess migration.
"""

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon


@pytest.fixture
def daemon(tmp_path):
    """Create a ForgeDaemon with a temporary project dir."""
    settings = ForgeSettings()
    return ForgeDaemon(project_dir=str(tmp_path), settings=settings)


class TestAutoDetectBuildCmd:
    def test_package_json_with_build_script(self, daemon, tmp_path):
        """package.json with a 'build' script → build_cmd detected."""
        pkg = {"scripts": {"build": "next build"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd == "npm run build"

    def test_package_json_without_build_script(self, daemon, tmp_path):
        """package.json without 'build' script → build_cmd stays None."""
        pkg = {"scripts": {"start": "node index.js"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd is None

    def test_no_package_json(self, daemon, tmp_path):
        """No package.json → build_cmd stays None."""
        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd is None

    def test_malformed_package_json(self, daemon, tmp_path):
        """Malformed package.json → build_cmd stays None (no crash)."""
        (tmp_path / "package.json").write_text("{invalid json")

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd is None

    def test_build_cmd_empty_string_not_overridden(self, tmp_path):
        """build_cmd='' (user wants to skip) must NOT be overridden."""
        settings = ForgeSettings(build_cmd="")
        daemon = ForgeDaemon(project_dir=str(tmp_path), settings=settings)
        pkg = {"scripts": {"build": "webpack"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd == ""


class TestAutoDetectTestCmd:
    def test_pyproject_with_tool_pytest_ini_options(self, daemon, tmp_path):
        """pyproject.toml with [tool.pytest.ini_options] → test_cmd detected."""
        content = "[project]\nname = 'foo'\n\n[tool.pytest.ini_options]\naddopts = '-v'\n"
        (tmp_path / "pyproject.toml").write_text(content)

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "python -m pytest"

    def test_pyproject_with_tool_pytest(self, daemon, tmp_path):
        """pyproject.toml with [tool.pytest] → test_cmd detected."""
        content = "[tool.pytest]\nminversion = '6.0'\n"
        (tmp_path / "pyproject.toml").write_text(content)

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "python -m pytest"

    def test_makefile_with_test_target(self, daemon, tmp_path):
        """Makefile with 'test:' target → test_cmd detected."""
        content = "build:\n\tgo build ./...\n\ntest:\n\tgo test ./...\n"
        (tmp_path / "Makefile").write_text(content)

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "make test"

    def test_makefile_with_test_space_target(self, daemon, tmp_path):
        """Makefile with 'test ' (space after) → test_cmd detected."""
        content = "test all:\n\tpytest\n"
        (tmp_path / "Makefile").write_text(content)

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "make test"

    def test_pyproject_takes_priority_over_makefile(self, daemon, tmp_path):
        """pyproject.toml with pytest is checked before Makefile."""
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        (tmp_path / "Makefile").write_text("test:\n\tmake test\n")

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "python -m pytest"

    def test_makefile_fallback_when_pyproject_has_no_pytest(self, daemon, tmp_path):
        """pyproject.toml without pytest config falls through to Makefile."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n")

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "make test"

    def test_test_cmd_not_overridden_when_set(self, tmp_path):
        """test_cmd='custom' must NOT be overridden."""
        settings = ForgeSettings(test_cmd="custom")
        daemon = ForgeDaemon(project_dir=str(tmp_path), settings=settings)
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == "custom"

    def test_test_cmd_empty_string_not_overridden(self, tmp_path):
        """test_cmd='' (user wants to skip) must NOT be overridden."""
        settings = ForgeSettings(test_cmd="")
        daemon = ForgeDaemon(project_dir=str(tmp_path), settings=settings)
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.test_cmd == ""


class TestAutoDetectNoFiles:
    def test_no_config_files_commands_stay_none(self, daemon, tmp_path):
        """No config files present → both commands stay None."""
        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd is None
        assert daemon._settings.test_cmd is None


class TestAutoDetectMultipleFiles:
    def test_both_detected(self, daemon, tmp_path):
        """package.json + pyproject.toml → both commands detected."""
        pkg = {"scripts": {"build": "vite build"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        daemon._auto_detect_commands(str(tmp_path))

        assert daemon._settings.build_cmd == "npm run build"
        assert daemon._settings.test_cmd == "python -m pytest"


# ---------------------------------------------------------------------------
# Tests: _auto_detect_commands called from async _preflight_checks
# ---------------------------------------------------------------------------

def _mock_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
class TestAutoDetectViaPreflightAsync:
    """Verify _auto_detect_commands runs correctly when invoked from
    the async _preflight_checks (which uses async_subprocess)."""

    async def test_preflight_calls_auto_detect_before_git_checks(self, tmp_path):
        """_preflight_checks invokes _auto_detect_commands, detecting build/test cmds."""
        pkg = {"scripts": {"build": "vite build"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        settings = ForgeSettings()
        daemon = ForgeDaemon(project_dir=str(tmp_path), settings=settings)
        daemon._emit = AsyncMock()

        db = MagicMock()
        db.update_pipeline_status = AsyncMock()
        db.log_event = AsyncMock()

        async_sub = AsyncMock(side_effect=[
            _mock_completed(0, "true\n"),    # git rev-parse --is-inside-work-tree
            _mock_completed(0, "abc123\n"),  # git rev-parse HEAD
            _mock_completed(0, "origin\n"),  # git remote
        ])

        with patch("forge.core.daemon.async_subprocess", async_sub), \
             patch("forge.core.daemon.shutil.which", return_value=None):
            result = await daemon._preflight_checks(str(tmp_path), db, "pipe-1")

        assert result is True
        assert daemon._settings.build_cmd == "npm run build"
        assert daemon._settings.test_cmd == "python -m pytest"
