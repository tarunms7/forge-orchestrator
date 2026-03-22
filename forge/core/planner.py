"""LLM planner. Decomposes user input into a validated TaskGraph."""

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

from pydantic import ValidationError as PydanticValidationError

from forge.core.errors import SdkCallError, ValidationError
from forge.core.models import TaskGraph
from forge.core.validator import validate_task_graph

logger = logging.getLogger("forge.planner")


class PlannerLLM(ABC):
    """Interface for the LLM that generates plans."""

    @abstractmethod
    async def generate_plan(
        self,
        user_input: str,
        context: str,
        feedback: str | None = None,
        on_message: Callable | None = None,
    ) -> str:
        """Generate a TaskGraph JSON string from user input."""


class Planner:
    """Orchestrates plan generation with validation and retry loop."""

    def __init__(self, llm: PlannerLLM, max_retries: int = 3) -> None:
        self._llm = llm
        self._max_retries = max_retries

    async def plan(
        self, user_input: str, context: str = "", on_message: Callable | None = None
    ) -> TaskGraph:
        feedback: str | None = None

        for attempt in range(self._max_retries):
            logger.info("Planning attempt %d/%d", attempt + 1, self._max_retries)
            try:
                raw = await self._llm.generate_plan(
                    user_input, context, feedback, on_message=on_message
                )
            except SdkCallError as e:
                logger.warning("Attempt %d/%d SDK error: %s", attempt + 1, self._max_retries, e)
                feedback = f"Previous attempt hit SDK error: {e}. Retrying."
                continue
            logger.info(
                "Attempt %d raw response (%d chars): %s",
                attempt + 1,
                len(raw),
                raw[:500] if raw else "<empty>",
            )
            graph, error = self._parse_and_validate(raw)
            if graph is not None:
                logger.info("Planning succeeded on attempt %d", attempt + 1)
                return graph
            logger.warning("Attempt %d validation failed: %s", attempt + 1, error)
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
