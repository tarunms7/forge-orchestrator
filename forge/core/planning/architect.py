"""Architect stage: decomposes spec into a TaskGraph using CodebaseMap context."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from claude_code_sdk import ClaudeCodeOptions
from pydantic import ValidationError as PydanticValidationError

from forge.agents.adapter import _build_question_protocol
from forge.core.models import TaskGraph
from forge.core.planning.models import CodebaseMap, PlanFeedback
from forge.core.planning.prompts import build_architect_system_prompt
from forge.core.sdk_helpers import sdk_query
from forge.core.validator import validate_task_graph

logger = logging.getLogger("forge.planning.architect")

_QUESTION_PATTERN = re.compile(r"FORGE_QUESTION:\s*\n?\s*(\{.*?\})\s*$", re.DOTALL)


@dataclass
class ArchitectResult:
    task_graph: TaskGraph | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_id: str | None = None


class Architect:
    def __init__(self, model: str = "opus", cwd: str | None = None, max_retries: int = 3, autonomy: str = "balanced", question_limit: int = 3) -> None:
        self._model = model
        self._cwd = cwd
        self._max_retries = max_retries
        self._autonomy = autonomy
        self._question_limit = question_limit

    async def run(self, *, user_input: str, spec_text: str, codebase_map: CodebaseMap, conventions: str,
                  feedback: PlanFeedback | None = None, on_message: Callable | None = None, on_question: Callable | None = None) -> ArchitectResult:
        total_cost = 0.0
        total_input = 0
        total_output = 0
        questions_asked = 0
        resume_session: str | None = None
        val_feedback: str | None = None

        for attempt in range(self._max_retries):
            if resume_session:
                prompt = f"User answered: {val_feedback}\n\nNow produce the TaskGraph JSON."
            else:
                prompt = self._build_prompt(user_input, spec_text, codebase_map, conventions, feedback, val_feedback)

            question_protocol = _build_question_protocol(autonomy=self._autonomy, remaining=self._question_limit - questions_asked)
            system_prompt = build_architect_system_prompt(question_protocol)

            options = ClaudeCodeOptions(
                system_prompt=system_prompt, max_turns=20, model=self._model,
                allowed_tools=["Read", "Glob", "Grep", "Bash"], permission_mode="acceptEdits",
            )
            if self._cwd:
                options.cwd = self._cwd
            if resume_session:
                options.resume = resume_session

            try:
                result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
            except Exception as e:
                logger.warning("Architect SDK error: %s", e)
                val_feedback = f"SDK error: {e}"
                resume_session = None
                continue

            if not result:
                continue

            total_cost += result.cost_usd
            total_input += result.input_tokens
            total_output += result.output_tokens

            raw = result.result or ""

            # Check for FORGE_QUESTION
            q_match = _QUESTION_PATTERN.search(raw)
            if q_match and on_question and questions_asked < self._question_limit:
                try:
                    q_data = json.loads(q_match.group(1))
                except json.JSONDecodeError:
                    q_data = None
                if q_data and "question" in q_data:
                    questions_asked += 1
                    answer = await on_question(q_data)
                    if answer:
                        resume_session = result.session_id
                        val_feedback = answer
                        continue

            resume_session = None
            graph, error = self._parse(raw)
            if graph is not None:
                return ArchitectResult(task_graph=graph, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output, session_id=result.session_id)
            val_feedback = f"Invalid output: {error}"

        return ArchitectResult(task_graph=None, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output)

    def _build_prompt(self, user_input, spec_text, codebase_map, conventions, feedback, validation_feedback):
        parts = [f"User request: {user_input}"]
        if spec_text:
            parts.append(f"Spec document:\n{spec_text}")
        parts.append(f"CodebaseMap:\n{codebase_map.model_dump_json(indent=2)}")
        if conventions:
            parts.append(f"Project conventions:\n{conventions}")
        if feedback:
            parts.append(
                f"Re-plan feedback (iteration {feedback.iteration}/{feedback.max_iterations}):\n"
                f"Scope: {feedback.replan_scope}\n"
                f"Issues:\n" + "\n".join(f"- [{i.severity}] {i.description}" for i in feedback.issues)
            )
        if validation_feedback:
            parts.append(f"Previous attempt feedback: {validation_feedback}")
        parts.append("Produce ONLY the TaskGraph JSON.")
        return "\n\n".join(p for p in parts if p)

    def _parse(self, raw: str) -> tuple[TaskGraph | None, str | None]:
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
            graph = TaskGraph.model_validate(data)
        except PydanticValidationError as e:
            return None, f"Schema validation failed: {e}"
        try:
            validate_task_graph(graph)
        except Exception as e:
            return None, str(e)
        return graph, None
