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
