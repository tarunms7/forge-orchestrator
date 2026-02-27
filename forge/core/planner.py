"""LLM planner. Decomposes user input into a validated TaskGraph."""

import json
from abc import ABC, abstractmethod

from pydantic import ValidationError as PydanticValidationError

from forge.core.errors import ValidationError
from forge.core.models import TaskGraph
from forge.core.validator import validate_task_graph


class PlannerLLM(ABC):
    """Interface for the LLM that generates plans."""

    @abstractmethod
    async def generate_plan(self, user_input: str, context: str, feedback: str | None = None) -> str:
        """Generate a TaskGraph JSON string from user input."""


class Planner:
    """Orchestrates plan generation with validation and retry loop."""

    def __init__(self, llm: PlannerLLM, max_retries: int = 3) -> None:
        self._llm = llm
        self._max_retries = max_retries

    async def plan(self, user_input: str, context: str = "") -> TaskGraph:
        feedback: str | None = None

        for attempt in range(self._max_retries):
            raw = await self._llm.generate_plan(user_input, context, feedback)
            graph, error = self._parse_and_validate(raw)
            if graph is not None:
                return graph
            feedback = f"Previous attempt failed: {error}. Please fix and try again."

        raise ValidationError(
            f"Planner failed to produce a valid TaskGraph after {self._max_retries} retries"
        )

    def _parse_and_validate(self, raw: str) -> tuple[TaskGraph | None, str | None]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"

        try:
            graph = TaskGraph.model_validate(data)
        except PydanticValidationError as e:
            return None, f"Schema validation failed: {e}"

        try:
            validate_task_graph(graph)
        except Exception as e:
            return None, str(e)

        return graph, None
