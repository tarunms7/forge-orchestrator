"""Mock pipeline simulating ForgeDaemon stages for deterministic gauntlet testing."""

from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import time

from forge.core.contracts import APIContract, ContractSet, FieldSpec, TypeContract
from forge.core.models import Complexity, TaskDefinition, TaskGraph
from forge.gauntlet.models import StageResult


def _run_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _default_branch(cwd: str) -> str:
    """Get the current branch name (handles both 'main' and 'master')."""
    result = _run_git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip()


class MockPipeline:
    """Simulated pipeline that mirrors ForgeDaemon stages for deterministic testing."""

    def __init__(
        self,
        workspace_dir: str,
        repos: dict[str, str],
        *,
        fail_at: str | None = None,
        chaos: bool = False,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.repos = repos
        self.fail_at = fail_at
        self.chaos = chaos
        self._cost_usd = 0.0
        self._state_path = os.path.join(self.workspace_dir, ".gauntlet_resume_state.json")

    @property
    def cost_usd(self) -> float:
        return self._cost_usd

    def _should_fail(self, stage: str) -> bool:
        return self.fail_at == stage

    async def _chaos_delay(self) -> None:
        if self.chaos:
            await asyncio.sleep(random.uniform(0.01, 0.05))

    def _timed(self, start: float) -> float:
        return round(time.monotonic() - start, 4)

    def persist_state(
        self,
        *,
        task_description: str,
        completed_stages: list[str],
        graph: TaskGraph | None = None,
    ) -> str:
        """Persist mock pipeline state so resume scenarios exercise disk round-tripping."""
        payload: dict[str, object] = {
            "task_description": task_description,
            "completed_stages": completed_stages,
            "cost_usd": self._cost_usd,
        }
        if graph is not None:
            payload["graph"] = graph.model_dump(mode="json")

        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        return self._state_path

    def load_state(self) -> tuple[str, list[str], TaskGraph | None, float]:
        """Load persisted mock pipeline state from disk."""
        with open(self._state_path, encoding="utf-8") as f:
            data = json.load(f)

        graph_data = data.get("graph")
        graph = TaskGraph.model_validate(graph_data) if graph_data is not None else None
        return (
            str(data["task_description"]),
            list(data.get("completed_stages", [])),
            graph,
            float(data.get("cost_usd", 0.0)),
        )

    async def resume_from_state(self) -> list[StageResult]:
        """Resume a persisted mock pipeline from the next incomplete stage."""
        task_description, completed_stages, graph, saved_cost = self.load_state()
        self._cost_usd = max(self._cost_usd, saved_cost)
        completed = set(completed_stages)
        results: list[StageResult] = []

        if "preflight" not in completed:
            preflight = await self.run_preflight()
            results.append(preflight)
            if not preflight.passed:
                return results

        if "planning" not in completed:
            planning, graph = await self.run_planning(task_description)
            results.append(planning)
            if not planning.passed or graph is None:
                return results
        elif graph is None:
            raise ValueError("Resume state is missing TaskGraph data after planning completed")

        if "contracts" not in completed:
            contracts = await self.run_contracts(graph)
            results.append(contracts)
            if not contracts.passed:
                return results

        if "execution" not in completed:
            execution = await self.run_execution(graph)
            results.append(execution)
            if not execution.passed:
                return results

        if "review" not in completed:
            review = await self.run_review(graph)
            results.append(review)
            if not review.passed:
                return results

        if "integration" not in completed:
            integration = await self.run_integration()
            results.append(integration)

        return results

    async def run_preflight(self) -> StageResult:
        """Validate repos exist and workspace is valid."""
        start = time.monotonic()
        await self._chaos_delay()

        if self._should_fail("preflight"):
            return StageResult(
                name="preflight",
                passed=False,
                duration_s=self._timed(start),
                details="Injected preflight failure",
            )

        missing = [rid for rid, path in self.repos.items() if not os.path.isdir(path)]
        if missing:
            return StageResult(
                name="preflight",
                passed=False,
                duration_s=self._timed(start),
                details=f"Missing repos: {', '.join(missing)}",
            )

        return StageResult(
            name="preflight",
            passed=True,
            duration_s=self._timed(start),
            details=f"All {len(self.repos)} repos validated",
        )

    async def run_planning(self, task_description: str) -> tuple[StageResult, TaskGraph | None]:
        """Create a deterministic TaskGraph from fixture structure."""
        start = time.monotonic()
        await self._chaos_delay()
        self._cost_usd += 0.02  # simulated planning cost

        if self._should_fail("planning"):
            return (
                StageResult(
                    name="planning",
                    passed=False,
                    duration_s=self._timed(start),
                    details="Injected planning failure",
                ),
                None,
            )

        graph = TaskGraph(
            tasks=[
                TaskDefinition(
                    id="fix-backend-bug",
                    title="Fix division-by-zero bug in /calculate endpoint",
                    description="Handle b=0 case in the /calculate endpoint to return 400 instead of crashing.",
                    files=["app.py", "test_app.py"],
                    repo="backend",
                    complexity=Complexity.LOW,
                ),
                TaskDefinition(
                    id="fix-frontend-import",
                    title="Fix incorrect type import in frontend",
                    description="Change data.value to data.result to match the CalculationResponse schema.",
                    files=["index.js"],
                    repo="frontend",
                    complexity=Complexity.LOW,
                ),
                TaskDefinition(
                    id="update-shared-types",
                    title="Update shared type definitions",
                    description="Ensure CalculationRequest and CalculationResponse types are up to date.",
                    files=["types.py"],
                    repo="shared-types",
                    complexity=Complexity.LOW,
                ),
            ],
        )

        return (
            StageResult(
                name="planning",
                passed=True,
                duration_s=self._timed(start),
                details=f"Created {len(graph.tasks)} tasks from: {task_description}",
            ),
            graph,
        )

    async def run_contracts(self, graph: TaskGraph) -> StageResult:
        """Generate mock ContractSet with cross-repo API contract."""
        start = time.monotonic()
        await self._chaos_delay()
        self._cost_usd += 0.01  # simulated contract cost

        if self._should_fail("contracts"):
            return StageResult(
                name="contracts",
                passed=False,
                duration_s=self._timed(start),
                details="Injected contracts failure",
            )

        # Store contract set for potential later inspection
        self._contract_set = ContractSet(
            api_contracts=[
                APIContract(
                    id="contract-api-calculate",
                    method="POST",
                    path="/calculate",
                    description="Calculation endpoint: accepts two numbers, returns result",
                    request_body=[
                        FieldSpec(name="a", type="number"),
                        FieldSpec(name="b", type="number"),
                    ],
                    response_body=[
                        FieldSpec(name="result", type="number"),
                    ],
                    producer_task_id="fix-backend-bug",
                    consumer_task_ids=["fix-frontend-import"],
                ),
            ],
            type_contracts=[
                TypeContract(
                    name="CalculationRequest",
                    description="Request payload for /calculate",
                    field_specs=[
                        FieldSpec(name="a", type="number"),
                        FieldSpec(name="b", type="number"),
                    ],
                    used_by_tasks=["fix-backend-bug", "update-shared-types"],
                ),
                TypeContract(
                    name="CalculationResponse",
                    description="Response payload for /calculate",
                    field_specs=[
                        FieldSpec(name="result", type="number"),
                    ],
                    used_by_tasks=["fix-backend-bug", "update-shared-types"],
                ),
            ],
        )

        return StageResult(
            name="contracts",
            passed=True,
            duration_s=self._timed(start),
            details=f"Generated {len(self._contract_set.api_contracts)} API contracts, "
            f"{len(self._contract_set.type_contracts)} type contracts",
        )

    async def run_execution(self, graph: TaskGraph) -> StageResult:
        """Simulate agent execution: create git branches and commit fixes."""
        start = time.monotonic()
        await self._chaos_delay()
        self._cost_usd += 0.05  # simulated execution cost

        if self._should_fail("execution"):
            return StageResult(
                name="execution",
                passed=False,
                duration_s=self._timed(start),
                details="Injected execution failure",
            )

        for task in graph.tasks:
            repo_path = self.repos.get(task.repo)
            if not repo_path or not os.path.isdir(repo_path):
                continue

            # Save current branch before switching
            default_branch = _default_branch(repo_path)
            branch = f"forge/{task.id}"
            _run_git(repo_path, "checkout", "-b", branch)

            # Simulate a fix by appending a comment to each file
            for fname in task.files:
                fpath = os.path.join(repo_path, fname)
                if os.path.isfile(fpath):
                    with open(fpath, "a") as f:
                        f.write(f"\n# Fixed by forge agent: {task.id}\n")

            _run_git(repo_path, "add", ".")
            _run_git(repo_path, "commit", "-m", f"fix: {task.title}")
            # Return to default branch
            _run_git(repo_path, "checkout", default_branch)

        return StageResult(
            name="execution",
            passed=True,
            duration_s=self._timed(start),
            details=f"Executed {len(graph.tasks)} tasks across repos",
        )

    async def run_review(self, graph: TaskGraph) -> StageResult:
        """Simulate review gate pass/fail based on fail_at setting."""
        start = time.monotonic()
        await self._chaos_delay()
        self._cost_usd += 0.01  # simulated review cost

        if self._should_fail("review"):
            return StageResult(
                name="review",
                passed=False,
                duration_s=self._timed(start),
                details="Injected review failure",
            )

        return StageResult(
            name="review",
            passed=True,
            duration_s=self._timed(start),
            details=f"All {len(graph.tasks)} tasks passed review",
        )

    async def run_integration(self) -> StageResult:
        """Simulate post-merge integration health check."""
        start = time.monotonic()
        await self._chaos_delay()

        if self._should_fail("integration"):
            return StageResult(
                name="integration",
                passed=False,
                duration_s=self._timed(start),
                details="Injected integration failure",
            )

        return StageResult(
            name="integration",
            passed=True,
            duration_s=self._timed(start),
            details="Integration health check passed",
        )

    async def run_full(self, task_description: str) -> list[StageResult]:
        """Run all stages in order, stopping on first failure."""
        results: list[StageResult] = []

        # Preflight
        preflight = await self.run_preflight()
        results.append(preflight)
        if not preflight.passed:
            return results

        # Planning
        planning, graph = await self.run_planning(task_description)
        results.append(planning)
        if not planning.passed or graph is None:
            return results

        # Contracts
        contracts = await self.run_contracts(graph)
        results.append(contracts)
        if not contracts.passed:
            return results

        # Execution
        execution = await self.run_execution(graph)
        results.append(execution)
        if not execution.passed:
            return results

        # Review
        review = await self.run_review(graph)
        results.append(review)
        if not review.passed:
            return results

        # Integration
        integration = await self.run_integration()
        results.append(integration)

        return results
