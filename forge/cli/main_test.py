import importlib
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from click.testing import CliRunner

from forge.cli.main import cli
import forge.cli.main as _main_module


def test_cli_version():
    runner = CliRunner()
    with patch("importlib.metadata.version", return_value="1.2.3"):
        importlib.reload(_main_module)
        result = runner.invoke(_main_module.cli, ["--version"])
    importlib.reload(_main_module)  # restore original version
    assert result.exit_code == 0
    assert "1.2.3" in result.output


def test_cli_version_not_installed():
    runner = CliRunner()
    with patch("importlib.metadata.version", side_effect=PackageNotFoundError("forge")):
        importlib.reload(_main_module)
        result = runner.invoke(_main_module.cli, ["--version"])
    importlib.reload(_main_module)  # restore original version
    assert result.exit_code == 0
    assert "dev" in result.output


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
