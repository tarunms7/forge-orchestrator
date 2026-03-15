"""Scout stage: deep codebase exploration producing a CodebaseMap."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from claude_code_sdk import ClaudeCodeOptions
from pydantic import ValidationError as PydanticValidationError

from forge.core.planning.models import CodebaseMap
from forge.core.planning.prompts import SCOUT_SYSTEM_PROMPT
from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.planning.scout")


@dataclass
class ScoutResult:
    """Output of the Scout stage."""
    codebase_map: CodebaseMap | None
    cost_usd: float
    input_tokens: int
    output_tokens: int


class Scout:
    """Explores the codebase and produces a structured CodebaseMap."""

    def __init__(self, model: str = "sonnet", cwd: str | None = None, max_retries: int = 3) -> None:
        self._model = model
        self._cwd = cwd
        self._max_retries = max_retries

    async def run(self, *, user_input: str, spec_text: str, snapshot_text: str, on_message: Callable | None = None) -> ScoutResult:
        total_cost = 0.0
        total_input = 0
        total_output = 0
        feedback: str | None = None

        for attempt in range(self._max_retries):
            logger.info("Scout attempt %d/%d", attempt + 1, self._max_retries)
            prompt = self._build_prompt(user_input, spec_text, snapshot_text, feedback)
            options = ClaudeCodeOptions(
                system_prompt=SCOUT_SYSTEM_PROMPT,
                max_turns=30, model=self._model,
                allowed_tools=["Read", "Glob", "Grep", "Bash"],
                permission_mode="acceptEdits",
            )
            if self._cwd:
                options.cwd = self._cwd

            try:
                result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
            except Exception as e:
                logger.warning("Scout SDK error on attempt %d: %s", attempt + 1, e)
                feedback = f"SDK error: {e}"
                continue

            if result:
                total_cost += result.cost_usd
                total_input += result.input_tokens
                total_output += result.output_tokens
                raw = result.result or ""
                codebase_map, error = self._parse(raw)
                if codebase_map is not None:
                    return ScoutResult(codebase_map=codebase_map, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output)
                feedback = f"Invalid output: {error}"
                logger.warning("Scout attempt %d parse failed: %s", attempt + 1, error)

        return ScoutResult(codebase_map=None, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output)

    def _build_prompt(self, user_input: str, spec_text: str, snapshot_text: str, feedback: str | None) -> str:
        parts = [f"User request: {user_input}"]
        if spec_text:
            parts.append(f"Spec document:\n{spec_text}")
        parts.append(f"Project snapshot:\n{snapshot_text}")
        if feedback:
            parts.append(f"Previous attempt feedback: {feedback}")
        parts.append("Explore the codebase and produce ONLY the CodebaseMap JSON.")
        return "\n\n".join(parts)

    def _parse(self, raw: str) -> tuple[CodebaseMap | None, str | None]:
        raw = raw.strip()
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        if match:
            raw = match.group(1)
        else:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"
        try:
            return CodebaseMap.model_validate(data), None
        except PydanticValidationError as e:
            return None, f"Schema validation failed: {e}"
