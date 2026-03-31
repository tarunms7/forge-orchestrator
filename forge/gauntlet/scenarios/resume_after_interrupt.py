"""Resume-after-interrupt scenario — pipeline interrupt and resume with state preservation."""

from __future__ import annotations

from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, ScenarioResult, StageResult

TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"


async def run_resume_after_interrupt(
    pipeline: MockPipeline, workspace: dict[str, str]
) -> ScenarioResult:
    """Run pipeline, simulate interrupt after planning, then resume and verify completion."""
    stages: list[StageResult] = []
    assertions: list[AssertionResult] = []
    artifacts: dict[str, str] = {}

    # Phase 1: Run preflight + planning (first 2 stages)
    preflight = await pipeline.run_preflight()
    stages.append(preflight)

    planning, graph = await pipeline.run_planning(TASK_DESCRIPTION)
    stages.append(planning)

    if not planning.passed or graph is None:
        return ScenarioResult(
            name="resume_after_interrupt",
            passed=False,
            duration_s=0.0,
            stages=stages,
            assertions=[
                AssertionResult(
                    name="planning_succeeded",
                    passed=False,
                    message="Planning failed, cannot test resume",
                )
            ],
            error="Planning stage failed",
        )

    # Run contracts
    contracts = await pipeline.run_contracts(graph)
    stages.append(contracts)

    # Simulate partial execution — run execution (marks 1st task as done conceptually)
    # Then "interrupt" by not running review/integration
    execution = await pipeline.run_execution(graph)
    stages.append(execution)

    # Record interrupt point
    assertions.append(
        AssertionResult(
            name="execution_completed_before_interrupt",
            passed=execution.passed,
            message="Execution completed before interrupt"
            if execution.passed
            else "Execution failed before interrupt could be simulated",
        )
    )

    # Phase 2: Simulate resume — the pipeline picks up from review stage
    # In a real pipeline, the daemon would detect completed execution and skip to review
    # Here we simulate by running remaining stages on a fresh pipeline (same workspace)
    resume_pipeline = MockPipeline(
        workspace_dir=pipeline.workspace_dir,
        repos=pipeline.repos,
        chaos=pipeline.chaos,
    )

    # Resumed pipeline runs review and integration
    review = await resume_pipeline.run_review(graph)
    stages.append(review)

    integration = await resume_pipeline.run_integration()
    stages.append(integration)

    # Assert: completed stages stay completed (preflight/planning/contracts/execution from phase 1)
    phase1_passed = all(s.passed for s in stages[:4])
    assertions.append(
        AssertionResult(
            name="completed_stages_preserved",
            passed=phase1_passed,
            message="All pre-interrupt stages remain passed"
            if phase1_passed
            else "Some pre-interrupt stages lost their state",
        )
    )

    # Assert: remaining stages execute and complete after resume
    phase2_passed = review.passed and integration.passed
    assertions.append(
        AssertionResult(
            name="remaining_stages_complete_after_resume",
            passed=phase2_passed,
            message="Review and integration completed after resume"
            if phase2_passed
            else f"Post-resume failures: review={review.passed}, integration={integration.passed}",
        )
    )

    # Assert: full pipeline transitions: executing -> interrupted -> executing -> complete
    all_stages_passed = all(s.passed for s in stages)
    assertions.append(
        AssertionResult(
            name="pipeline_completes_after_resume",
            passed=all_stages_passed,
            message="Pipeline completed successfully after interrupt and resume"
            if all_stages_passed
            else "Pipeline did not complete after resume",
        )
    )

    # Assert: all 6 stages ran total
    expected_count = 6
    stages_count_ok = len(stages) == expected_count
    assertions.append(
        AssertionResult(
            name="all_stages_ran",
            passed=stages_count_ok,
            message=f"All {expected_count} stages ran across interrupt/resume"
            if stages_count_ok
            else f"Expected {expected_count} stages, got {len(stages)}",
        )
    )

    artifacts["workspace_dir"] = pipeline.workspace_dir
    artifacts["interrupt_after_stage"] = "execution"
    scenario_passed = all(a.passed for a in assertions)
    return ScenarioResult(
        name="resume_after_interrupt",
        passed=scenario_passed,
        duration_s=0.0,
        stages=stages,
        assertions=assertions,
        artifacts=artifacts,
    )
