"""Planner-stage conformance tests.

Each test verifies a behavioral contract that any provider must satisfy
when running in the *planner* (intelligence) execution stage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from forge.providers.base import (
    EventKind,
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.tests.conformance import ConformanceResult, ConformanceTest

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry


def _planner_tool_policy() -> ToolPolicy:
    """Planner allowlist: read-only tools only."""
    return ToolPolicy(
        mode="allowlist",
        allowed_tools=["read", "glob", "grep"],
    )


def _planner_contract() -> OutputContract:
    return OutputContract(
        format="json",
        json_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "files": {"type": "array", "items": {"type": "string"}},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "complexity": {"type": "string"},
                        },
                        "required": ["id", "title"],
                    },
                },
            },
            "required": ["tasks"],
        },
    )


class TestProducesValidTaskgraph(ConformanceTest):
    """Planning task produces valid JSON task graph."""

    stage = "planner"

    async def run(self, registry: ProviderRegistry) -> ConformanceResult:
        start = self._timer()
        spec = ModelSpec.parse(self.model) if self.model else ModelSpec.parse("sonnet")
        provider = registry.get_for_model(spec)
        entry = registry.get_catalog_entry(spec)

        events: list[ProviderEvent] = []

        handle = provider.start(
            prompt=(
                "Plan the implementation of a simple REST API with two endpoints: "
                "GET /health and POST /echo. Return a JSON task graph."
            ),
            system_prompt=(
                "You are a planning agent. Analyze the request and produce a JSON "
                "task graph with tasks, dependencies, and file assignments."
            ),
            catalog_entry=entry,
            execution_mode=ExecutionMode.INTELLIGENCE,
            tool_policy=_planner_tool_policy(),
            output_contract=_planner_contract(),
            workspace=WorkspaceRoots(primary_cwd="."),
            max_turns=5,
            on_event=lambda e: events.append(e),
        )
        result = await handle.result()

        if result.is_error:
            return self._fail(start, f"Planner execution error: {result.text[:200]}")

        # Attempt to parse JSON from output
        text = result.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            start_idx = text.find("{")
            end_idx = text.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                try:
                    parsed = json.loads(text[start_idx:end_idx])
                except json.JSONDecodeError:
                    return self._fail(start, "Output is not valid JSON")
            else:
                return self._fail(start, "No JSON object found in output")

        if "tasks" not in parsed:
            return self._fail(start, "JSON output missing 'tasks' key")
        if not isinstance(parsed["tasks"], list):
            return self._fail(start, "'tasks' is not a list")
        if not parsed["tasks"]:
            return self._fail(start, "'tasks' list is empty")

        # Validate each task has at least an id and title
        for task in parsed["tasks"]:
            if "id" not in task:
                return self._fail(start, f"Task missing 'id': {task}")
            if "title" not in task:
                return self._fail(start, f"Task missing 'title': {task}")

        return self._pass(
            start,
            f"Valid task graph with {len(parsed['tasks'])} tasks",
        )


class TestReadesCodebase(ConformanceTest):
    """Planner uses Read/Glob/Grep tools to explore the codebase."""

    stage = "planner"

    async def run(self, registry: ProviderRegistry) -> ConformanceResult:
        start = self._timer()
        spec = ModelSpec.parse(self.model) if self.model else ModelSpec.parse("sonnet")
        provider = registry.get_for_model(spec)
        entry = registry.get_catalog_entry(spec)

        events: list[ProviderEvent] = []

        handle = provider.start(
            prompt=(
                "Plan the implementation of a new feature: add rate limiting to "
                "all API endpoints. First explore the existing codebase to understand "
                "the current API structure, then produce a task plan."
            ),
            system_prompt=(
                "You are a planning agent. Use Read, Glob, and Grep tools to explore "
                "the codebase before producing your plan. You MUST read files first."
            ),
            catalog_entry=entry,
            execution_mode=ExecutionMode.INTELLIGENCE,
            tool_policy=_planner_tool_policy(),
            output_contract=_planner_contract(),
            workspace=WorkspaceRoots(primary_cwd="."),
            max_turns=10,
            on_event=lambda e: events.append(e),
        )
        await handle.result()

        read_tools = {"read", "glob", "grep"}
        tool_calls = [
            e
            for e in events
            if e.kind == EventKind.TOOL_USE and e.tool_name in read_tools
        ]
        if not tool_calls:
            return self._fail(start, "Planner did not use any Read/Glob/Grep tools")
        tools_used = sorted({e.tool_name for e in tool_calls})
        return self._pass(
            start,
            f"Planner used codebase tools: {', '.join(tools_used)}",
            events=[{"kind": e.kind.value, "tool": e.tool_name} for e in tool_calls],
        )


class TestRespectsToolAllowlist(ConformanceTest):
    """Planner does not call Edit/Write tools (read-only allowlist)."""

    stage = "planner"

    async def run(self, registry: ProviderRegistry) -> ConformanceResult:
        start = self._timer()
        spec = ModelSpec.parse(self.model) if self.model else ModelSpec.parse("sonnet")
        provider = registry.get_for_model(spec)
        entry = registry.get_catalog_entry(spec)

        events: list[ProviderEvent] = []

        handle = provider.start(
            prompt=(
                "Plan how to refactor the authentication module. "
                "Explore the code and produce a task plan."
            ),
            system_prompt="You are a planning agent. Only use read-only tools.",
            catalog_entry=entry,
            execution_mode=ExecutionMode.INTELLIGENCE,
            tool_policy=_planner_tool_policy(),
            output_contract=_planner_contract(),
            workspace=WorkspaceRoots(primary_cwd="."),
            max_turns=5,
            on_event=lambda e: events.append(e),
        )
        await handle.result()

        # Check for forbidden tool calls
        forbidden_tools = {"edit", "write", "bash"}
        violations = [
            e
            for e in events
            if e.kind == EventKind.TOOL_USE and e.tool_name in forbidden_tools
        ]
        if violations:
            tool_names = [e.tool_name for e in violations]
            return self._fail(
                start,
                f"Planner used forbidden tools: {', '.join(tool_names)}",
            )
        return self._pass(start, "Planner respected read-only tool allowlist")


# ---------------------------------------------------------------------------
# Registry of all planner conformance tests
# ---------------------------------------------------------------------------

PLANNER_CONFORMANCE_TESTS: list[type[ConformanceTest]] = [
    TestProducesValidTaskgraph,
    TestReadesCodebase,
    TestRespectsToolAllowlist,
]
