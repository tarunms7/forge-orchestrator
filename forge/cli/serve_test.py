# forge/cli/serve_test.py
from click.testing import CliRunner

from forge.cli.main import cli


def test_serve_command_exists():
    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "Start the Forge web server" in result.output


def test_serve_shows_port_option():
    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--help"])
    assert "--port" in result.output
    assert "--host" in result.output
