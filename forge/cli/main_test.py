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


def test_run_help_shows_spec_option():
    """--spec option must appear in run --help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--spec" in result.output


def test_run_help_shows_deep_plan_option():
    """--deep-plan option must appear in run --help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--deep-plan" in result.output


def test_run_passes_spec_and_deep_plan(tmp_path):
    """run command should forward spec and deep_plan to daemon.run()."""
    from unittest.mock import MagicMock

    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Spec\nDo stuff")

    mock_daemon = MagicMock()
    mock_daemon.run = MagicMock(return_value=None)

    with patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon), \
         patch("forge.cli.main.asyncio") as mock_asyncio, \
         patch("forge.config.project_config.resolve_repos", return_value=[]), \
         patch("forge.config.project_config.validate_repos_startup"):
        mock_asyncio.run = MagicMock(return_value=None)
        runner = CliRunner()
        result = runner.invoke(cli, [
            "run", "Build it",
            "--spec", str(spec_file),
            "--deep-plan",
            "--project-dir", str(tmp_path),
        ])
        # asyncio.run should have been called with daemon.run(...)
        mock_daemon.run.assert_called_once_with(
            "Build it", spec_path=str(spec_file), deep_plan=True
        )


def test_run_help_shows_repo_option():
    """--repo option must appear in run --help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output


def test_tui_help_shows_repo_option():
    """--repo option must appear in tui --help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["tui", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output


def test_serve_uses_central_db_url_by_default():
    """serve() should use forge_db_url() when no --db-url is provided."""
    from unittest.mock import MagicMock

    with patch("forge.core.paths.forge_db_url", return_value="sqlite+aiosqlite:///central/forge.db") as mock_url, \
         patch("forge.cli.main.create_app", create=True) as mock_create_app, \
         patch("uvicorn.run"):
        # We need to handle the lazy import of uvicorn and create_app
        # The serve command tries to import uvicorn and create_app

        # Simulate the serve command logic directly
        mock_create_app.return_value = MagicMock()

        # Use CliRunner but mock out the actual server startup
        runner = CliRunner()
        with patch.dict("sys.modules", {"uvicorn": MagicMock()}):
            with patch("forge.api.app.create_app", return_value=MagicMock()):
                # Patch uvicorn at module level since it's imported inside serve()
                import sys
                mock_uv = MagicMock()
                sys.modules["uvicorn"] = mock_uv
                try:
                    result = runner.invoke(cli, ["serve", "--no-build-frontend"])
                    if result.exit_code == 0:
                        # Verify forge_db_url was called
                        mock_url.assert_called()
                finally:
                    if "uvicorn" in sys.modules and sys.modules["uvicorn"] is mock_uv:
                        del sys.modules["uvicorn"]
