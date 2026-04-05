"""Claude-backed planner. Uses provider protocol to decompose tasks into TaskGraph JSON."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from forge.core.errors import SdkCallError
from forge.core.planner import PlannerLLM
from forge.core.sanitize import extract_json_block
from forge.providers import (
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ProviderResult,
    WorkspaceRoots,
)
from forge.providers.restrictions import PLANNER_TOOL_POLICY

if TYPE_CHECKING:
    from forge.core.cost_registry import CostRegistry
    from forge.providers.registry import ProviderRegistry

logger = logging.getLogger("forge.planner")

PLANNER_SYSTEM_PROMPT = """You are a task decomposition engine for a multi-agent coding system called Forge.

Your job: read the codebase, understand the request, and produce a TaskGraph as valid JSON.

## Output Schema

{
  "conventions": {
    "styling": "...",
    "state_management": "...",
    "component_patterns": "...",
    "naming": "...",
    "testing": "...",
    "imports": "...",
    "error_handling": "...",
    "other": "..."
  },
  "tasks": [
    {
      "id": "task-1",
      "title": "Short title",
      "description": "Detailed description of what to implement, including specific functions, classes, and behavior",
      "files": ["src/file.py"],
      "depends_on": [],
      "complexity": "low"
    }
  ],
  "integration_hints": [
    {
      "producer_task_id": "task-1",
      "consumer_task_ids": ["task-3", "task-4"],
      "interface_type": "api_endpoint",
      "description": "REST API for template CRUD",
      "endpoint_hints": ["GET /api/templates", "POST /api/templates"]
    }
  ]
}

## How to Explore the Codebase

Follow this workflow — it prevents wasted turns:

1. **Start with structure**: Glob for key files (e.g. `**/__init__.py`, `**/models.py`, `**/app.py`) to understand the project layout.
2. **Read the most relevant files**: Read the files directly related to the user's request. Read the implementation plan / spec document if one is referenced.
3. **Read dependencies**: Read files that the changed files import from or export to — you need to understand interfaces.
4. **Stop exploring when you can answer these questions**:
   - What files need to be created or modified?
   - What are the dependencies between changes?
   - What existing patterns should the agents follow?
5. **Produce the JSON**.

CRITICAL ANTI-LOOP RULES:
- NEVER re-read a file you have already seen. Track what you've read.
- NEVER glob or grep the same directory/pattern twice.
- If you notice you're about to read something you've already read, STOP and produce JSON immediately.

## Task Decomposition Rules

- Each task must own specific files. No two tasks may own the same file.
- CROSS-TASK COUPLING: If task A creates a module but the integration point (e.g. registering a router in app.py) belongs to task B, handle this explicitly. Either: (1) Add the shared file to BOTH tasks' file lists, or (2) Make one task depend on the other and give the downstream task ownership of the integration file. NEVER leave a task unable to complete because it needs to modify a file it doesn't own.
- When a file needs modifications from multiple tasks (e.g. __init__.py that imports from several new modules), assign it to the LAST task in the chain, or include it in all relevant tasks' file lists.
- Use depends_on for ordering — only when a task genuinely needs another task's output files.
- complexity: "low", "medium", or "high".
- Keep tasks focused: each task does ONE thing well.
- Aim for 2-6 tasks. Go higher only for genuinely large features.
- MINIMIZE dependencies. Independent tasks should have empty depends_on so they run in parallel.
- Never make test tasks depend on implementation tasks — tests should be self-contained with mocks.

## Task Descriptions — Be Specific

Each task description should be detailed enough that a coding agent can implement it without guessing. Include:
- What functions/classes to create or modify
- What the inputs and outputs should be
- What existing patterns to follow (reference specific files the agent should read)
- What tests to write and what they should cover
- Any edge cases or error handling to include

BAD: "Add rate limiting"
GOOD: "Create review_bot/github/rate_limits.py with a RateLimitTracker singleton class that parses X-RateLimit-* headers from GitHub API responses. Store per-resource (core, search, graphql) snapshots with remaining/limit/used/reset fields. Add update_from_response(url, headers) and snapshot() methods. Thread-safe via threading.Lock. Follow the pattern in review_bot/github/api.py for the class structure."

## Integration Hints

