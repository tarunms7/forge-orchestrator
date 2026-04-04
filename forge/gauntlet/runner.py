"""GauntletRunner — top-level orchestrator for gauntlet scenario execution."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import traceback

from forge.gauntlet.fixtures import create_fixture_workspace, setup_forge_config
from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import GauntletResult, ScenarioResult
from forge.gauntlet.scenarios import SCENARIO_FUNCTIONS, SCENARIO_REGISTRY

# Live mode timeout per scenario (seconds)
LIVE_SCENARIO_TIMEOUT = 300


LIVE_TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"


def _claude_cli_available() -> bool:
    """Check whether the ``claude`` CLI binary is on PATH."""
    return shutil.which("claude") is not None


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
            return await self._run_live_scenario(name, start)

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

    async def _run_live_scenario(self, name: str, start: float) -> ScenarioResult:
        """Run a scenario in live mode using ForgeDaemon + Claude SDK."""
        if not _claude_cli_available():
            return ScenarioResult(
                name=name,
                passed=False,
                duration_s=round(time.monotonic() - start, 4),
                error="Claude CLI not found. Install via: claude login",
            )

        tmp_dir: str | None = None
        try:
            from forge.config.settings import ForgeSettings
            from forge.core.daemon import ForgeDaemon
            from forge.core.models import RepoConfig

            if self.workspace_dir:
                workspace_base = self.workspace_dir
            else:
                tmp_dir = tempfile.mkdtemp(prefix=f"gauntlet-live-{name}-")
                workspace_base = tmp_dir

            repos = create_fixture_workspace(workspace_base)

            # Set up .forge/forge.toml with tests/lint disabled for each repo
            for repo_path in repos.values():
                setup_forge_config(repo_path)

            # Build RepoConfig list for ForgeDaemon
            repo_configs = [
                RepoConfig(id=repo_id, path=repo_path, base_branch="main")
                for repo_id, repo_path in repos.items()
            ]

            settings = ForgeSettings(
                max_agents=1,
                agent_timeout_seconds=120,
                agent_max_turns=30,
                max_retries=1,
                budget_limit_usd=0.0,  # unlimited for gauntlet
            )

            daemon = ForgeDaemon(
                project_dir=workspace_base,
                settings=settings,
                repos=repo_configs,
            )

            await asyncio.wait_for(
                daemon.run(LIVE_TASK_DESCRIPTION),
                timeout=LIVE_SCENARIO_TIMEOUT,
            )

            return ScenarioResult(
                name=name,
                passed=True,
                duration_s=round(time.monotonic() - start, 4),
                artifacts={"workspace_dir": workspace_base, "mode": "live"},
            )

        except TimeoutError:
            return ScenarioResult(
                name=name,
                passed=False,
                duration_s=round(time.monotonic() - start, 4),
                error=f"Live scenario timed out after {LIVE_SCENARIO_TIMEOUT}s",
            )
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
