"""Claude-backed planner. Uses claude-code-sdk to decompose tasks into TaskGraph JSON."""

import re

from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query

from forge.core.planner import PlannerLLM

PLANNER_SYSTEM_PROMPT = """You are a task decomposition engine for a multi-agent coding system called Forge.

Given a user request and project context, produce a TaskGraph as valid JSON with this exact schema:

{
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

Rules:
- Each task must own specific files. No two tasks may own the same file.
- Use depends_on to express ordering (task-2 depends on task-1 if task-2 needs task-1's output).
- complexity is one of: "low", "medium", "high"
- Keep tasks focused: each task should do ONE thing well.
- Aim for 2-6 tasks. Only go higher for genuinely large features.
- Output ONLY valid JSON. No markdown fences, no explanation, just the JSON object."""


class ClaudePlannerLLM(PlannerLLM):
    """Concrete planner that calls Claude via claude-code-sdk."""

    def __init__(self, model: str = "sonnet", cwd: str | None = None) -> None:
        self._model = model
        self._cwd = cwd

    async def generate_plan(
        self, user_input: str, context: str, feedback: str | None = None,
    ) -> str:
        prompt = self._build_prompt(user_input, context, feedback)

        options = ClaudeCodeOptions(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            max_turns=1,
            model=self._model,
        )
        if self._cwd:
            options.cwd = self._cwd

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                if message.result:
                    result_text = message.result
                break

        return _extract_json(result_text)

    def _build_prompt(
        self, user_input: str, context: str, feedback: str | None,
    ) -> str:
        parts = [f"User request: {user_input}"]
        if context:
            parts.append(f"Project context:\n{context}")
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
