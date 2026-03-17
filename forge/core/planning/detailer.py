"""Detailer stage: enriches rough task descriptions with implementation-ready detail."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from claude_code_sdk import ClaudeCodeOptions

from forge.core.models import TaskDefinition
from forge.core.planning.models import CodebaseMap
from forge.core.planning.prompts import DETAILER_SYSTEM_PROMPT
from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.planning.detailer")


@dataclass
class DetailerResult:
    task_id: str
    enriched_description: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    success: bool


class Detailer:
    def __init__(self, model: str = "sonnet", cwd: str | None = None, max_retries: int = 2) -> None:
        self._model = model
        self._cwd = cwd
        self._max_retries = max_retries

    async def run(self, *, task: TaskDefinition, codebase_map: CodebaseMap, conventions: str, on_message: Callable | None = None) -> DetailerResult:
        total_cost = 0.0
        total_input = 0
        total_output = 0
        sliced_map = codebase_map.slice_for_files(task.files)

        for attempt in range(self._max_retries):
            prompt = self._build_prompt(task, sliced_map, conventions)
            options = ClaudeCodeOptions(
                system_prompt=DETAILER_SYSTEM_PROMPT,
                max_turns=3, model=self._model,
                disallowed_tools=["Bash", "Glob", "Grep", "Task", "Edit", "Write"],
                permission_mode="acceptEdits",
            )
            if self._cwd:
                options.cwd = self._cwd

            try:
                result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
            except Exception as e:
                logger.warning("Detailer SDK error for %s: %s", task.id, e)
                continue

            if result:
                total_cost += result.cost_usd
                total_input += result.input_tokens
                total_output += result.output_tokens
                text = (result.result or "").strip()
                if text and len(text) > len(task.description):
                    return DetailerResult(task_id=task.id, enriched_description=text, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output, success=True)

        return DetailerResult(task_id=task.id, enriched_description=task.description, cost_usd=total_cost, input_tokens=total_input, output_tokens=total_output, success=False)

    def _build_prompt(self, task: TaskDefinition, sliced_map: CodebaseMap, conventions: str) -> str:
        parts = [
            f"Task: {task.title}",
            f"Current description: {task.description}",
            f"Files to modify: {', '.join(task.files)}",
            f"Complexity: {task.complexity}",
            f"\nRelevant codebase context:\n{sliced_map.model_dump_json(indent=2)}",
        ]
        if conventions:
            parts.append(f"\nProject conventions:\n{conventions}")
        parts.append(
            "\nEnrich this task with focused implementation notes. "
            "Keep scope limited to the listed files and existing task intent. "
            "Do not add new audit items, new risks, or unrelated refactors."
        )
        return "\n".join(parts)


class DetailerFactory:
    def __init__(self, model: str = "sonnet", cwd: str | None = None, max_concurrent: int = 4) -> None:
        self._model = model
        self._cwd = cwd
        self._max_concurrent = max_concurrent

    async def run_all(self, *, tasks: list[TaskDefinition], codebase_map: CodebaseMap, conventions: str, on_message: Callable | None = None) -> list[DetailerResult]:
        sem = asyncio.Semaphore(self._max_concurrent)
        detailer = Detailer(model=self._model, cwd=self._cwd)

        async def run_one(task: TaskDefinition) -> DetailerResult:
            async with sem:
                return await detailer.run(task=task, codebase_map=codebase_map, conventions=conventions, on_message=on_message)

        results = await asyncio.gather(*[run_one(t) for t in tasks])
        return list(results)
