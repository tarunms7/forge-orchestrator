"""Multi-repo contracts scenario — cross-repo contract generation and validation."""

from __future__ import annotations

from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, ScenarioResult

TASK_DESCRIPTION = "Fix calculator bugs across backend, frontend, and shared-types repos"


async def run_multi_repo_contracts(
    pipeline: MockPipeline, workspace: dict[str, str]
) -> ScenarioResult:
    """Run planning + contract generation and validate cross-repo contracts."""
    stages = []
    assertions: list[AssertionResult] = []
    artifacts: dict[str, str] = {}

    # Run preflight
    preflight = await pipeline.run_preflight()
    stages.append(preflight)

    # Run planning
    planning, graph = await pipeline.run_planning(TASK_DESCRIPTION)
    stages.append(planning)

    if not planning.passed or graph is None:
        return ScenarioResult(
            name="multi_repo_contracts",
            passed=False,
            duration_s=0.0,
            stages=stages,
            assertions=[
                AssertionResult(
                    name="planning_succeeded",
                    passed=False,
                    message="Planning failed, cannot test contracts",
                )
            ],
            error="Planning stage failed",
        )

    # Run contracts
    contracts_result = await pipeline.run_contracts(graph)
    stages.append(contracts_result)

    # Assert contracts contain cross-repo API contract
    contract_set = getattr(pipeline, "_contract_set", None)
    has_api_contract = (
        contract_set is not None
        and len(contract_set.api_contracts) > 0
        and contract_set.api_contracts[0].id == "contract-api-calculate"
    )
    assertions.append(
        AssertionResult(
            name="contracts_contain_cross_repo",
            passed=has_api_contract,
            message="Cross-repo API contract between backend and shared-types exists"
            if has_api_contract
            else "Missing cross-repo API contract",
        )
    )

    # Assert API contract links backend (producer) to frontend (consumer)
    if has_api_contract and contract_set:
        api = contract_set.api_contracts[0]
        producer_ok = api.producer_task_id == "fix-backend-bug"
        consumer_ok = "fix-frontend-import" in api.consumer_task_ids
        cross_repo_ok = producer_ok and consumer_ok
        assertions.append(
            AssertionResult(
                name="api_contract_cross_repo_link",
                passed=cross_repo_ok,
                message="API contract links backend producer to frontend consumer"
                if cross_repo_ok
                else f"Producer: {api.producer_task_id}, Consumers: {api.consumer_task_ids}",
            )
        )
    else:
        assertions.append(
            AssertionResult(
                name="api_contract_cross_repo_link",
                passed=False,
                message="No API contract to validate",
            )
        )

    # Assert TypeContract references CalculationRequest/CalculationResponse
    type_names = [tc.name for tc in contract_set.type_contracts] if contract_set else []
    has_calc_request = "CalculationRequest" in type_names
    has_calc_response = "CalculationResponse" in type_names
    type_ok = has_calc_request and has_calc_response
    assertions.append(
        AssertionResult(
            name="type_contracts_reference_shared_types",
            passed=type_ok,
            message="TypeContracts reference CalculationRequest and CalculationResponse"
            if type_ok
            else f"Found type contracts: {type_names}",
        )
    )

    # Assert type contracts are used by correct tasks
    if contract_set and type_ok:
        req_contract = next(
            tc for tc in contract_set.type_contracts if tc.name == "CalculationRequest"
        )
        resp_contract = next(
            tc for tc in contract_set.type_contracts if tc.name == "CalculationResponse"
        )
        req_tasks_ok = (
            "fix-backend-bug" in req_contract.used_by_tasks
            and "update-shared-types" in req_contract.used_by_tasks
        )
        resp_tasks_ok = (
            "fix-backend-bug" in resp_contract.used_by_tasks
            and "update-shared-types" in resp_contract.used_by_tasks
        )
        usage_ok = req_tasks_ok and resp_tasks_ok
        assertions.append(
            AssertionResult(
                name="type_contracts_used_by_correct_tasks",
                passed=usage_ok,
                message="Type contracts used by fix-backend-bug and update-shared-types"
                if usage_ok
                else f"CalculationRequest used_by: {req_contract.used_by_tasks}, CalculationResponse used_by: {resp_contract.used_by_tasks}",
            )
        )
    else:
        assertions.append(
            AssertionResult(
                name="type_contracts_used_by_correct_tasks",
                passed=False,
                message="Cannot validate type contract usage",
            )
        )

    # Assert tasks have correct repo assignments
    task_repos = {t.id: t.repo for t in graph.tasks}
    repo_ok = (
        task_repos.get("fix-backend-bug") == "backend"
        and task_repos.get("fix-frontend-import") == "frontend"
        and task_repos.get("update-shared-types") == "shared-types"
    )
    assertions.append(
        AssertionResult(
            name="tasks_have_correct_repo_assignments",
            passed=repo_ok,
            message="All tasks assigned to correct repos"
            if repo_ok
            else f"Task repo assignments: {task_repos}",
        )
    )

    artifacts["workspace_dir"] = pipeline.workspace_dir
    scenario_passed = all(a.passed for a in assertions)
    return ScenarioResult(
        name="multi_repo_contracts",
        passed=scenario_passed,
        duration_s=0.0,
        stages=stages,
        assertions=assertions,
        artifacts=artifacts,
    )
