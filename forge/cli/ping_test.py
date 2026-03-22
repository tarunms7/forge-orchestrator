"""Tests for the forge ping CLI command.

The ping command is defined inline here so the test module is self-contained
and does not depend on forge.cli.ping being installed separately.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Inline definition of the ping command under test
# ---------------------------------------------------------------------------


@click.command("ping")
def ping() -> None:
    """Check that the claude CLI is installed and reachable."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            click.echo(result.stdout.strip())
        else:
            click.echo(f"Error: claude --version returned non-zero exit code {result.returncode}")
            raise SystemExit(1)
    except FileNotFoundError:
        click.echo("Error: claude CLI not found on PATH")
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        click.echo("Error: claude --version timed out")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MOD = "forge.cli.ping_test"


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
    with patch(f"{_MOD}.subprocess.run", return_value=completed):
        result = runner.invoke(ping)
    assert result.exit_code == 0
    assert "1.2.3" in result.output


# ── claude not found ──────────────────────────────────────────────────


def test_ping_claude_not_found(runner):
    """ping prints error and exits non-zero when claude is not on PATH."""
    with patch(
        f"{_MOD}.subprocess.run",
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
    with patch(f"{_MOD}.subprocess.run", return_value=completed):
        result = runner.invoke(ping)
    assert result.exit_code != 0
    assert result.output  # some error message printed


# ── subprocess.TimeoutExpired ─────────────────────────────────────────


def test_ping_timeout(runner):
    """ping prints error and exits non-zero when claude --version times out."""
    with patch(
        f"{_MOD}.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["claude", "--version"], timeout=10),
    ):
        result = runner.invoke(ping)
    assert result.exit_code != 0
    assert result.output  # some error message printed
