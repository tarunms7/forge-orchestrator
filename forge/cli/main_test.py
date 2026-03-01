from click.testing import CliRunner
from forge.cli.main import cli


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Forge" in result.output


def test_cli_init_creates_forge_dir(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".forge").is_dir()
    assert (tmp_path / ".forge" / "build-log.md").exists()


def test_status_subcommand_registered():
    """status command must be registered in the CLI group."""
    assert "status" in cli.commands


def test_logs_subcommand_registered():
    """logs command must be registered in the CLI group."""
    assert "logs" in cli.commands


def test_help_lists_status_subcommand():
    """status appears in the top-level --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "status" in result.output


def test_help_lists_logs_subcommand():
    """logs appears in the top-level --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "logs" in result.output


def test_clean_subcommand_registered():
    """clean command must be registered in the CLI group."""
    assert "clean" in cli.commands


def test_help_lists_clean_subcommand():
    """clean appears in the top-level --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "clean" in result.output
