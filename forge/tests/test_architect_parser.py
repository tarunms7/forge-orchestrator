"""Tests for the Architect's JSON parser — isolated from SDK dependencies.

We test _parse() logic directly by extracting it into a standalone function
rather than importing the full Architect class (which pulls in claude_code_sdk).
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError as PydanticValidationError

from forge.core.models import TaskGraph


def _parse(raw: str) -> tuple[TaskGraph | None, str | None]:
    """Mirror of Architect._parse() for testing without SDK imports."""
    raw = raw.strip()

    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if not blocks:
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


class TestArchitectParser:
    def test_single_json_block(self):
        raw = '```json\n{"conventions": {}, "tasks": [{"id": "task-1", "title": "Do X", "description": "Do X in detail enough to pass validation minimum.", "files": ["a.py"], "depends_on": [], "complexity": "low"}]}\n```'
        graph, error = _parse(raw)
        assert graph is not None
        assert error is None
        assert len(graph.tasks) == 1

    def test_two_json_blocks_takes_last_valid(self):
        """When the model produces two JSON blocks, take the last valid one (the refined version)."""
        block1 = '{"conventions": {}, "tasks": [{"id": "task-1", "title": "First", "description": "First attempt description that is long enough for validation.", "files": ["a.py"], "depends_on": [], "complexity": "low"}]}'
        block2 = '{"conventions": {}, "tasks": [{"id": "task-1", "title": "Refined", "description": "Refined second attempt description that is long enough for validation.", "files": ["a.py", "b.py"], "depends_on": [], "complexity": "medium"}]}'
        raw = f"Here is my plan:\n```json\n{block1}\n```\nLet me re-read and refine...\n```json\n{block2}\n```"
        graph, error = _parse(raw)
        assert graph is not None
        assert error is None
        assert graph.tasks[0].title == "Refined"
        assert len(graph.tasks[0].files) == 2

    def test_no_json_returns_error(self):
        raw = "I could not produce a plan because the request is unclear."
        graph, error = _parse(raw)
        assert graph is None
        assert error is not None

    def test_bare_json_no_code_fences(self):
        raw = '{"conventions": {}, "tasks": [{"id": "task-1", "title": "Do X", "description": "Description that meets the minimum length requirement for validation checks.", "files": ["a.py"], "depends_on": [], "complexity": "low"}]}'
        graph, error = _parse(raw)
        assert graph is not None
        assert len(graph.tasks) == 1

    def test_invalid_json_returns_error(self):
        raw = '```json\n{"conventions": {}, "tasks": [BROKEN}\n```'
        graph, error = _parse(raw)
        assert graph is None
        assert "Invalid JSON" in error

    def test_first_block_invalid_second_valid(self):
        """If the first block is garbage but the second is valid, use the second."""
        block_bad = '{"conventions": {}, "tasks": [INVALID STUFF}'
        block_good = '{"conventions": {}, "tasks": [{"id": "task-1", "title": "Good", "description": "A valid task description that passes the minimum length check easily.", "files": ["x.py"], "depends_on": [], "complexity": "low"}]}'
        raw = f"```json\n{block_bad}\n```\nFixed version:\n```json\n{block_good}\n```"
        graph, error = _parse(raw)
        assert graph is not None
        assert graph.tasks[0].title == "Good"

    def test_greedy_regex_bug_is_fixed(self):
        """Regression test: the old greedy regex would capture across both blocks, producing invalid JSON."""
        block1 = '{"conventions": {}, "tasks": [{"id": "task-1", "title": "A", "description": "First plan attempt with enough text to pass minimum length.", "files": ["a.py"], "depends_on": [], "complexity": "low"}]}'
        block2 = '{"conventions": {}, "tasks": [{"id": "task-1", "title": "B", "description": "Second plan attempt with enough text to pass minimum length.", "files": ["b.py"], "depends_on": [], "complexity": "low"}, {"id": "task-2", "title": "C", "description": "Third task with enough text to pass the minimum length checks.", "files": ["c.py"], "depends_on": [], "complexity": "low"}]}'
        raw = f"```json\n{block1}\n```\nRe-reading files...\n```json\n{block2}\n```"
        graph, error = _parse(raw)
        # Must succeed — the old greedy regex would fail here
        assert graph is not None
        assert len(graph.tasks) == 2
        assert graph.tasks[0].title == "B"
