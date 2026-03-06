"""Claude-backed planner. Uses claude-code-sdk to decompose tasks into TaskGraph JSON."""

import logging
import os
import re
from collections.abc import Callable

from claude_code_sdk import ClaudeCodeOptions

from forge.core.planner import PlannerLLM
from forge.core.sdk_helpers import SdkResult, sdk_query

logger = logging.getLogger("forge.planner")

PLANNER_SYSTEM_PROMPT = """You are a task decomposition engine for a multi-agent coding system called Forge.

Given a user request and project context, produce a TaskGraph as valid JSON with this exact schema:

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
      "description": "What to do",
      "files": ["src/file.py"],
      "depends_on": [],
      "complexity": "low"
    }
  ]
}

The "conventions" object captures coding patterns observed in the existing codebase. Only include keys where you found clear evidence. Omit keys where the convention is unclear. These conventions will be forwarded to every coding agent so they write consistent code.

Rules:
- Each task must own specific files. No two tasks may own the same file.
- CROSS-TASK COUPLING: If task A creates a module (e.g. webhooks.py) but the integration point (e.g. registering the router in app.py) belongs to task B, you MUST handle this explicitly. Either: (1) Add the shared file to BOTH tasks' file lists so both can modify it, or (2) Make one task depend on the other and give the downstream task ownership of the integration file. NEVER leave a task unable to complete because its implementation requires modifying a file it doesn't own.
- When a file needs modifications from multiple tasks (e.g. an __init__.py that imports from several new modules), assign that file to the LAST task in the dependency chain, or include it in all relevant tasks' file lists.
- Use depends_on to express ordering (task-2 depends on task-1 if task-2 needs task-1's output).
- complexity is one of: "low", "medium", "high"
- Keep tasks focused: each task should do ONE thing well.
- Aim for 2-6 tasks. Only go higher for genuinely large features.
- MINIMIZE dependencies. Only add depends_on when a task genuinely needs another task's output files. Independent tasks should have empty depends_on so they run in parallel.
- Never make test tasks depend on implementation tasks — tests should be self-contained with mocks.
- If the user request mentions attached images (file paths), you MUST read them first with the Read tool before planning. Include the image paths in relevant task descriptions so agents can also read them.
- Output ONLY valid JSON. No markdown fences, no explanation, just the JSON object."""


class ClaudePlannerLLM(PlannerLLM):
    """Concrete planner that calls Claude via claude-code-sdk."""

    def __init__(self, model: str = "sonnet", cwd: str | None = None, system_prompt_modifier: str = "") -> None:
        self._model = model
        self._cwd = cwd
        self._system_prompt_modifier = system_prompt_modifier
        self._last_sdk_result: SdkResult | None = None

    async def generate_plan(
        self, user_input: str, context: str, feedback: str | None = None,
        on_message: Callable | None = None,
    ) -> str:
        prompt = self._build_prompt(user_input, context, feedback)

        system_prompt = PLANNER_SYSTEM_PROMPT
        if self._system_prompt_modifier:
            system_prompt += self._system_prompt_modifier

        options = ClaudeCodeOptions(
            system_prompt=system_prompt,
            # Give the planner enough turns to read project files before
            # producing JSON.  Opus typically needs 3-5 turns to explore
            # the codebase, then 1 turn to output the TaskGraph.
            max_turns=10,
            model=self._model,
            # Read-only tools: planner explores the codebase but must NOT
            # write files — its only output is the TaskGraph JSON in the
            # result text.  Without these settings the SDK defaults give
            # all tools + interactive permission mode, causing Write
            # attempts to hang (no terminal) and waste turns.
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
        )
        if self._cwd:
            options.cwd = self._cwd

        try:
            result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
        except Exception as e:
            # SDK failures (rate limits, timeouts, etc.) should be retried
            # by the Planner's retry loop, not crash the pipeline.
            logger.warning("SDK call failed during planning: %s", e)
            return ""  # Empty string → triggers Planner's validation retry

        self._last_sdk_result = result
        logger.info("SDK result type: %s", type(result).__name__ if result else "None")
        logger.info("SDK result.result: %s", (result.result[:300] if result.result else "<None>") if result else "<no result obj>")

        result_text = result.result if result and result.result else ""
        extracted = _extract_json(result_text)
        logger.info("Extracted JSON (%d chars): %s", len(extracted), extracted[:300] if extracted else "<empty>")
        return extracted

    def _build_prompt(
        self, user_input: str, context: str, feedback: str | None,
    ) -> str:
        parts = [f"User request: {user_input}"]
        if context:
            parts.append(f"Project context:\n{context}")

        # Inject existing conventions file if present
        conventions_path = os.path.join(self._cwd or ".", ".forge", "conventions.md")
        try:
            if os.path.isfile(conventions_path):
                with open(conventions_path) as f:
                    content = f.read().strip()
                if content:
                    parts.append(
                        f"Existing project conventions (from .forge/conventions.md):\n{content}"
                    )
        except OSError:
            pass  # File unreadable — skip silently

        if feedback:
            parts.append(f"Previous attempt feedback:\n{feedback}")
        parts.append("Respond with ONLY the TaskGraph JSON.")
        return "\n\n".join(parts)


def _extract_json(text: str) -> str:
    """Extract JSON from response, stripping markdown fences if present."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text
