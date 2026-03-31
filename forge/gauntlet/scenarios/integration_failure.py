"""Integration failure scenario — post-merge integration check failure detection."""

from __future__ import annotations

from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, ScenarioResult

TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"


async def run_integration_failure(
    pipeline: MockPipeline, workspace: dict[str, str]
) -> ScenarioResult:
    """Run pipeline with fail_at='integration' and assert correct failure handling."""
    # Override fail_at for this scenario
    pipeline.fail_at = "integration"

    stages = await pipeline.run_full(TASK_DESCRIPTION)
    assertions: list[AssertionResult] = []
    artifacts: dict[str, str] = {}

    # Assert: all stages up to integration pass
    pre_integration = [s for s in stages if s.name != "integration"]
    pre_all_passed = all(s.passed for s in pre_integration)
    assertions.append(
        AssertionResult(
            name="all_stages_before_integration_pass",
            passed=pre_all_passed,
            message="All stages before integration passed"
            if pre_all_passed
            else f"Failed pre-integration stages: {[s.name for s in pre_integration if not s.passed]}",
        )
    )

    # Assert: integration stage exists and fails
    integration = next((s for s in stages if s.name == "integration"), None)
    integration_failed = integration is not None and not integration.passed
    assertions.append(
        AssertionResult(
            name="integration_check_fails",
            passed=integration_failed,
            message="Integration check failed as expected"
            if integration_failed
            else "Integration check did not fail as expected"
            + (f" (integration={integration})" if integration else " (no integration stage)"),
        )
    )

    # Assert: integration failure has details (is_regression indication)
    integration_has_details = integration is not None and len(integration.details) > 0
    assertions.append(
        AssertionResult(
            name="integration_failure_has_details",
            passed=integration_has_details,
            message=f"Integration failure details: {integration.details}"
            if integration_has_details
            else "Integration failure has no details",
        )
    )

    # Assert: all 6 stages ran (integration runs even though it fails — it's post-merge)
    all_6_stages = len(stages) == 6
    assertions.append(
        AssertionResult(
            name="all_stages_ran",
            passed=all_6_stages,
            message="All 6 stages ran including integration"
            if all_6_stages
            else f"Expected 6 stages, got {len(stages)}: {[s.name for s in stages]}",
        )
    )

    # Assert: execution tasks completed (tasks remain DONE even after integration failure)
    execution = next((s for s in stages if s.name == "execution"), None)
    tasks_done = execution is not None and execution.passed
    assertions.append(
        AssertionResult(
            name="tasks_remain_done",
            passed=tasks_done,
            message="Tasks remain DONE despite integration failure (post-merge)"
            if tasks_done
            else "Task execution state was affected by integration failure",
        )
    )

    # Assert: review passed (review is pre-integration)
    review = next((s for s in stages if s.name == "review"), None)
    review_passed = review is not None and review.passed
    assertions.append(
        AssertionResult(
            name="review_passed",
            passed=review_passed,
            message="Review passed before integration failure"
            if review_passed
            else "Review did not pass",
        )
    )

    artifacts["workspace_dir"] = pipeline.workspace_dir
    artifacts["failed_stage"] = "integration"
    scenario_passed = all(a.passed for a in assertions)
    return ScenarioResult(
        name="integration_failure",
        passed=scenario_passed,
        duration_s=0.0,
        stages=stages,
        assertions=assertions,
        artifacts=artifacts,
    )
