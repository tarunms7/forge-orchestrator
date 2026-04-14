"""Contract models for cross-task interface alignment."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ContractType(str, Enum):
    """Types of cross-task contracts."""

    API_ENDPOINT = "api_endpoint"
    SHARED_TYPE = "shared_type"
    EVENT = "event"
    FILE_IMPORT = "file_import"


class FieldSpec(BaseModel):
    """A single field in a type contract."""

    name: str
    type: str  # e.g., "string", "number", "boolean", "Template[]"
    required: bool = True
    description: str = ""


class TypeContract(BaseModel):
    """Contract for a shared data structure used across tasks."""

    name: str  # e.g., "PipelineTemplate", "ReviewConfig"
    description: str = ""
    field_specs: list[FieldSpec]
    used_by_tasks: list[str]  # task IDs that reference this type


class APIContract(BaseModel):
    """Contract for a single API endpoint."""

    id: str  # e.g., "contract-api-1"
    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str  # e.g., "/api/templates"
    description: str = ""
    request_body: list[FieldSpec] | None = None
    response_body: list[FieldSpec]
    response_example: str = ""  # JSON example string for clarity
    auth_required: bool = True
    producer_task_id: str  # task that implements this endpoint
    consumer_task_ids: list[str]  # tasks that call this endpoint


class IntegrationHint(BaseModel):
    """A cross-task interface flagged by the planner.

    The planner doesn't define contracts — it just flags where they exist.
    The Contract Builder uses these hints to generate precise contracts.
    """

    producer_task_id: str  # task that creates/defines the interface
    consumer_task_ids: list[str]  # tasks that consume the interface
    interface_type: str  # e.g., "api_endpoint", "shared_type", "event", "file_import"
    description: str  # e.g., "REST API for template CRUD"
    # Optional hints from planner to guide contract generation
    endpoint_hints: list[str] = Field(default_factory=list)
    # e.g., ["GET /api/templates", "POST /api/templates"]

    @property
    def contract_type(self) -> ContractType | None:
        """Convert interface_type string to ContractType enum, or None if unknown."""
        try:
            return ContractType(self.interface_type)
        except ValueError:
            return None


class ContractSet(BaseModel):
    """All contracts for a pipeline. Output of the Contract Builder."""

    api_contracts: list[APIContract] = Field(default_factory=list)
    type_contracts: list[TypeContract] = Field(default_factory=list)
    # Original hints (kept for traceability)
    integration_hints: list[IntegrationHint] = Field(default_factory=list)
    # True when contract generation failed and this is an empty fallback.
    # Agents should be warned to verify cross-task interfaces manually.
    degraded: bool = False
    # Human-readable reason for degradation (empty when not degraded).
    degraded_reason: str = ""

    def contracts_for_task(self, task_id: str) -> TaskContracts:
        """Get only the contracts relevant to a specific task."""
        producing_apis: list[APIContract] = []
        consuming_apis: list[APIContract] = []
        for c in self.api_contracts:
            if c.producer_task_id == task_id:
                producing_apis.append(c)
            if task_id in c.consumer_task_ids:
                consuming_apis.append(c)
        relevant_types = [t for t in self.type_contracts if task_id in t.used_by_tasks]
        return TaskContracts(
            producing=producing_apis,
            consuming=consuming_apis,
            types=relevant_types,
        )

    def has_contracts(self) -> bool:
        """Whether any contracts exist (used to decide if phase should run)."""
        return bool(self.api_contracts or self.type_contracts)

    def remap_task_ids(self, id_map: dict[str, str]) -> ContractSet:
        """Return a new ContractSet with task IDs remapped via *id_map*.

        Used after the execute() ID-prefix step so contracts reference the
        same prefixed IDs that agents and reviewers use at runtime.
        """
        new_apis = []
        for api in self.api_contracts:
            new_apis.append(
                api.model_copy(
                    update={
                        "producer_task_id": id_map.get(api.producer_task_id, api.producer_task_id),
                        "consumer_task_ids": [
                            id_map.get(cid, cid) for cid in api.consumer_task_ids
                        ],
                    }
                )
            )
        new_types = []
        for tc in self.type_contracts:
            new_types.append(
                tc.model_copy(
                    update={
                        "used_by_tasks": [id_map.get(tid, tid) for tid in tc.used_by_tasks],
                    }
                )
            )
        new_hints = []
        for hint in self.integration_hints:
            new_hints.append(
                hint.model_copy(
                    update={
                        "producer_task_id": id_map.get(
                            hint.producer_task_id, hint.producer_task_id
                        ),
                        "consumer_task_ids": [
                            id_map.get(cid, cid) for cid in hint.consumer_task_ids
                        ],
                    }
                )
            )
        return ContractSet(
            api_contracts=new_apis,
            type_contracts=new_types,
            integration_hints=new_hints,
        )


class TaskContracts(BaseModel):
    """Contracts relevant to a single task. Injected into agent prompt."""

    producing: list[APIContract] = Field(default_factory=list)
    consuming: list[APIContract] = Field(default_factory=list)
    types: list[TypeContract] = Field(default_factory=list)

    def format_for_agent(self) -> str:
        """Format contracts as a system prompt section for the agent."""
        if not self.producing and not self.consuming and not self.types:
            return ""

        parts: list[str] = [
            "## Interface Contracts (STRICT — you MUST implement these exactly)",
            "",
            "Other tasks in this pipeline depend on these interfaces being exact.",
            "Do NOT deviate from the specified field names, types, or response shapes.",
            "",
        ]

        if self.types:
            parts.append("### Shared Types")
            parts.append("")
            for t in self.types:
                parts.append(f"**{t.name}**: {t.description}")
                parts.append("```")
                for f in t.field_specs:
                    req = "" if f.required else " (optional)"
                    parts.append(
                        f"  {f.name}: {f.type}{req}  // {f.description}"
                        if f.description
                        else f"  {f.name}: {f.type}{req}"
                    )
                parts.append("```")
                parts.append("")

        if self.producing:
            parts.append("### APIs You Are PRODUCING (other tasks depend on these exact shapes)")
            parts.append("")
            for api in self.producing:
                parts.append(f"**{api.method} {api.path}** — {api.description}")
                if api.request_body:
                    parts.append("Request body:")
                    parts.append("```")
                    for f in api.request_body:
                        req = "" if f.required else " (optional)"
                        parts.append(f"  {f.name}: {f.type}{req}")
                    parts.append("```")
                parts.append("Response:")
                parts.append("```")
                for f in api.response_body:
                    req = "" if f.required else " (optional)"
                    parts.append(f"  {f.name}: {f.type}{req}")
                parts.append("```")
                if api.response_example:
                    parts.append(f"Example: `{api.response_example}`")
                parts.append("")

        if self.consuming:
            parts.append("### APIs You Are CONSUMING (use these exact shapes in your code)")
            parts.append("")
            for api in self.consuming:
                parts.append(f"**{api.method} {api.path}** — {api.description}")
                if api.request_body:
                    parts.append("Request body:")
                    parts.append("```")
                    for f in api.request_body:
                        req = "" if f.required else " (optional)"
                        parts.append(f"  {f.name}: {f.type}{req}")
                    parts.append("```")
                parts.append("Response shape:")
                parts.append("```")
                for f in api.response_body:
                    req = "" if f.required else " (optional)"
                    parts.append(f"  {f.name}: {f.type}{req}")
                parts.append("```")
                if api.response_example:
                    parts.append(f"Example: `{api.response_example}`")
                parts.append("")

        return "\n".join(parts)

    def format_for_reviewer(self) -> str:
        """Format contracts as review criteria for the L2 reviewer."""
        if not self.producing and not self.consuming:
            return ""

        parts: list[str] = [
            "## Contract Compliance Check",
            "",
            "This task has interface contracts with other tasks. "
            "VERIFY that the implementation matches these contracts EXACTLY.",
            "",
        ]

        for api in self.producing:
            parts.append(
                f"- **{api.method} {api.path}**: Must return EXACTLY these fields: "
                + ", ".join(f"`{f.name}` ({f.type})" for f in api.response_body)
            )

        for api in self.consuming:
            parts.append(
                f"- Code calling **{api.method} {api.path}** must expect EXACTLY these fields: "
                + ", ".join(f"`{f.name}` ({f.type})" for f in api.response_body)
            )

        parts.append("")
        parts.append(
            "If any field names, types, or response shapes don't match the contract, FAIL the review."
        )

        return "\n".join(parts)
