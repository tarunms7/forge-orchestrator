"""Claude-backed planner. Uses claude-code-sdk to decompose tasks into TaskGraph JSON."""

import logging
import os
import re
from collections.abc import Callable

from claude_code_sdk import ClaudeCodeOptions

from forge.core.errors import SdkCallError
from forge.core.planner import PlannerLLM
from forge.core.sdk_helpers import SdkResult, sdk_query

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
            # Give the planner plenty of room for large tasks.  Complex
            # implementation plans may reference 15+ source files — the
            # planner needs enough turns to read them all before producing
            # JSON.  The anti-loop prompt rules prevent wasted turns.
            max_turns=30,
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
            logger.warning("SDK call failed during planning: %s", e)
            raise SdkCallError(f"SDK call failed: {e}", original_error=e) from e

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
        parts.append("Respond with ONLY the TaskGraph JSON. No markdown, no explanation. NEVER re-read a file you have already seen — if you catch yourself looping, output JSON immediately with what you have.")
        return "\n\n".join(parts)


def _extract_json(text: str) -> str:
    """Extract JSON from response, stripping markdown fences if present."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    start = text.find("{")
    if start == -1:
        return text
    # Use string-aware brace counter to find the matching closing brace.
    # This avoids the greedy rfind("}") which can include trailing garbage
    # when the response contains text after the JSON object.
    brace_depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                return text[start : i + 1]
    # Fallback: no balanced closing brace found — use rfind
    end = text.rfind("}")
    if end != -1:
        return text[start : end + 1]
    return text
