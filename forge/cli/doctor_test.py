"""Tests for forge doctor CLI command."""

import os
import subprocess
from collections import namedtuple
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from forge.cli.doctor import doctor


DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])

GB = 1024**3

# Default good results for subprocess.run dispatch
_GIT_OK = subprocess.CompletedProcess(
    args=["git", "--version"], returncode=0,
    stdout="git version 2.39.3\n", stderr="",
)
_NODE_OK = subprocess.CompletedProcess(
    args=["node", "--version"], returncode=0,
    stdout="v20.0.0\n", stderr="",
)


def _make_subprocess_run(*, git=None, node=None):
    """Build a subprocess.run side_effect dispatching on command name.

    *git* / *node* can be a CompletedProcess (returned) or an Exception
    class/instance (raised).  Defaults to _GIT_OK / _NODE_OK.
    """
    git = git if git is not None else _GIT_OK
    node = node if node is not None else _NODE_OK

    def _run(cmd, **kwargs):
        target = git if cmd[0] == "git" else node if cmd[0] == "node" else None
        if target is None:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            )
        if isinstance(target, type) and issubclass(target, BaseException):
            raise target()
        if isinstance(target, BaseException):
            raise target
        return target

    return _run


@pytest.fixture()
def runner():
    return CliRunner()


# ── Python check ──────────────────────────────────────────────────────


def test_python_version_shown(runner):
    """Doctor reports the Python version."""
    result = runner.invoke(doctor)
    assert "Python" in result.output


# ── Git checks ────────────────────────────────────────────────────────


def test_git_ok(runner):
    """Git >= 2.20 shows success."""
    git_ok = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=0,
        stdout="git version 2.39.3 (Apple Git-146)\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(git=git_ok)):
        result = runner.invoke(doctor)
    assert "2.39.3" in result.output
    assert "Git" in result.output


def test_git_old_version(runner):
    """Git < 2.20 shows failure."""
    git_old = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=0,
        stdout="git version 2.17.1\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(git=git_old)):
        result = runner.invoke(doctor)
    assert "2.17.1" in result.output
    assert "requires" in result.output
    assert result.exit_code != 0


def test_git_not_installed(runner):
    """Git missing shows failure."""
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(git=FileNotFoundError())):
        result = runner.invoke(doctor)
    assert "not installed" in result.output
    assert result.exit_code != 0


def test_git_command_error(runner):
    """Git returning non-zero exit code shows failure."""
    git_err = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=1,
        stdout="",
        stderr="error",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(git=git_err)):
        result = runner.invoke(doctor)
    assert "not found" in result.output or "error" in result.output


# ── Claude CLI checks ────────────────────────────────────────────────


def test_claude_cli_ok(runner):
    """Claude CLI present and ~/.claude exists."""
    with (
        patch("forge.cli.doctor.shutil.which", side_effect=lambda c: "/usr/bin/claude" if c == "claude" else None),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Claude CLI" in result.output
    assert "authenticated" in result.output


def test_claude_cli_missing(runner):
    """Claude CLI not on PATH."""
    with patch("forge.cli.doctor.shutil.which", return_value=None):
        result = runner.invoke(doctor)
    assert "not found" in result.output


def test_claude_cli_no_auth(runner):
    """Claude CLI found but ~/.claude missing."""
    def _which(cmd):
        if cmd == "claude":
            return "/usr/bin/claude"
        return None

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=False),
    ):
        result = runner.invoke(doctor)
    assert "claude login" in result.output


# ── gh CLI check ──────────────────────────────────────────────────────


def test_gh_present(runner):
    """gh CLI found shows success."""
    def _which(cmd):
        return f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "GitHub CLI" in result.output


def test_gh_missing(runner):
    """gh CLI missing shows warning about PR creation."""
    def _which(cmd):
        if cmd == "gh":
            return None
        return f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "PR creation won't work" in result.output


# ── Node version checks ─────────────────────────────────────────────


