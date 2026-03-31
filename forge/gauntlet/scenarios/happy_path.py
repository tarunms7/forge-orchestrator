"""Happy path scenario — full pipeline success with no injected failures."""

from __future__ import annotations

from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, ScenarioResult

TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"


async def run_happy_path(
    pipeline: MockPipeline, workspace: dict[str, str]
) -> ScenarioResult:
    """Run full pipeline with no injected failures and assert everything passes."""
    stages = await pipeline.run_full(TASK_DESCRIPTION)
    assertions: list[AssertionResult] = []
    artifacts: dict[str, str] = {}

    # Assert all stages pass
    all_passed = all(s.passed for s in stages)
    assertions.append(
        AssertionResult(
            name="all_stages_pass",
            passed=all_passed,
            message="All stages passed" if all_passed else f"Failed stages: {[s.name for s in stages if not s.passed]}",
        )
    )

    # Assert we got all 6 stages
    expected_stages = ["preflight", "planning", "contracts", "execution", "review", "integration"]
    actual_stages = [s.name for s in stages]
    stages_complete = actual_stages == expected_stages
    assertions.append(
        AssertionResult(
            name="all_stages_ran",
            passed=stages_complete,
            message=f"Expected {expected_stages}, got {actual_stages}",
        )
    )

    # Assert TaskGraph has 3 tasks (from planning stage)
    planning_result = next((s for s in stages if s.name == "planning"), None)
    has_3_tasks = planning_result is not None and "3 tasks" in planning_result.details
    assertions.append(
        AssertionResult(
            name="task_graph_has_3_tasks",
            passed=has_3_tasks,
            message="TaskGraph contains 3 tasks" if has_3_tasks else f"Planning details: {planning_result.details if planning_result else 'missing'}",
        )
    )

    # Assert contracts were generated
    contracts_result = next((s for s in stages if s.name == "contracts"), None)
    contracts_ok = contracts_result is not None and contracts_result.passed
    assertions.append(
        AssertionResult(
            name="contracts_generated",
            passed=contracts_ok,
            message="Contracts were generated successfully" if contracts_ok else "Contract generation failed",
        )
    )

    # Assert review passed
    review_result = next((s for s in stages if s.name == "review"), None)
    review_ok = review_result is not None and review_result.passed
    assertions.append(
        AssertionResult(
            name="review_passed",
            passed=review_ok,
            message="Review gate passed" if review_ok else "Review gate failed",
        )
    )

    # Assert pipeline status is complete (all stages ran and passed)
    pipeline_complete = len(stages) == 6 and all_passed
    assertions.append(
        AssertionResult(
            name="pipeline_status_complete",
            passed=pipeline_complete,
            message="Pipeline completed successfully" if pipeline_complete else "Pipeline did not complete",
        )
    )

    # Record artifacts
    artifacts["workspace_dir"] = pipeline.workspace_dir
    if stages:
        artifacts["stage_count"] = str(len(stages))

    scenario_passed = all(a.passed for a in assertions)
    return ScenarioResult(
        name="happy_path",
        passed=scenario_passed,
        duration_s=0.0,  # filled by runner
        stages=stages,
        assertions=assertions,
        artifacts=artifacts,
    )
