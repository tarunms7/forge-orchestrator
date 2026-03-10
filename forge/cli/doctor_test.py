"""Tests for forge doctor CLI command."""

import builtins
import os
import sqlite3
import subprocess
import sys
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from forge.cli.doctor import (
    _check_db_connectivity,
    _check_node_version,
    _parse_node_version,
    doctor,
)

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
    """Build a subprocess.run side_effect dispatching on command name."""
    git = git if git is not None else _GIT_OK
    node = node if node is not None else _NODE_OK

    def _run(cmd, **kwargs):
        name = cmd[0] if cmd else ""
        if name == "git":
            target = git
        elif name == "node":
            target = node
        else:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if isinstance(target, BaseException):
            raise target
        if isinstance(target, type) and issubclass(target, BaseException):
            raise target()
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
        args=["git", "--version"], returncode=0,
        stdout="git version 2.39.3 (Apple Git-146)\n", stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(git=git_ok)):
        result = runner.invoke(doctor)
    assert "2.39.3" in result.output
    assert "Git" in result.output


def test_git_old_version(runner):
    """Git < 2.20 shows failure."""
    git_old = subprocess.CompletedProcess(
        args=["git", "--version"], returncode=0,
        stdout="git version 2.17.1\n", stderr="",
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
        args=["git", "--version"], returncode=1, stdout="", stderr="error",
    )
    with patch("forge.cli.doctor.subprocess.run",
               side_effect=_make_subprocess_run(git=git_err)):
        result = runner.invoke(doctor)
    assert "not found" in result.output or "error" in result.output


# ── Claude CLI checks ────────────────────────────────────────────────


def test_claude_cli_ok(runner):
    """Claude CLI present and ~/.claude exists."""
    with (
        patch("forge.cli.doctor.shutil.which",
              side_effect=lambda c: "/usr/bin/claude" if c == "claude" else None),
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
        return "/usr/bin/claude" if cmd == "claude" else None

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=False),
    ):
        result = runner.invoke(doctor)
    assert "claude login" in result.output


# ── gh CLI check ──────────────────────────────────────────────────────


def test_gh_present(runner):
    """gh CLI found shows success."""
    with (
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "GitHub CLI" in result.output


def test_gh_missing(runner):
    """gh CLI missing shows warning about PR creation."""
    def _which(cmd):
        return None if cmd == "gh" else f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "PR creation won't work" in result.output


# ── Node/npm presence checks ─────────────────────────────────────────


def test_node_npm_present(runner):
    """Both node and npm found."""
    with (
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Node/npm" in result.output


def test_node_npm_missing(runner):
    """Both node and npm missing shows warning."""
    def _which(cmd):
        if cmd in ("node", "npm"):
            return None
        return f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Web UI won't work" in result.output


def test_node_missing_npm_present(runner):
    """Only node missing shows warning."""
    def _which(cmd):
        return None if cmd == "node" else f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Web UI won't work" in result.output


def test_npm_missing_node_present(runner):
    """Only npm missing shows warning."""
    def _which(cmd):
        return None if cmd == "npm" else f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Web UI won't work" in result.output


# ── Node version checks ──────────────────────────────────────────────


def test_parse_node_version_standard():
    assert _parse_node_version("v20.0.0") == (20, 0, 0)


def test_parse_node_version_major_only():
    assert _parse_node_version("v18") == (18,)


def test_check_node_version_18_passes():
    """Node v18.17.0 satisfies >= 18 requirement."""
    node_v18 = subprocess.CompletedProcess(
        args=["node", "--version"], returncode=0,
        stdout="v18.17.0\n", stderr="",
    )
    with (
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/node"),
        patch("forge.cli.doctor.subprocess.run", return_value=node_v18),
    ):
        status, label, detail = _check_node_version()
    assert status == "ok"
    assert "18.17.0" in detail


def test_check_node_version_22_passes():
    """Node v22.1.0 satisfies >= 18 requirement."""
    node_v22 = subprocess.CompletedProcess(
        args=["node", "--version"], returncode=0,
        stdout="v22.1.0\n", stderr="",
    )
    with (
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/node"),
        patch("forge.cli.doctor.subprocess.run", return_value=node_v22),
    ):
        status, label, detail = _check_node_version()
    assert status == "ok"
    assert "22.1.0" in detail


def test_check_node_version_16_fails():
    """Node v16.20.0 fails >= 18 requirement."""
    node_v16 = subprocess.CompletedProcess(
        args=["node", "--version"], returncode=0,
        stdout="v16.20.0\n", stderr="",
    )
    with (
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/node"),
        patch("forge.cli.doctor.subprocess.run", return_value=node_v16),
    ):
        status, label, detail = _check_node_version()
    assert status == "fail"
    assert "16.20.0" in detail
    assert "requires" in detail


def test_check_node_version_not_installed():
    """Node not on PATH returns warn."""
    with patch("forge.cli.doctor.shutil.which", return_value=None):
        status, label, detail = _check_node_version()
    assert status == "warn"
    assert "not installed" in detail


def test_check_node_version_timeout():
    """Node --version timing out returns fail."""
    with (
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/node"),
        patch("forge.cli.doctor.subprocess.run",
              side_effect=subprocess.TimeoutExpired(cmd="node", timeout=10)),
    ):
        status, label, detail = _check_node_version()
    assert status == "fail"
    assert "timed out" in detail


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


# ── Database connectivity check ──────────────────────────────────────


def test_db_connectivity_ok():
    """Successful DB connectivity check returns ok."""
    status, label, detail = _check_db_connectivity()
    # Should pass in test env since aiosqlite and sqlalchemy are installed
    assert status == "ok"
    assert "OK" in detail


def test_db_connectivity_import_error():
    """Missing aiosqlite dependency fails DB check."""
    real_import = builtins.__import__

    def _fail_aiosqlite(name, *args, **kwargs):
        if name == "aiosqlite":
            raise ImportError("No module named 'aiosqlite'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fail_aiosqlite):
        status, label, detail = _check_db_connectivity()
    assert status == "fail"
    assert "missing dependency" in detail


def test_db_connectivity_connection_failure():
    """DB connection failure returns fail."""
    with patch("forge.cli.doctor.sqlite3.connect", side_effect=RuntimeError("connection refused")):
        status, label, detail = _check_db_connectivity()
    assert status == "fail"
    assert "connection failed" in detail


# ── Overall exit code ────────────────────────────────────────────────


def test_all_pass_exit_zero(runner):
    """Exit code 0 when all checks pass."""
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor._check_python", return_value=("ok", "Python", "3.12.0")),
        patch("forge.cli.doctor.subprocess.run",
              side_effect=_make_subprocess_run()),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_failure_exit_nonzero(runner):
    """Exit code != 0 when a critical check fails."""
    git_fail = subprocess.CompletedProcess(
        args=["git", "--version"], returncode=0,
        stdout="git version 1.9.0\n", stderr="",
    )
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor._check_python", return_value=("ok", "Python", "3.12.0")),
        patch("forge.cli.doctor.subprocess.run",
              side_effect=_make_subprocess_run(git=git_fail)),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code != 0
    assert "failed" in result.output.lower()
