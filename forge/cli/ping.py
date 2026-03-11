"""Forge CLI ping command. Verifies claude CLI is reachable by running claude --version."""

from __future__ import annotations

import subprocess

import click


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
