"""GauntletRunner — top-level orchestrator for gauntlet scenario execution."""

from __future__ import annotations

import tempfile
import time
import traceback

from forge.gauntlet.fixtures import create_fixture_workspace
from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import GauntletResult, ScenarioResult
from forge.gauntlet.scenarios import SCENARIO_FUNCTIONS, SCENARIO_REGISTRY


class GauntletRunner:
    """Top-level runner that orchestrates fixture creation, scenario execution, and result collection."""

    def __init__(
        self,
        *,
        scenarios: list[str] | None = None,
        chaos: bool = False,
        live: bool = False,
        workspace_dir: str | None = None,
    ) -> None:
        self.scenarios = scenarios
        self.chaos = chaos
        self.live = live
        self.workspace_dir = workspace_dir

    def _selected_scenarios(self) -> list[str]:
        """Return the list of scenario names to run."""
        if self.scenarios:
            return [s for s in self.scenarios if s in SCENARIO_REGISTRY]
        return list(SCENARIO_REGISTRY.keys())

    async def run(self) -> GauntletResult:
        """Main entry point: create fixtures, run scenarios, return aggregate result."""
        total_start = time.monotonic()
        results: list[ScenarioResult] = []
        selected = self._selected_scenarios()

        for scenario_name in selected:
            result = await self._run_scenario(scenario_name)
            results.append(result)

        total_duration = round(time.monotonic() - total_start, 4)
        return GauntletResult(scenarios=results, total_duration_s=total_duration)

    async def _run_scenario(self, name: str) -> ScenarioResult:
        """Run a single scenario with an isolated fixture workspace."""
        start = time.monotonic()
        scenario_fn = SCENARIO_FUNCTIONS.get(name)
        config = SCENARIO_REGISTRY.get(name)

        if not scenario_fn or not config:
            return ScenarioResult(
                name=name,
                passed=False,
                duration_s=0.0,
                error=f"Unknown scenario: {name}",
            )

        try:
            # Each scenario gets a fresh fixture workspace for isolation
            if self.workspace_dir:
                workspace_base = self.workspace_dir
                repos = create_fixture_workspace(workspace_base)
            else:
                tmp = tempfile.mkdtemp(prefix=f"gauntlet-{name}-")
                repos = create_fixture_workspace(tmp)
                workspace_base = tmp

            chaos_for_scenario = self.chaos and config.chaos_compatible

            pipeline = MockPipeline(
                workspace_dir=workspace_base,
                repos=repos,
                chaos=chaos_for_scenario,
            )

            result = await scenario_fn(pipeline, repos)
            result.duration_s = round(time.monotonic() - start, 4)
            result.cost_usd = pipeline.cost_usd
            return result

        except Exception as exc:
            return ScenarioResult(
                name=name,
                passed=False,
                duration_s=round(time.monotonic() - start, 4),
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
