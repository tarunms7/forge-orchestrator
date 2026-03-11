"""Tests for forge ping CLI command."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from forge.cli.ping import ping


@pytest.fixture()
def runner():
    return CliRunner()


# ── Success case ──────────────────────────────────────────────────────


def test_ping_claude_version_ok(runner):
    """ping succeeds when claude --version returns 0 and prints version."""
    completed = subprocess.CompletedProcess(
        args=["claude", "--version"],
        returncode=0,
        stdout="claude 1.2.3\n",
        stderr="",
    )
    with patch("forge.cli.ping.subprocess.run", return_value=completed):
        result = runner.invoke(ping)
    assert result.exit_code == 0
    assert "claude" in result.output.lower() or "1.2.3" in result.output


# ── claude not found ──────────────────────────────────────────────────


def test_ping_claude_not_found(runner):
    """ping prints error and exits non-zero when claude is not on PATH."""
    with patch(
        "forge.cli.ping.subprocess.run",
        side_effect=FileNotFoundError("No such file: 'claude'"),
    ):
        result = runner.invoke(ping)
    assert result.exit_code != 0
    assert result.output  # some error message printed


# ── claude returns non-zero exit code ────────────────────────────────


def test_ping_claude_nonzero_exit(runner):
    """ping prints error and exits non-zero when claude returns non-zero."""
    completed = subprocess.CompletedProcess(
        args=["claude", "--version"],
        returncode=1,
        stdout="",
        stderr="error: something went wrong",
    )
    with patch("forge.cli.ping.subprocess.run", return_value=completed):
        result = runner.invoke(ping)
    assert result.exit_code != 0
    assert result.output  # some error message printed


# ── subprocess.TimeoutExpired ─────────────────────────────────────────


def test_ping_timeout(runner):
    """ping prints error and exits non-zero when claude --version times out."""
    with patch(
        "forge.cli.ping.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["claude", "--version"], timeout=10),
    ):
        result = runner.invoke(ping)
    assert result.exit_code != 0
    assert result.output  # some error message printed
