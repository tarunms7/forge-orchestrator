"""Tests for contract data models."""

from forge.core.contracts import (
    APIContract,
    ContractSet,
    ContractType,
    FieldSpec,
    IntegrationHint,
    TaskContracts,
    TypeContract,
)


# -- Helpers ---------------------------------------------------------------

def _sample_api_contract(
    producer="task-1", consumers=None, contract_id="contract-api-1",
) -> APIContract:
    return APIContract(
        id=contract_id,
        method="GET",
        path="/api/templates",
        description="List templates",
        response_body=[
            FieldSpec(name="builtin", type="Template[]"),
            FieldSpec(name="user", type="Template[]"),
        ],
        response_example='{"builtin": [], "user": []}',
        producer_task_id=producer,
        consumer_task_ids=consumers or ["task-2"],
    )


def _sample_type_contract(used_by=None) -> TypeContract:
    return TypeContract(
        name="Template",
        description="A pipeline template",
        fields=[
            FieldSpec(name="id", type="string", description="Unique ID"),
            FieldSpec(name="name", type="string"),
            FieldSpec(name="icon", type="string", required=False),
        ],
        used_by_tasks=used_by or ["task-1", "task-2"],
    )


# -- ContractSet tests ----------------------------------------------------


class TestContractSet:
    def test_has_contracts_empty(self):
        cs = ContractSet()
        assert cs.has_contracts() is False

    def test_has_contracts_with_api(self):
        cs = ContractSet(api_contracts=[_sample_api_contract()])
        assert cs.has_contracts() is True

    def test_has_contracts_with_type(self):
        cs = ContractSet(type_contracts=[_sample_type_contract()])
        assert cs.has_contracts() is True

    def test_contracts_for_task_producer(self):
        cs = ContractSet(
            api_contracts=[_sample_api_contract()],
            type_contracts=[_sample_type_contract()],
        )
        tc = cs.contracts_for_task("task-1")
        assert len(tc.producing) == 1
        assert len(tc.consuming) == 0
        assert len(tc.types) == 1

    def test_contracts_for_task_consumer(self):
        cs = ContractSet(
            api_contracts=[_sample_api_contract()],
            type_contracts=[_sample_type_contract()],
        )
        tc = cs.contracts_for_task("task-2")
        assert len(tc.producing) == 0
        assert len(tc.consuming) == 1
        assert len(tc.types) == 1

    def test_contracts_for_task_unrelated(self):
        cs = ContractSet(
            api_contracts=[_sample_api_contract()],
            type_contracts=[_sample_type_contract()],
        )
        tc = cs.contracts_for_task("task-99")
        assert len(tc.producing) == 0
        assert len(tc.consuming) == 0
        assert len(tc.types) == 0

    def test_round_trip_json(self):
        cs = ContractSet(
            api_contracts=[_sample_api_contract()],
            type_contracts=[_sample_type_contract()],
            integration_hints=[
                IntegrationHint(
                    producer_task_id="task-1",
                    consumer_task_ids=["task-2"],
                    interface_type=ContractType.API_ENDPOINT,
                    description="Templates API",
                    endpoint_hints=["GET /api/templates"],
                ),
            ],
        )
        json_str = cs.model_dump_json()
        restored = ContractSet.model_validate_json(json_str)
        assert len(restored.api_contracts) == 1
        assert len(restored.type_contracts) == 1
        assert len(restored.integration_hints) == 1
        assert restored.api_contracts[0].path == "/api/templates"


# -- TaskContracts formatting tests ----------------------------------------


class TestTaskContractsFormatForAgent:
    def test_empty_returns_empty_string(self):
        tc = TaskContracts()
        assert tc.format_for_agent() == ""

    def test_producer_format(self):
        tc = TaskContracts(
            producing=[_sample_api_contract()],
            types=[_sample_type_contract()],
        )
        output = tc.format_for_agent()
        assert "## Interface Contracts" in output
        assert "### APIs You Are PRODUCING" in output
        assert "GET /api/templates" in output
        assert "builtin: Template[]" in output
        assert "### Shared Types" in output
        assert "**Template**" in output
        # Should not contain consumer section
        assert "### APIs You Are CONSUMING" not in output

    def test_consumer_format(self):
        tc = TaskContracts(
            consuming=[_sample_api_contract()],
        )
        output = tc.format_for_agent()
        assert "### APIs You Are CONSUMING" in output
        assert "Response shape:" in output
        # Should not contain producer section
        assert "### APIs You Are PRODUCING" not in output

    def test_request_body_shown(self):
        api = APIContract(
            id="c1", method="POST", path="/api/templates",
            description="Create template",
            request_body=[
                FieldSpec(name="name", type="string"),
                FieldSpec(name="icon", type="string", required=False),
            ],
            response_body=[FieldSpec(name="id", type="string")],
            producer_task_id="task-1",
            consumer_task_ids=["task-2"],
        )
        tc = TaskContracts(producing=[api])
        output = tc.format_for_agent()
        assert "Request body:" in output
        assert "name: string" in output
        assert "icon: string (optional)" in output

    def test_optional_fields_marked(self):
        tc = TaskContracts(types=[_sample_type_contract()])
        output = tc.format_for_agent()
        assert "icon: string (optional)" in output
        assert "id: string  // Unique ID" in output


class TestTaskContractsFormatForReviewer:
    def test_empty_returns_empty_string(self):
        tc = TaskContracts()
        assert tc.format_for_reviewer() == ""

    def test_producer_review(self):
        tc = TaskContracts(producing=[_sample_api_contract()])
        output = tc.format_for_reviewer()
        assert "## Contract Compliance Check" in output
        assert "Must return EXACTLY these fields" in output
        assert "`builtin`" in output
        assert "FAIL the review" in output

    def test_consumer_review(self):
        tc = TaskContracts(consuming=[_sample_api_contract()])
        output = tc.format_for_reviewer()
        assert "must expect EXACTLY these fields" in output
        assert "`user`" in output

    def test_types_only_no_review(self):
        """Types without producing/consuming APIs should return empty."""
        tc = TaskContracts(types=[_sample_type_contract()])
        assert tc.format_for_reviewer() == ""


# -- IntegrationHint tests ------------------------------------------------


class TestIntegrationHint:
    def test_basic_hint(self):
        hint = IntegrationHint(
            producer_task_id="task-1",
            consumer_task_ids=["task-2", "task-3"],
            interface_type=ContractType.API_ENDPOINT,
            description="Templates REST API",
            endpoint_hints=["GET /api/templates"],
        )
        assert hint.producer_task_id == "task-1"
        assert len(hint.consumer_task_ids) == 2
        assert hint.interface_type == ContractType.API_ENDPOINT

    def test_empty_endpoint_hints(self):
        hint = IntegrationHint(
            producer_task_id="task-1",
            consumer_task_ids=["task-2"],
            interface_type=ContractType.SHARED_TYPE,
            description="Shared data type",
        )
        assert hint.endpoint_hints == []