When tasks have cross-task interfaces (one produces an API/type/event another consumes), add integration_hints:
- producer_task_id: The task that CREATES the interface
- consumer_task_ids: Tasks that CONSUME it
- interface_type: "api_endpoint", "shared_type", "event", or "file_import"
- description: What the interface is for
- endpoint_hints: (api_endpoint only) e.g. "GET /api/templates"
Hints enable PARALLEL execution — the system generates contracts so both sides can work simultaneously. PREFER hints over depends_on for API integration. Omit if no cross-task interfaces exist.

## Conventions

The "conventions" object captures coding patterns you observe in the existing codebase. Only include keys where you found clear evidence. These conventions will be forwarded to every coding agent so they write consistent code.

## If the User References Images

Read them first with the Read tool before planning. Include image paths in relevant task descriptions.

## Output

Output ONLY valid JSON. No markdown fences, no explanation, no commentary. Just the JSON object."""


class ClaudePlannerLLM(PlannerLLM):
    """Concrete planner using provider protocol."""

    def __init__(
        self,
        model: str | ModelSpec = "sonnet",
        cwd: str | None = None,
        system_prompt_modifier: str = "",
        registry: ProviderRegistry | None = None,
        cost_registry: CostRegistry | None = None,
    ) -> None:
        self._model_spec = ModelSpec.parse(model) if isinstance(model, str) else model
        self._cwd = cwd
        self._system_prompt_modifier = system_prompt_modifier
        self._registry = registry
        self._cost_registry = cost_registry
        self._last_result: ProviderResult | None = None

    async def generate_plan(
        self,
        user_input: str,
        context: str,
        feedback: str | None = None,
        on_message: Callable | None = None,
    ) -> str:
        prompt = self._build_prompt(user_input, context, feedback)

        system_prompt = PLANNER_SYSTEM_PROMPT
        if self._system_prompt_modifier:
            system_prompt += self._system_prompt_modifier

        if self._registry is None:
            raise SdkCallError("ProviderRegistry not set on ClaudePlannerLLM")

        provider = self._registry.get_for_model(self._model_spec)
        catalog_entry = self._registry.get_catalog_entry(self._model_spec)

        workspace = WorkspaceRoots(primary_cwd=self._cwd or ".")

        # Bridge async on_message callback from sync on_event
        def _on_event(event: ProviderEvent) -> None:
            if on_message is not None:
                asyncio.ensure_future(on_message(event))

        try:
            handle = provider.start(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=PLANNER_TOOL_POLICY,
                output_contract=OutputContract(format="freeform"),
                workspace=workspace,
                max_turns=30,
                on_event=_on_event,
            )
            result = await handle.result()
        except Exception as e:
            logger.warning("Provider call failed during planning: %s", e)
            raise SdkCallError(f"Provider call failed: {e}", original_error=e) from e

        self._last_result = result
        logger.info("Provider result is_error: %s", result.is_error)
        logger.info(
            "Provider result text: %s",
            result.text[:300] if result.text else "<empty>",
        )

        result_text = result.text or ""
        extracted = extract_json_block(result_text) or result_text
        logger.info(
            "Extracted JSON (%d chars): %s",
            len(extracted),
            extracted[:300] if extracted else "<empty>",
        )
        return extracted

    def _build_prompt(
        self,
        user_input: str,
        context: str,
        feedback: str | None,
    ) -> str:
        parts = [f"User request: {user_input}"]
        if context:
            parts.append(f"Project context:\n{context}")

        # Inject existing conventions file if present
        conventions_path = os.path.join(self._cwd or ".", ".forge", "conventions.md")
        try:
            if os.path.isfile(conventions_path):
                with open(conventions_path, encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    parts.append(
                        f"Existing project conventions (from .forge/conventions.md):\n{content}"
                    )
        except OSError:
            pass  # File unreadable — skip silently

        if feedback:
            parts.append(f"Previous attempt feedback:\n{feedback}")
        parts.append(
            "Respond with ONLY the TaskGraph JSON. No markdown, no explanation. NEVER re-read a file you have already seen — if you catch yourself looping, output JSON immediately with what you have."
        )
        return "\n\n".join(parts)


def _extract_json(text: str) -> str:
    """Extract JSON from response, stripping markdown fences if present.

    .. deprecated:: Use :func:`forge.core.sanitize.extract_json_block` directly.
    """
    return extract_json_block(text) or text
