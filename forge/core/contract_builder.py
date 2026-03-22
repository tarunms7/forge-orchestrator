"""Contract Builder. Generates cross-task interface contracts from planner hints."""

import json
import logging
import re
from collections.abc import Callable

from claude_code_sdk import ClaudeCodeOptions

from forge.core.contracts import ContractSet, IntegrationHint
from forge.core.models import TaskGraph
from forge.core.sdk_helpers import SdkResult, sdk_query

logger = logging.getLogger("forge.contracts")


CONTRACT_BUILDER_SYSTEM_PROMPT = """You are an interface contract generator for a multi-agent coding system called Forge.

Given a task graph and integration hints, generate precise interface contracts that
multiple coding agents will build against simultaneously.

Your contracts must be EXACT — field names, types, and response shapes must be specific
enough that two independent developers could implement both sides and have them work
together on first try.

RULES:
- For each integration hint, generate one or more APIContract entries
- For shared data structures referenced by multiple contracts, generate TypeContract entries
- Use the existing codebase context to align with established patterns (e.g., if the
  project uses snake_case for API fields, use snake_case in contracts)
- If the hint references existing code (e.g., extending an existing API), READ the
  existing code first to ensure the contract is consistent
- response_example should be a realistic JSON string showing the response shape
- Each FieldSpec must have a clear type: use "string", "number", "boolean", "string[]",
  or reference a TypeContract name like "PipelineTemplate[]"
- Mark fields as required: true unless they are genuinely optional
- Include ALL fields in the response — don't omit fields the consumer will need

Output ONLY valid JSON matching this schema:

{
  "api_contracts": [
    {
      "id": "contract-api-1",
      "method": "GET",
      "path": "/api/templates",
      "description": "List all templates (built-in and user-created)",
      "request_body": null,
      "response_body": [
        { "name": "builtin", "type": "PipelineTemplate[]", "required": true, "description": "Built-in templates" },
        { "name": "user", "type": "PipelineTemplate[]", "required": true, "description": "User-created templates" }
      ],
      "response_example": "{\\"builtin\\": [{...}], \\"user\\": [{...}]}",
      "auth_required": true,
      "producer_task_id": "task-1",
      "consumer_task_ids": ["task-2"]
    }
  ],
  "type_contracts": [
    {
      "name": "PipelineTemplate",
      "description": "A pipeline configuration template",
      "field_specs": [
        { "name": "id", "type": "string", "required": true, "description": "Unique identifier (UUID for user, slug for built-in)" },
        { "name": "name", "type": "string", "required": true, "description": "" }
      ],
      "used_by_tasks": ["task-1", "task-2"]
    }
  ]
}

JSON only. No markdown fences, no explanation."""