def test_node_version_18_ok(runner):
    """Node 18.x passes the version check."""
    node_18 = subprocess.CompletedProcess(
        args=["node", "--version"],
        returncode=0,
        stdout="v18.17.0\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(node=node_18)):
        result = runner.invoke(doctor)
    assert "18.17.0" in result.output
    assert "Node.js" in result.output


def test_node_version_22_ok(runner):
    """Node 22.x passes the version check."""
    node_22 = subprocess.CompletedProcess(
        args=["node", "--version"],
        returncode=0,
        stdout="v22.1.0\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(node=node_22)):
        result = runner.invoke(doctor)
    assert "22.1.0" in result.output


def test_node_version_too_old(runner):
    """Node < 18 fails the version check."""
    node_16 = subprocess.CompletedProcess(
        args=["node", "--version"],
        returncode=0,
        stdout="v16.20.0\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(node=node_16)):
        result = runner.invoke(doctor)
    assert "16.20.0" in result.output
    assert "requires" in result.output
    assert result.exit_code != 0


def test_node_not_installed(runner):
    """Node not installed shows warning."""
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(node=FileNotFoundError())):
        result = runner.invoke(doctor)
    assert "not installed" in result.output


def test_node_version_timeout(runner):
    """Node --version timeout shows failure."""
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(
                   node=subprocess.TimeoutExpired(cmd="node", timeout=10),
               )):
        result = runner.invoke(doctor)
    assert "timed out" in result.output
    assert result.exit_code != 0


# ── npm check (presence only) ────────────────────────────────────────


def test_npm_present(runner):
    """npm found shows success."""
    def _which(cmd):
        return f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "npm" in result.output


def test_npm_missing(runner):
    """npm missing shows warning about Web UI."""
    def _which(cmd):
        if cmd == "npm":
            return None
        if cmd == "claude":
            return "/usr/bin/claude"
        return f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Web UI won't work" in result.output


# ── Database connectivity check ──────────────────────────────────────


def test_db_connectivity_ok(runner):
    """Successful DB connection shows success."""
    with patch("forge.cli.doctor.asyncio.run",
               return_value=("ok", "Database", "aiosqlite + sqlalchemy OK")):
        result = runner.invoke(doctor)
    assert "Database" in result.output
    assert "aiosqlite" in result.output or "OK" in result.output


def test_db_connectivity_import_error(runner):
    """Missing aiosqlite dependency shows failure."""
    with patch("forge.cli.doctor.asyncio.run",
               side_effect=ImportError("No module named 'aiosqlite'")):
        result = runner.invoke(doctor)
    assert "Database" in result.output
    assert "missing dependency" in result.output or "aiosqlite" in result.output
    assert result.exit_code != 0


def test_db_connectivity_connection_failure(runner):
    """Database connection failure shows failure."""
    with patch("forge.cli.doctor.asyncio.run",
               side_effect=RuntimeError("unable to open database")):
        result = runner.invoke(doctor)
    assert "Database" in result.output
    assert "connection failed" in result.output or "unable" in result.output
    assert result.exit_code != 0


# ── FORGE_JWT_SECRET check ────────────────────────────────────────────


def test_jwt_secret_set(runner):
    """FORGE_JWT_SECRET set shows success."""
    with patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}):
        result = runner.invoke(doctor)
    assert "FORGE_JWT_SECRET" in result.output


def test_jwt_secret_unset(runner):
    """FORGE_JWT_SECRET missing shows warning."""
    env = os.environ.copy()
    env.pop("FORGE_JWT_SECRET", None)
    with patch.dict(os.environ, env, clear=True):
        result = runner.invoke(doctor)
    assert "FORGE_JWT_SECRET" in result.output
    assert "not set" in result.output


# ── Disk space check ─────────────────────────────────────────────────


def test_disk_space_ok(runner):
    """Sufficient disk space shows success."""
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)
    with patch("forge.cli.doctor.shutil.disk_usage", return_value=usage):
        result = runner.invoke(doctor)
    assert "Disk space" in result.output
    assert "100.0 GB" in result.output


def test_disk_space_low(runner):
    """Low disk space shows failure."""
    usage = DiskUsage(total=500 * GB, used=497 * GB, free=3 * GB)
    with patch("forge.cli.doctor.shutil.disk_usage", return_value=usage):
        result = runner.invoke(doctor)
    assert "3.0 GB" in result.output
    assert "requires" in result.output
    assert result.exit_code != 0


# ── Overall exit code ────────────────────────────────────────────────


def test_all_pass_exit_zero(runner):
    """Exit code 0 when all checks pass."""
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor.subprocess.run",
              side_effect=_make_subprocess_run()),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch("forge.cli.doctor.asyncio.run",
              return_value=("ok", "Database", "aiosqlite + sqlalchemy OK")),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_failure_exit_nonzero(runner):
    """Exit code != 0 when a critical check fails."""
    git_fail = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=0,
        stdout="git version 1.9.0\n",
        stderr="",
    )
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor.subprocess.run",
              side_effect=_make_subprocess_run(git=git_fail)),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch("forge.cli.doctor.asyncio.run",
              return_value=("ok", "Database", "aiosqlite + sqlalchemy OK")),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code != 0
    assert "failed" in result.output.lower()


def test_node_version_fail_causes_nonzero_exit(runner):
    """Node < 18 causes non-zero exit code."""
    node_old = subprocess.CompletedProcess(
        args=["node", "--version"],
        returncode=0,
        stdout="v16.20.0\n",
        stderr="",
    )
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor.subprocess.run",
              side_effect=_make_subprocess_run(node=node_old)),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch("forge.cli.doctor.asyncio.run",
              return_value=("ok", "Database", "aiosqlite + sqlalchemy OK")),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code != 0


def test_db_fail_causes_nonzero_exit(runner):
    """Database connection failure causes non-zero exit code."""
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor.subprocess.run",
              side_effect=_make_subprocess_run()),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch("forge.cli.doctor.asyncio.run",
              side_effect=ImportError("No module named 'aiosqlite'")),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code != 0
