"""GauntletRunner — top-level orchestrator for gauntlet scenario execution."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import traceback

from forge.gauntlet.fixtures import create_fixture_workspace
from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import GauntletResult, ScenarioResult
from forge.gauntlet.scenarios import SCENARIO_FUNCTIONS, SCENARIO_REGISTRY


class UnknownScenarioError(ValueError):
    """Raised when a caller requests gauntlet scenarios that do not exist."""


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
            unknown = [s for s in self.scenarios if s not in SCENARIO_REGISTRY]
            if unknown:
                available = ", ".join(sorted(SCENARIO_REGISTRY))
                unknown_str = ", ".join(sorted(set(unknown)))
                raise UnknownScenarioError(
                    f"Unknown gauntlet scenario(s): {unknown_str}. Available scenarios: {available}"
                )
            return list(self.scenarios)
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

        if self.live:
            return ScenarioResult(
                name=name,
                passed=False,
                duration_s=round(time.monotonic() - start, 4),
                error="Live mode (ForgeDaemon + Claude SDK) is not yet implemented. "
                "Use live=False to run with MockPipeline.",
            )

        tmp_dir: str | None = None
        try:
            # Each scenario gets a fresh fixture workspace for isolation
            if self.workspace_dir:
                workspace_base = self.workspace_dir
            else:
                tmp_dir = tempfile.mkdtemp(prefix=f"gauntlet-{name}-")
                workspace_base = tmp_dir

            repos = create_fixture_workspace(workspace_base)
            chaos_for_scenario = self.chaos and config.chaos_compatible

            pipeline = MockPipeline(
                workspace_dir=workspace_base,
                repos=repos,
                chaos=chaos_for_scenario,
            )

            result = await scenario_fn(pipeline, repos)
            if tmp_dir and result.artifacts:
                tmp_root = os.path.abspath(tmp_dir)
                retained_artifacts: dict[str, str] = {}
                for key, value in result.artifacts.items():
                    if not isinstance(value, str):
                        retained_artifacts[key] = value
                        continue
                    artifact_path = os.path.abspath(value)
                    if artifact_path == tmp_root or artifact_path.startswith(tmp_root + os.sep):
                        continue
                    retained_artifacts[key] = value
                result.artifacts = retained_artifacts
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
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
