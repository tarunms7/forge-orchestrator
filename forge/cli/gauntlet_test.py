"""Tests for forge.cli.gauntlet CLI command."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from forge.cli.gauntlet import gauntlet
from forge.gauntlet.models import GauntletResult, ScenarioResult


def _make_result(passed: bool = True) -> GauntletResult:
    return GauntletResult(
        scenarios=[
            ScenarioResult(name="happy_path", passed=passed, duration_s=1.0, cost_usd=0.02),
        ],
        total_duration_s=1.0,
    )


def _mock_runner(result: GauntletResult) -> MagicMock:
    mock_cls = MagicMock()
    mock_instance = mock_cls.return_value
    mock_instance.run = AsyncMock(return_value=result)
    return mock_cls


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_default_rich(mock_runner_cls):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result())

    runner = CliRunner()
    result = runner.invoke(gauntlet, [], obj={"verbose": False})
    assert result.exit_code == 0
    assert "Gauntlet: 1/1 passed" in result.output


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_json_format(mock_runner_cls):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result())

    runner = CliRunner()
    result = runner.invoke(gauntlet, ["--format", "json"], obj={"verbose": False})
    assert result.exit_code == 0
    assert '"happy_path"' in result.output


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_exit_code_1_on_failure(mock_runner_cls):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result(passed=False))

    runner = CliRunner()
    result = runner.invoke(gauntlet, [], obj={"verbose": False})
    assert result.exit_code == 1


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_output_file(mock_runner_cls, tmp_path):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result())

    out_file = str(tmp_path / "report.json")
    runner = CliRunner()
    result = runner.invoke(gauntlet, ["--output", out_file], obj={"verbose": False})
    assert result.exit_code == 0
    assert "Report written to" in result.output

    with open(out_file) as f:
        content = f.read()
    assert '"happy_path"' in content


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_live_warning(mock_runner_cls):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result())

    runner = CliRunner()
    result = runner.invoke(gauntlet, ["--live"], obj={"verbose": False})
    assert "real money" in result.output


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_scenario_filter(mock_runner_cls):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result())

    runner = CliRunner()
    result = runner.invoke(gauntlet, ["-s", "happy_path"], obj={"verbose": False})
    assert result.exit_code == 0


@patch("forge.gauntlet.runner.GauntletRunner")
def test_gauntlet_verbose(mock_runner_cls):
    mock_runner_cls.return_value.run = AsyncMock(return_value=_make_result())

    runner = CliRunner()
    result = runner.invoke(gauntlet, ["--verbose"], obj={"verbose": False})
    assert result.exit_code == 0
