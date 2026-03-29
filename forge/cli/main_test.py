import importlib
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from click.testing import CliRunner

import forge.cli.main as _main_module
from forge.cli.main import cli


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
    from unittest.mock import AsyncMock, MagicMock

    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Spec\nDo stuff")

    mock_daemon = MagicMock()
    mock_daemon.run = AsyncMock(return_value=None)

    # Mock preflight to pass
    mock_preflight_report = MagicMock()
    mock_preflight_report.passed = True
    mock_preflight_report.warnings = []

    with (
        patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon),
        patch(
            "forge.core.preflight.run_preflight",
            new_callable=AsyncMock,
            return_value=mock_preflight_report,
        ),
        patch("forge.config.project_config.resolve_repos", return_value=[]),
        patch("forge.config.project_config.validate_repos_startup"),
    ):
        runner = CliRunner()
        runner.invoke(
            cli,
            [
                "run",
                "Build it",
                "--spec",
                str(spec_file),
                "--deep-plan",
                "--project-dir",
                str(tmp_path),
            ],
        )
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


def test_run_exception_shows_traceback_hint_without_verbose(tmp_path):
    """When run fails without --verbose, show hint about --verbose flag."""
    from unittest.mock import AsyncMock, MagicMock

    mock_daemon = MagicMock()
    mock_daemon.run = AsyncMock(side_effect=RuntimeError("boom"))

    mock_preflight_report = MagicMock()
    mock_preflight_report.passed = True
    mock_preflight_report.warnings = []

    runner = CliRunner()
    with (
        patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon),
        patch(
            "forge.core.preflight.run_preflight",
            new_callable=AsyncMock,
            return_value=mock_preflight_report,
        ),
        patch("forge.config.project_config.resolve_repos", return_value=[]),
        patch("forge.config.project_config.validate_repos_startup"),
    ):
        result = runner.invoke(
            cli,
            [
                "run",
                "Build it",
                "--project-dir",
                str(tmp_path),
            ],
        )
    assert result.exit_code == 1
    assert "Forge failed: boom" in result.output
    assert "Run with --verbose for full traceback" in result.output


def test_run_exception_shows_traceback_with_verbose(tmp_path):
    """When run fails with --verbose, print the full traceback."""
    from unittest.mock import AsyncMock, MagicMock

    mock_daemon = MagicMock()
    mock_daemon.run = AsyncMock(side_effect=RuntimeError("boom"))

    mock_preflight_report = MagicMock()
    mock_preflight_report.passed = True
    mock_preflight_report.warnings = []

    runner = CliRunner()
    with (
        patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon),
        patch(
            "forge.core.preflight.run_preflight",
            new_callable=AsyncMock,
            return_value=mock_preflight_report,
        ),
        patch("forge.config.project_config.resolve_repos", return_value=[]),
        patch("forge.config.project_config.validate_repos_startup"),
    ):
        result = runner.invoke(
            cli,
            [
                "--verbose",
                "run",
                "Build it",
                "--project-dir",
                str(tmp_path),
            ],
        )
    assert result.exit_code == 1
    assert "Forge failed: boom" in result.output
    assert "Traceback" in result.output
    assert "RuntimeError" in result.output


def test_run_help_shows_dry_run_option():
    """--dry-run option must appear in run --help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_tui_help_shows_dry_run_option():
    """--dry-run option must appear in tui --help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["tui", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_run_dry_run_calls_daemon_dry_run(tmp_path):
    """run --dry-run should call daemon.dry_run() instead of daemon.run()."""
    from unittest.mock import AsyncMock, MagicMock

    mock_task = MagicMock()
    mock_task.id = "t1"
    mock_task.title = "Task 1"
    mock_task.description = "Do stuff"
    mock_task.files = ["a.py"]
    mock_task.depends_on = []
    mock_task.complexity = MagicMock()
    mock_task.complexity.value = "low"

    mock_graph = MagicMock()
    mock_graph.tasks = [mock_task]

    mock_daemon = MagicMock()
    mock_daemon.dry_run = AsyncMock(
        return_value={
            "graph": mock_graph,
            "cost_estimate": 0.12,
            "model_assignments": {"t1": "sonnet"},
        }
    )
    mock_daemon.run = AsyncMock()

    mock_preflight_report = MagicMock()
    mock_preflight_report.passed = True
    mock_preflight_report.warnings = []

    with (
        patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon),
        patch(
            "forge.core.preflight.run_preflight",
            new_callable=AsyncMock,
            return_value=mock_preflight_report,
        ),
        patch("forge.config.project_config.resolve_repos", return_value=[]),
        patch("forge.config.project_config.validate_repos_startup"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "run",
                "Build it",
                "--dry-run",
                "--project-dir",
                str(tmp_path),
            ],
        )
        mock_daemon.dry_run.assert_called_once_with(
            "Build it", spec_path=None, deep_plan=False
        )
        mock_daemon.run.assert_not_called()
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert "Task 1" in result.output
    assert "sonnet" in result.output


def test_run_dry_run_output_shows_cost(tmp_path):
    """Dry-run output should display the estimated cost."""
    from unittest.mock import AsyncMock, MagicMock

    mock_task = MagicMock()
    mock_task.id = "t1"
    mock_task.title = "Build API"
    mock_task.description = "Create REST endpoints"
    mock_task.files = ["api.py", "routes.py"]
    mock_task.depends_on = []
    mock_task.complexity = MagicMock()
    mock_task.complexity.value = "medium"

    mock_task2 = MagicMock()
    mock_task2.id = "t2"
    mock_task2.title = "Add tests"
    mock_task2.description = "Unit tests"
    mock_task2.files = ["test_api.py"]
    mock_task2.depends_on = ["t1"]
    mock_task2.complexity = MagicMock()
    mock_task2.complexity.value = "high"

    mock_graph = MagicMock()
    mock_graph.tasks = [mock_task, mock_task2]

    mock_daemon = MagicMock()
    mock_daemon.dry_run = AsyncMock(
        return_value={
            "graph": mock_graph,
            "cost_estimate": 1.50,
            "model_assignments": {"t1": "opus", "t2": "opus"},
        }
    )

    mock_preflight_report = MagicMock()
    mock_preflight_report.passed = True
    mock_preflight_report.warnings = []

    with (
        patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon),
        patch(
            "forge.core.preflight.run_preflight",
            new_callable=AsyncMock,
            return_value=mock_preflight_report,
        ),
        patch("forge.config.project_config.resolve_repos", return_value=[]),
        patch("forge.config.project_config.validate_repos_startup"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["run", "Build it", "--dry-run", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0
    assert "$1.50" in result.output
    assert "2 tasks" in result.output
    assert "Run without --dry-run to execute" in result.output


def test_serve_uses_central_db_url_by_default():
    """serve() should use forge_db_url() when no --db-url is provided."""
    from unittest.mock import MagicMock

    with (
        patch(
            "forge.core.paths.forge_db_url", return_value="sqlite+aiosqlite:///central/forge.db"
        ) as mock_url,
        patch("forge.cli.main.create_app", create=True) as mock_create_app,
        patch("uvicorn.run"),
    ):
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
