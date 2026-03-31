"""Tests for forge.gauntlet.runner — GauntletRunner orchestration."""

import pytest

from forge.gauntlet.runner import GauntletRunner


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
    async def test_invalid_scenario_name_skipped(self, tmp_path):
        """Invalid scenario names are silently filtered by _selected_scenarios.

        The runner's _selected_scenarios() filters to names present in
        SCENARIO_REGISTRY, so unrecognized names produce an empty run
        rather than raising an error.
        """
        runner = GauntletRunner(
            scenarios=["nonexistent_scenario"],
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 0
        assert result.passed is True  # vacuously true — no scenarios failed

    @pytest.mark.asyncio
    async def test_mix_valid_and_invalid(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path", "nonexistent"],
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert result.scenarios[0].name == "happy_path"


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
    async def test_live_mode_returns_error(self, tmp_path):
        runner = GauntletRunner(
            scenarios=["happy_path"],
            live=True,
            workspace_dir=str(tmp_path),
        )
        result = await runner.run()
        assert len(result.scenarios) == 1
        assert result.scenarios[0].passed is False
        assert "not yet implemented" in result.scenarios[0].error.lower()