class ContractBuilderLLM:
    """Generates interface contracts from planner integration hints."""

    def __init__(
        self,
        model: str = "sonnet",
        cwd: str | None = None,
    ) -> None:
        self._model = model
        self._cwd = cwd
        self._last_sdk_result: SdkResult | None = None

    async def generate_contracts(
        self,
        graph: TaskGraph,
        hints: list[IntegrationHint],
        project_context: str = "",
        on_message: Callable | None = None,
        feedback: str | None = None,
    ) -> str:
        """Generate a ContractSet JSON string from integration hints."""
        prompt = self._build_prompt(graph, hints, project_context, feedback=feedback)

        options = ClaudeCodeOptions(
            system_prompt=CONTRACT_BUILDER_SYSTEM_PROMPT,
            max_turns=10,  # Allow reading existing code for context
            model=self._model,
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
        )
        if self._cwd:
            options.cwd = self._cwd

        try:
            result = await sdk_query(
                prompt=prompt,
                options=options,
                on_message=on_message,
            )
        except Exception as e:
            logger.warning("SDK call failed during contract generation: %s", e)
            return ""

        self._last_sdk_result = result
        result_text = result.result if result and result.result else ""
        return _extract_json(result_text)

    def _build_prompt(
        self,
        graph: TaskGraph,
        hints: list[IntegrationHint],
        project_context: str,
        feedback: str | None = None,
    ) -> str:
        parts: list[str] = []

        # Task summaries so the builder understands what each task does
        parts.append("## Task Graph\n")
        for task in graph.tasks:
            parts.append(
                f"- **{task.id}** ({task.title}): {task.description}\n"
                f"  Files: {', '.join(task.files)}"
            )

        parts.append("\n## Integration Hints\n")
        for hint in hints:
            parts.append(
                f"- Producer: {hint.producer_task_id} → "
                f"Consumers: {', '.join(hint.consumer_task_ids)}\n"
                f"  Type: {hint.interface_type}\n"
                f"  Description: {hint.description}"
            )
            if hint.endpoint_hints:
                parts.append(f"  Endpoints: {', '.join(hint.endpoint_hints)}")

        if project_context:
            parts.append(f"\n## Project Context\n{project_context}")

        if feedback:
            parts.append(
                f"\n## Previous Attempt Failed\n"
                f"Your previous response had this validation error:\n{feedback}\n"
                f"Fix the issue and try again."
            )

        parts.append(
            "\nGenerate precise contracts for ALL integration hints above. "
            "Read existing code if needed to align with project patterns. "
            "Respond with ONLY the ContractSet JSON."
        )

        return "\n\n".join(parts)


class ContractBuilder:
    """Orchestrates contract generation with validation and retry."""

    def __init__(self, llm: ContractBuilderLLM, max_retries: int = 3) -> None:
        self._llm = llm
        self._max_retries = max_retries

    async def build(
        self,
        graph: TaskGraph,
        hints: list[IntegrationHint],
        project_context: str = "",
        on_message: Callable | None = None,
    ) -> ContractSet:
        """Generate and validate a ContractSet."""
        last_error: str | None = None
        for attempt in range(self._max_retries):
            logger.info("Contract generation attempt %d/%d", attempt + 1, self._max_retries)
            raw = await self._llm.generate_contracts(
                graph,
                hints,
                project_context,
                on_message,
                feedback=last_error,
            )
            contract_set, error = self._parse_and_validate(raw, graph)
            if contract_set is not None:
                logger.info("Contract generation succeeded on attempt %d", attempt + 1)
                return contract_set
            last_error = error
            logger.warning("Attempt %d validation failed: %s", attempt + 1, error)

        # If all retries fail, return empty ContractSet (graceful degradation)
        logger.warning(
            "Contract generation failed after %d retries — proceeding without contracts",
            self._max_retries,
        )
        return ContractSet()

    def _parse_and_validate(
        self,
        raw: str,
        graph: TaskGraph,
    ) -> tuple[ContractSet | None, str | None]:
        """Parse JSON and validate contract references."""
        if not raw or not raw.strip():
            return None, "Empty response"

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"

        try:
            contract_set = ContractSet.model_validate(data)
        except Exception as e:
            return None, f"Schema validation failed: {e}"

        # Validate that all referenced task IDs exist in the graph
        task_ids = {t.id for t in graph.tasks}
        for api in contract_set.api_contracts:
            if api.producer_task_id not in task_ids:
                return (
                    None,
                    f"API contract {api.id} references unknown producer task: {api.producer_task_id}",
                )
            for consumer_id in api.consumer_task_ids:
                if consumer_id not in task_ids:
                    return (
                        None,
                        f"API contract {api.id} references unknown consumer task: {consumer_id}",
                    )

        for type_contract in contract_set.type_contracts:
            for task_id in type_contract.used_by_tasks:
                if task_id not in task_ids:
                    return (
                        None,
                        f"Type contract {type_contract.name} references unknown task: {task_id}",
                    )

        return contract_set, None


def _extract_json(text: str) -> str:
    """Extract JSON from response, stripping markdown fences if present."""
    text = text.strip()
    # Use greedy .* so nested braces aren't truncated
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text
