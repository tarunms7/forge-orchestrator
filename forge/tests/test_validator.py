"""Tests for the structural validator."""

from __future__ import annotations

from forge.core.models import TaskDefinition, TaskGraph
from forge.core.planning.models import CodebaseMap
from forge.core.planning.validator import (
    _check_file_completeness,
    _check_file_ownership,
    validate_plan,
)


def _make_task(id: str, description: str, files: list[str], depends_on: list[str] | None = None) -> TaskDefinition:
    return TaskDefinition(id=id, title=f"Task {id}", description=description, files=files, depends_on=depends_on or [])


def _make_graph(*tasks: TaskDefinition) -> TaskGraph:
    return TaskGraph(tasks=list(tasks))


class TestFileCompleteness:
    def test_no_issue_when_files_match(self):
        task = _make_task(
            "task-1",
            "Modify src/api.py to add the new endpoint. Update src/models.py with the schema.",
            ["src/api.py", "src/models.py"],
        )
        issues = _check_file_completeness(_make_graph(task))
        assert len(issues) == 0

    def test_detects_missing_file(self):
        task = _make_task(
            "task-1",
            "Modify src/api.py to add the new endpoint. Update src/models.py with the schema.",
            ["src/api.py"],  # models.py is missing
        )
        issues = _check_file_completeness(_make_graph(task))
        assert len(issues) == 1
        assert issues[0].category == "missing_file_in_scope"
        assert "src/models.py" in issues[0].description
        assert issues[0].severity == "major"

    def test_detects_multiple_missing_files(self):
        task = _make_task(
            "task-1",
            "Add routes to src/api.py, models to src/models.py, and tests to tests/test_api.py",
            ["src/api.py"],  # both models.py and test_api.py missing
        )
        issues = _check_file_completeness(_make_graph(task))
        assert len(issues) == 2
        mentioned = {i.description for i in issues}
        assert any("src/models.py" in d for d in mentioned)
        assert any("tests/test_api.py" in d for d in mentioned)

    def test_no_false_positive_for_extensions_in_text(self):
        task = _make_task(
            "task-1",
            "This task creates a REST API endpoint that returns JSON data.",
            ["src/api.py"],
        )
        issues = _check_file_completeness(_make_graph(task))
        assert len(issues) == 0

    def test_normalizes_paths(self):
        task = _make_task(
            "task-1",
            "Modify ./src/api.py to add the endpoint.",
            ["src/api.py"],  # listed without ./ prefix
        )
        issues = _check_file_completeness(_make_graph(task))
        assert len(issues) == 0

    def test_validate_plan_includes_file_completeness(self):
        """Ensure _check_file_completeness is wired into validate_plan."""
        task = _make_task(
            "task-1",
            "Add update_comment and delete_comment to review_bot/server/api.py for queue management.",
            ["review_bot/server/queue.py", "tests/test_graceful_shutdown.py"],
        )
        codebase_map = CodebaseMap(architecture_summary="Test project")
        result = validate_plan(_make_graph(task), codebase_map)
        # Should fail because api.py is mentioned but not in files
        assert result.status == "fail"
        missing_file_issues = [i for i in result.issues if i.category == "missing_file_in_scope"]
        assert len(missing_file_issues) == 1
        assert "review_bot/server/api.py" in missing_file_issues[0].description


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
