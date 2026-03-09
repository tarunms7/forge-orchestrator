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
    completed = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=0,
        stdout="git version 2.39.3 (Apple Git-146)\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run", return_value=completed):
        result = runner.invoke(doctor)
    assert "2.39.3" in result.output
    assert "Git" in result.output


def test_git_old_version(runner):
    """Git < 2.20 shows failure."""
    completed = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=0,
        stdout="git version 2.17.1\n",
        stderr="",
    )
    with patch("forge.cli.doctor.subprocess.run", return_value=completed):
        result = runner.invoke(doctor)
    assert "2.17.1" in result.output
    assert "requires" in result.output
    assert result.exit_code != 0


def test_git_not_installed(runner):
    """Git missing shows failure."""
    with patch("forge.cli.doctor.subprocess.run", side_effect=FileNotFoundError):
        result = runner.invoke(doctor)
    assert "not installed" in result.output
    assert result.exit_code != 0


def test_git_command_error(runner):
    """Git returning non-zero exit code shows failure."""
    completed = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=1,
        stdout="",
        stderr="error",
    )
    with patch("forge.cli.doctor.subprocess.run", return_value=completed):
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


# ── Node/npm checks ──────────────────────────────────────────────────


def test_node_npm_present(runner):
    """Both node and npm found."""
    def _which(cmd):
        return f"/usr/bin/{cmd}"

    with (
        patch("forge.cli.doctor.shutil.which", side_effect=_which),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
    ):
        result = runner.invoke(doctor)
    assert "Node/npm" in result.output


def test_node_npm_missing(runner):
    """Both node and npm missing shows warning."""
    def _which(cmd):
        if cmd in ("node", "npm"):
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


def test_node_missing_npm_present(runner):
    """Only node missing shows warning."""
    def _which(cmd):
        if cmd == "node":
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


def test_npm_missing_node_present(runner):
    """Only npm missing shows warning."""
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
    git_ok = subprocess.CompletedProcess(
        args=["git", "--version"],
        returncode=0,
        stdout="git version 2.39.3\n",
        stderr="",
    )
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor.subprocess.run", return_value=git_ok),
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
        args=["git", "--version"],
        returncode=0,
        stdout="git version 1.9.0\n",
        stderr="",
    )
    usage = DiskUsage(total=500 * GB, used=400 * GB, free=100 * GB)

    with (
        patch("forge.cli.doctor.subprocess.run", return_value=git_fail),
        patch("forge.cli.doctor.shutil.which", return_value="/usr/bin/thing"),
        patch("forge.cli.doctor.os.path.isdir", return_value=True),
        patch("forge.cli.doctor.shutil.disk_usage", return_value=usage),
        patch.dict(os.environ, {"FORGE_JWT_SECRET": "s3cret"}),
    ):
        result = runner.invoke(doctor)
    assert result.exit_code != 0
    assert "failed" in result.output.lower()
