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

    completed_stage_names = [stage.name for stage in stages if stage.passed]
    state_path = pipeline.persist_state(
        task_description=TASK_DESCRIPTION,
        completed_stages=completed_stage_names,
        graph=graph,
    )

    # Phase 2: Simulate resume using persisted state on disk so the scenario
    # validates state round-tripping rather than sharing in-memory objects.
    resume_pipeline = MockPipeline(
        workspace_dir=pipeline.workspace_dir,
        repos=pipeline.repos,
        chaos=pipeline.chaos,
    )
    task_description, loaded_completed_stages, loaded_graph, _ = resume_pipeline.load_state()
    resumed_stages = await resume_pipeline.resume_from_state()
    stages.extend(resumed_stages)
    review = next((stage for stage in resumed_stages if stage.name == "review"), None)
    integration = next((stage for stage in resumed_stages if stage.name == "integration"), None)

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
    phase2_passed = (
        review is not None and review.passed and integration is not None and integration.passed
    )
    assertions.append(
        AssertionResult(
            name="remaining_stages_complete_after_resume",
            passed=phase2_passed,
            message="Review and integration completed after resume"
            if phase2_passed
            else f"Post-resume failures: review={review.passed}, integration={integration.passed}",
        )
    )

    state_round_trip_ok = (
        task_description == TASK_DESCRIPTION
        and loaded_completed_stages == completed_stage_names
        and loaded_graph is not None
        and [task.id for task in loaded_graph.tasks] == [task.id for task in graph.tasks]
    )
    assertions.append(
        AssertionResult(
            name="resume_state_round_trips",
            passed=state_round_trip_ok,
            message="Resume state persisted and reloaded correctly"
            if state_round_trip_ok
            else "Resume state did not reload the expected task graph",
        )
    )

    resumed_only_remaining_stages = [stage.name for stage in resumed_stages] == [
        "review",
        "integration",
    ]
    assertions.append(
        AssertionResult(
            name="resume_only_runs_remaining_stages",
            passed=resumed_only_remaining_stages,
            message="Resume skipped completed work and ran only review/integration"
            if resumed_only_remaining_stages
            else f"Resume ran stages: {[stage.name for stage in resumed_stages]}",
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

    artifacts["interrupt_after_stage"] = "execution"
    artifacts["resume_state_file"] = state_path
    scenario_passed = all(a.passed for a in assertions)
    return ScenarioResult(
        name="resume_after_interrupt",
        passed=scenario_passed,
        duration_s=0.0,
        stages=stages,
        assertions=assertions,
        artifacts=artifacts,
    )
