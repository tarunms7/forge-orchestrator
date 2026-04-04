"""GauntletRunner — top-level orchestrator for gauntlet scenario execution."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import traceback
from contextlib import contextmanager

from forge.gauntlet.fixtures import create_fixture_workspace, setup_forge_config
from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, GauntletResult, ScenarioResult, StageResult
from forge.gauntlet.scenarios import SCENARIO_FUNCTIONS, SCENARIO_REGISTRY

LIVE_SCENARIO_TIMEOUT = 300
LIVE_TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"
_LIVE_SUPPORTED_SCENARIOS = frozenset({"happy_path"})


class UnknownScenarioError(ValueError):
    """Raised when a caller requests gauntlet scenarios that do not exist."""


def _claude_cli_available() -> bool:
    """Check whether the ``claude`` CLI binary is available on PATH."""
    return shutil.which("claude") is not None


@contextmanager
def _scoped_forge_data_dir(path: str):
    """Temporarily point Forge's central DB into the scenario workspace."""
    previous = os.environ.get("FORGE_DATA_DIR")
    os.environ["FORGE_DATA_DIR"] = path
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FORGE_DATA_DIR", None)
        else:
            os.environ["FORGE_DATA_DIR"] = previous


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
        """Run a live scenario using the real daemon and validate the DB outcome."""
        if name not in _LIVE_SUPPORTED_SCENARIOS:
            supported = ", ".join(sorted(_LIVE_SUPPORTED_SCENARIOS))
            return ScenarioResult(
                name=name,
                passed=False,
                duration_s=round(time.monotonic() - start, 4),
                error=f"Live mode currently supports only: {supported}",
            )
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
            from forge.core.paths import forge_db_url
            from forge.storage.db import Database

            if self.workspace_dir:
                workspace_base = self.workspace_dir
            else:
                tmp_dir = tempfile.mkdtemp(prefix=f"gauntlet-live-{name}-")
                workspace_base = tmp_dir

            repos = create_fixture_workspace(workspace_base)
            for repo_path in repos.values():
                setup_forge_config(repo_path)

            repo_configs = [
                RepoConfig(id=repo_id, path=repo_path, base_branch="main")
                for repo_id, repo_path in repos.items()
            ]
            settings = ForgeSettings(
                max_agents=1,
                agent_timeout_seconds=120,
                agent_max_turns=30,
                max_retries=1,
                budget_limit_usd=0.0,
            )

            data_dir = os.path.join(workspace_base, ".forge", "gauntlet-data")
            os.makedirs(data_dir, exist_ok=True)
            with _scoped_forge_data_dir(data_dir):
                daemon = ForgeDaemon(
                    project_dir=workspace_base,
                    settings=settings,
                    repos=repo_configs,
                )
                await asyncio.wait_for(
                    daemon.run(LIVE_TASK_DESCRIPTION),
                    timeout=LIVE_SCENARIO_TIMEOUT,
                )

                pipeline_id = getattr(daemon, "_pipeline_id", None)
                if not pipeline_id:
                    return ScenarioResult(
                        name=name,
                        passed=False,
                        duration_s=round(time.monotonic() - start, 4),
                        error="Live mode finished without recording a pipeline id",
                    )

                db = Database(forge_db_url())
                await db.initialize()
                try:
                    pipeline = await db.get_pipeline(pipeline_id)
                    tasks = await db.list_tasks_by_pipeline(pipeline_id)
                finally:
                    await db.close()

            return self._build_live_happy_path_result(
                name=name,
                start=start,
                workspace_base=workspace_base,
                include_workspace_artifact=bool(self.workspace_dir),
                pipeline=pipeline,
                tasks=tasks,
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

    @staticmethod
    def _build_live_happy_path_result(
        *,
        name: str,
        start: float,
        workspace_base: str,
        include_workspace_artifact: bool,
        pipeline,
        tasks: list,
    ) -> ScenarioResult:
        """Build a truthful happy-path result from the persisted live pipeline outcome."""
        task_graph = {}
        task_count = 0
        if pipeline and getattr(pipeline, "task_graph_json", None):
            try:
                task_graph = json.loads(pipeline.task_graph_json)
                task_count = len(task_graph.get("tasks", []) or [])
            except Exception:
                task_graph = {}

        contracts_generated = bool(getattr(pipeline, "contracts_json", None))
        task_states = [t.state for t in tasks]
        all_done = bool(tasks) and all(state == "done" for state in task_states)
        no_terminal_failures = not any(
            state in ("error", "blocked", "cancelled") for state in task_states
        )
        pipeline_complete = bool(pipeline) and getattr(pipeline, "status", "") == "complete"

        assertions = [
            AssertionResult(
                name="pipeline_completed",
                passed=pipeline_complete,
                message=f"Pipeline status is {getattr(pipeline, 'status', 'missing')}",
            ),
            AssertionResult(
                name="task_graph_present",
                passed=task_count > 0,
                message=f"Planned {task_count} task(s)",
            ),
            AssertionResult(
                name="contracts_generated",
                passed=contracts_generated,
                message="Contracts JSON was persisted"
                if contracts_generated
                else "Contracts JSON missing",
            ),
            AssertionResult(
                name="all_tasks_done",
                passed=all_done,
                message=f"Task states: {task_states}",
            ),
            AssertionResult(
                name="no_terminal_failures",
                passed=no_terminal_failures,
                message=f"Task states: {task_states}",
            ),
        ]

        stages = [
            StageResult(
                name="planning",
                passed=task_count > 0,
                duration_s=0.0,
                details=f"Planned {task_count} task(s)",
            ),
            StageResult(
                name="contracts",
                passed=contracts_generated,
                duration_s=0.0,
                details="Contracts generated" if contracts_generated else "Contracts missing",
            ),
            StageResult(
                name="execution",
                passed=all_done,
                duration_s=0.0,
                details=f"Task states: {task_states}",
            ),
        ]

        passed = all(assertion.passed for assertion in assertions)
        error = None
        if not passed:
            pipeline_status = getattr(pipeline, "status", "missing")
            error = f"Live happy_path failed validation: pipeline={pipeline_status}, task_states={task_states}"

        artifacts = {"mode": "live"}
        if include_workspace_artifact:
            artifacts["workspace_dir"] = workspace_base

        return ScenarioResult(
            name=name,
            passed=passed,
            duration_s=round(time.monotonic() - start, 4),
            stages=stages,
            assertions=assertions,
            artifacts=artifacts,
            cost_usd=float(getattr(pipeline, "total_cost_usd", 0.0) or 0.0),
            error=error,
        )
