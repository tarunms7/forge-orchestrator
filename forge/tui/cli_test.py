"""Tests for TUI CLI integration."""

from click.testing import CliRunner

from forge.cli.main import cli


def test_tui_command_exists():
    runner = CliRunner()
    result = runner.invoke(cli, ["tui", "--help"])
    assert result.exit_code == 0
    assert "terminal" in result.output.lower() or "tui" in result.output.lower()
