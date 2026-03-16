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
                disallowed_tools=["Bash", "Glob", "Grep", "Edit", "Write"],
                permission_mode="acceptEdits",
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
                logger.info("Architect succeeded on attempt %d (%d tasks)", attempt + 1, len(graph.tasks))
                return ArchitectResult(task_graph=graph, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output, session_id=result.session_id)
            val_feedback = f"Invalid output: {error}"
            logger.warning("Architect attempt %d parse failed: %s", attempt + 1, error)

        logger.error("Architect failed after %d retries", self._max_retries)
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

        # Find ALL fenced JSON blocks (non-greedy) and try each.
        # When the model produces multiple blocks, the last one is usually
        # the refined version, so we iterate in reverse.
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if not blocks:
            # Fallback: extract from first { to last }
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                blocks = [raw[start : end + 1]]

        last_error: str | None = None
        for candidate in reversed(blocks):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON: {e}"
                continue
            try:
                graph = TaskGraph.model_validate(data)
            except PydanticValidationError as e:
                last_error = f"Schema validation failed: {e}"
                continue
            # Basic structural checks (duplicate IDs, invalid dep refs).
            task_ids = {t.id for t in graph.tasks}
            seen: set[str] = set()
            valid = True
            for t in graph.tasks:
                if t.id in seen:
                    last_error = f"Duplicate task id: '{t.id}'"
                    valid = False
                    break
                seen.add(t.id)
                for dep in t.depends_on:
                    if dep not in task_ids:
                        last_error = f"Task '{t.id}' depends on unknown task '{dep}'"
                        valid = False
                        break
                if not valid:
                    break
            if valid:
                return graph, None

        return None, last_error or "No JSON found in output"
