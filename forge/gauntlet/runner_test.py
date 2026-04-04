"""Tests for forge.gauntlet.runner — GauntletRunner orchestration."""

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
    async def test_live_mode_sdk_unavailable(self, tmp_path):
        """When claude CLI is not on PATH, live mode returns a clear error."""
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
    async def test_live_mode_creates_daemon_and_runs(self, tmp_path):
        """When claude CLI is available, live mode instantiates ForgeDaemon and calls run()."""
        mock_daemon_instance = AsyncMock()
        mock_daemon_instance.run = AsyncMock(return_value=None)

        with (
            patch("forge.gauntlet.runner._claude_cli_available", return_value=True),
            patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon_instance) as mock_cls,
        ):
            runner = GauntletRunner(
                scenarios=["happy_path"],
                live=True,
                workspace_dir=str(tmp_path),
            )
            result = await runner.run()

            assert len(result.scenarios) == 1
            assert result.scenarios[0].passed is True
            # Verify ForgeDaemon was constructed with correct args
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["project_dir"] == str(tmp_path)
            assert len(call_kwargs["repos"]) == 3
            # Verify daemon.run() was called with the task description
            mock_daemon_instance.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_live_mode_timeout_returns_failure(self, tmp_path):
        """When daemon.run() exceeds timeout, live mode returns a timeout error."""
        import asyncio

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(999)

        mock_daemon_instance = AsyncMock()
        mock_daemon_instance.run = slow_run

        with (
            patch("forge.gauntlet.runner._claude_cli_available", return_value=True),
            patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon_instance),
            patch("forge.gauntlet.runner.LIVE_SCENARIO_TIMEOUT", 0.1),
        ):
            runner = GauntletRunner(
                scenarios=["happy_path"],
                live=True,
                workspace_dir=str(tmp_path),
            )
            result = await runner.run()

            assert len(result.scenarios) == 1
            assert result.scenarios[0].passed is False
            assert "timed out" in result.scenarios[0].error.lower()

    @pytest.mark.asyncio
    async def test_live_mode_daemon_exception_returns_failure(self, tmp_path):
        """When daemon.run() raises, live mode captures the error."""
        mock_daemon_instance = AsyncMock()
        mock_daemon_instance.run = AsyncMock(side_effect=RuntimeError("SDK auth failed"))

        with (
            patch("forge.gauntlet.runner._claude_cli_available", return_value=True),
            patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon_instance),
        ):
            runner = GauntletRunner(
                scenarios=["happy_path"],
                live=True,
                workspace_dir=str(tmp_path),
            )
            result = await runner.run()

            assert len(result.scenarios) == 1
            assert result.scenarios[0].passed is False
            assert "SDK auth failed" in result.scenarios[0].error


class TestGauntletRunnerArtifacts:
    @pytest.mark.asyncio
    async def test_temp_workspace_paths_not_reported_as_artifacts(self):
        runner = GauntletRunner(scenarios=["happy_path"])
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert "workspace_dir" not in result.scenarios[0].artifacts
