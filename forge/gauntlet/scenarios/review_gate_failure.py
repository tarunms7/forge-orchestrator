"""Review gate failure scenario — review stage failure detection and error state transitions."""

from __future__ import annotations

from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, ScenarioResult

TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"


async def run_review_gate_failure(
    pipeline: MockPipeline, workspace: dict[str, str]
) -> ScenarioResult:
    """Run pipeline with fail_at='review' and assert correct failure handling."""
    # Override fail_at for this scenario
    pipeline.fail_at = "review"

    stages = await pipeline.run_full(TASK_DESCRIPTION)
    assertions: list[AssertionResult] = []
    artifacts: dict[str, str] = {}

    # Assert: preflight passes
    preflight = next((s for s in stages if s.name == "preflight"), None)
    assertions.append(
        AssertionResult(
            name="preflight_passes",
            passed=preflight is not None and preflight.passed,
            message="Preflight passed"
            if preflight and preflight.passed
            else "Preflight did not pass",
        )
    )

    # Assert: planning passes
    planning = next((s for s in stages if s.name == "planning"), None)
    assertions.append(
        AssertionResult(
            name="planning_passes",
            passed=planning is not None and planning.passed,
            message="Planning passed" if planning and planning.passed else "Planning did not pass",
        )
    )

    # Assert: contracts passes
    contracts = next((s for s in stages if s.name == "contracts"), None)
    assertions.append(
        AssertionResult(
            name="contracts_passes",
            passed=contracts is not None and contracts.passed,
            message="Contracts passed"
            if contracts and contracts.passed
            else "Contracts did not pass",
        )
    )

    # Assert: execution passes
    execution = next((s for s in stages if s.name == "execution"), None)
    assertions.append(
        AssertionResult(
            name="execution_passes",
            passed=execution is not None and execution.passed,
            message="Execution passed"
            if execution and execution.passed
            else "Execution did not pass",
        )
    )

    # Assert: review stage fails
    review = next((s for s in stages if s.name == "review"), None)
    review_failed = review is not None and not review.passed
    assertions.append(
        AssertionResult(
            name="review_stage_fails",
            passed=review_failed,
            message="Review stage failed as expected"
            if review_failed
            else "Review stage did not fail as expected",
        )
    )

    # Assert: review failure has details explaining why
    review_has_details = review is not None and len(review.details) > 0
    assertions.append(
        AssertionResult(
            name="review_failure_has_details",
            passed=review_has_details,
            message=f"Review failure details: {review.details}"
            if review_has_details
            else "Review failure has no details",
        )
    )

    # Assert: pipeline stops at review (no integration stage)
    integration = next((s for s in stages if s.name == "integration"), None)
    no_integration = integration is None
    assertions.append(
        AssertionResult(
            name="pipeline_stops_at_review",
            passed=no_integration,
            message="Pipeline stopped at review, no integration ran"
            if no_integration
            else "Integration stage ran despite review failure",
        )
    )

    # Assert: pipeline status is error (not all stages passed)
    pipeline_errored = not all(s.passed for s in stages)
    assertions.append(
        AssertionResult(
            name="pipeline_status_error",
            passed=pipeline_errored,
            message="Pipeline status reflects error state"
            if pipeline_errored
            else "Pipeline incorrectly shows success despite review failure",
        )
    )

    artifacts["workspace_dir"] = pipeline.workspace_dir
    artifacts["failed_stage"] = "review"
    scenario_passed = all(a.passed for a in assertions)
    return ScenarioResult(
        name="review_gate_failure",
        passed=scenario_passed,
        duration_s=0.0,
        stages=stages,
        assertions=assertions,
        artifacts=artifacts,
    )
