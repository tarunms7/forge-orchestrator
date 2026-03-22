"""Tests for the structural validator."""

from __future__ import annotations

from forge.core.models import TaskDefinition, TaskGraph
from forge.core.planning.models import CodebaseMap
from forge.core.planning.validator import (
    _check_file_ownership,
    validate_plan,
)


def _make_task(
    id: str, description: str, files: list[str], depends_on: list[str] | None = None
) -> TaskDefinition:
    return TaskDefinition(
        id=id, title=f"Task {id}", description=description, files=files, depends_on=depends_on or []
    )


def _make_graph(*tasks: TaskDefinition) -> TaskGraph:
    return TaskGraph(tasks=list(tasks))


class TestFileOwnership:
    def test_no_issue_for_independent_files(self):
        t1 = _make_task("task-1", "Modify src/api.py to add endpoint.", ["src/api.py"])
        t2 = _make_task("task-2", "Modify src/models.py to add schema.", ["src/models.py"])
        issues = _check_file_ownership(_make_graph(t1, t2))
        assert len(issues) == 0

    def test_detects_conflict_between_independent_tasks(self):
        t1 = _make_task("task-1", "Modify src/api.py.", ["src/api.py"])
        t2 = _make_task("task-2", "Also modify src/api.py.", ["src/api.py"])
        issues = _check_file_ownership(_make_graph(t1, t2))
        assert len(issues) == 1
        assert issues[0].category == "file_conflict"

    def test_no_conflict_when_dependency_exists(self):
        t1 = _make_task("task-1", "Create src/api.py.", ["src/api.py"])
        t2 = _make_task("task-2", "Extend src/api.py.", ["src/api.py"], depends_on=["task-1"])
        issues = _check_file_ownership(_make_graph(t1, t2))
        assert len(issues) == 0


class TestValidatePlan:
    def test_passes_clean_plan(self):
        t1 = _make_task(
            "task-1", "Rewrite README.md with new features and simplified structure.", ["README.md"]
        )
        t2 = _make_task(
            "task-2",
            "Update pyproject.toml description field to be simpler and clearer.",
            ["pyproject.toml"],
        )
        codebase_map = CodebaseMap(architecture_summary="Test project")
        result = validate_plan(_make_graph(t1, t2), codebase_map)
        assert result.status == "pass"

    def test_description_referencing_other_files_for_context_does_not_fail(self):
        """Descriptions that mention files for context (not modification) should not trigger failures."""
        task = _make_task(
            "task-1",
            "Rewrite README.md to document features. The review pipeline is in review_bot/review/orchestrator.py "
            "and notifications are in review_bot/notifications/slack.py. Describe these features accurately.",
            ["README.md"],
        )
        codebase_map = CodebaseMap(architecture_summary="Test project")
        result = validate_plan(_make_graph(task), codebase_map)
        assert result.status == "pass"
