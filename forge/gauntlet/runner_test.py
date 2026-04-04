"""Tests for forge.gauntlet.runner — GauntletRunner orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from forge.gauntlet.runner import GauntletRunner, UnknownScenarioError


class TestGauntletRunnerBasic:
    @pytest.mark.asyncio
    async def test_runs_happy_path_scenario(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path"],
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert result.scenarios[0].name == "happy_path"
        assert result.scenarios[0].passed is True
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_runs_all_scenarios(self, tmp_path):
        runner = GauntletRunner(workspace_dir=str(tmp_path))
        result = await runner.run()
        assert len(result.scenarios) == 5
        assert result.total_duration_s > 0

    @pytest.mark.asyncio
    async def test_total_duration_populated(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path"],
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert result.total_duration_s > 0


class TestGauntletRunnerFiltering:
    @pytest.mark.asyncio
    async def test_specific_scenario_filter(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["review_gate_failure"],
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert result.scenarios[0].name == "review_gate_failure"

    @pytest.mark.asyncio
    async def test_multiple_scenario_filter(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path", "integration_failure"],
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 2
        names = {s.name for s in result.scenarios}
        assert names == {"happy_path", "integration_failure"}

    @pytest.mark.asyncio
    async def test_invalid_scenario_name_raises(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["nonexistent_scenario"],
            workspace_dir=str(tmp_path),
        )
        with pytest.raises(UnknownScenarioError, match="Unknown gauntlet scenario"):
            await runner.run()

    @pytest.mark.asyncio
    async def test_mix_valid_and_invalid_raises(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path", "nonexistent"],
            workspace_dir=str(tmp_path),
        )
        with pytest.raises(UnknownScenarioError, match="Unknown gauntlet scenario"):
            await runner.run()


class TestGauntletRunnerChaos:
    @pytest.mark.asyncio
    async def test_chaos_flag_passed_through(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path"],
            chaos=True,
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        # happy_path is not chaos_compatible, so chaos shouldn't break it
        assert result.scenarios[0].passed is True

    @pytest.mark.asyncio
    async def test_chaos_with_compatible_scenario(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["resume_after_interrupt"],
            chaos=True,
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert result.scenarios[0].name == "resume_after_interrupt"


class TestGauntletRunnerLiveMode:
    @pytest.mark.asyncio
    async def test_live_mode_rejects_unsupported_scenario(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["resume_after_interrupt"],
            live=True,
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert result.scenarios[0].passed is False
        assert "currently supports only" in result.scenarios[0].error.lower()

    @pytest.mark.asyncio
    async def test_live_mode_sdk_unavailable(self, tmp_path):
        with patch("forge.gauntlet.runner._claude_cli_available", return_value=False):
            runner = GauntletRunner(
                scenarios=["happy_path"],
                live=True,
                workspace_dir=str(tmp_path),
            )
            result = await runner.run()
            assert len(result.scenarios) == 1
            assert result.scenarios[0].passed is False
            assert "claude cli not found" in result.scenarios[0].error.lower()

    @pytest.mark.asyncio
    async def test_live_mode_builds_validated_result_from_pipeline_db(self, tmp_path):
        mock_daemon_instance = AsyncMock()
        mock_daemon_instance.run = AsyncMock(return_value=None)
        mock_daemon_instance._pipeline_id = "pipe-1"

        mock_db = AsyncMock()
        mock_db.get_pipeline = AsyncMock(
            return_value=SimpleNamespace(
                status="complete",
                task_graph_json='{"tasks":[{"id":"t1"},{"id":"t2"}]}',
                contracts_json='{"api_contracts":[],"type_contracts":[]}',
                total_cost_usd=1.25,
            )
        )
        mock_db.list_tasks_by_pipeline = AsyncMock(
            return_value=[
                SimpleNamespace(id="t1", state="done"),
                SimpleNamespace(id="t2", state="done"),
            ]
        )

        with (
            patch("forge.gauntlet.runner._claude_cli_available", return_value=True),
            patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon_instance) as mock_cls,
            patch("forge.storage.db.Database", return_value=mock_db),
        ):
            runner = GauntletRunner(
                scenarios=["happy_path"],
                live=True,
                workspace_dir=str(tmp_path),
            )
            result = await runner.run()

        assert len(result.scenarios) == 1
        scenario = result.scenarios[0]
        assert scenario.passed is True
        assert scenario.cost_usd == 1.25
        assert scenario.artifacts["mode"] == "live"
        assert [stage.name for stage in scenario.stages] == ["planning", "contracts", "execution"]
        assert all(stage.passed for stage in scenario.stages)
        assert all(assertion.passed for assertion in scenario.assertions)
        mock_cls.assert_called_once()
        mock_daemon_instance.run.assert_awaited_once()
        mock_db.get_pipeline.assert_awaited_once_with("pipe-1")
        mock_db.list_tasks_by_pipeline.assert_awaited_once_with("pipe-1")

    @pytest.mark.asyncio
    async def test_live_mode_fails_when_pipeline_validation_fails(self, tmp_path):
        mock_daemon_instance = AsyncMock()
        mock_daemon_instance.run = AsyncMock(return_value=None)
        mock_daemon_instance._pipeline_id = "pipe-2"

        mock_db = AsyncMock()
        mock_db.get_pipeline = AsyncMock(
            return_value=SimpleNamespace(
                status="partial_success",
                task_graph_json='{"tasks":[{"id":"t1"}]}',
                contracts_json=None,
                total_cost_usd=0.5,
            )
        )
        mock_db.list_tasks_by_pipeline = AsyncMock(
            return_value=[
                SimpleNamespace(id="t1", state="done"),
                SimpleNamespace(id="t2", state="blocked"),
            ]
        )

        with (
            patch("forge.gauntlet.runner._claude_cli_available", return_value=True),
            patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon_instance),
            patch("forge.storage.db.Database", return_value=mock_db),
        ):
            runner = GauntletRunner(
                scenarios=["happy_path"],
                live=True,
                workspace_dir=str(tmp_path),
            )
            result = await runner.run()

        scenario = result.scenarios[0]
        assert scenario.passed is False
        assert "failed validation" in (scenario.error or "").lower()
        assert any(not assertion.passed for assertion in scenario.assertions)


class TestGauntletRunnerArtifacts:
    @pytest.mark.asyncio
    async def test_temp_workspace_paths_not_reported_as_artifacts(self):
        runner = GauntletRunner(scenarios=["happy_path"])
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert "workspace_dir" not in result.scenarios[0].artifacts
