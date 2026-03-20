"""Tests for structural plan validator."""

import pytest
from forge.core.models import TaskDefinition, TaskGraph
from forge.core.planning.models import CodebaseMap, ValidationResult, ValidationIssue, MinorFix
from forge.core.planning.validator import validate_plan


def _graph(*tasks):
    return TaskGraph(tasks=list(tasks))

def _task(id, files, depends_on=None, description="Detailed task description with test requirements", repo="default"):
    return TaskDefinition(id=id, title=f"Task {id}", description=description, files=files, depends_on=depends_on or [], repo=repo)

def _empty_map():
    return CodebaseMap(architecture_summary="test", key_modules=[])


class TestFileOwnership:
    def test_independent_tasks_same_file_is_major(self):
        graph = _graph(_task("t1", ["shared.py"]), _task("t2", ["shared.py"]))
        result = validate_plan(graph, _empty_map())
        majors = [i for i in result.issues if i.severity == "major" and i.category == "file_conflict"]
        assert len(majors) == 1
        assert "shared.py" in majors[0].description

    def test_dependent_tasks_same_file_is_ok(self):
        graph = _graph(_task("t1", ["shared.py"]), _task("t2", ["shared.py"], depends_on=["t1"]))
        result = validate_plan(graph, _empty_map())
        conflicts = [i for i in result.issues if i.category == "file_conflict"]
        assert len(conflicts) == 0

    def test_transitive_dependency_same_file_is_ok(self):
        # t1 -> t2 -> t3, t1 and t3 share a file — t3 transitively depends on t1
        graph = _graph(
            _task("t1", ["shared.py"]),
            _task("t2", ["b.py"], depends_on=["t1"]),
            _task("t3", ["shared.py"], depends_on=["t2"]),
        )
        result = validate_plan(graph, _empty_map())
        conflicts = [i for i in result.issues if i.category == "file_conflict"]
        assert len(conflicts) == 0

    def test_multiple_independent_conflicts_reported(self):
        graph = _graph(
            _task("t1", ["a.py", "b.py"]),
            _task("t2", ["a.py"]),
            _task("t3", ["b.py"]),
        )
        result = validate_plan(graph, _empty_map())
        majors = [i for i in result.issues if i.category == "file_conflict"]
        assert len(majors) == 2


class TestDependencyValidity:
    def test_unknown_dependency_is_major(self):
        graph = _graph(_task("t1", ["a.py"], depends_on=["nonexistent"]))
        result = validate_plan(graph, _empty_map())
        majors = [i for i in result.issues if i.category == "invalid_dependency"]
        assert len(majors) == 1

    def test_cycle_is_fatal(self):
        graph = _graph(_task("t1", ["a.py"], depends_on=["t2"]), _task("t2", ["b.py"], depends_on=["t1"]))
        result = validate_plan(graph, _empty_map())
        fatals = [i for i in result.issues if i.severity == "fatal"]
        assert len(fatals) >= 1

    def test_valid_dependency_no_issue(self):
        graph = _graph(_task("t1", ["a.py"]), _task("t2", ["b.py"], depends_on=["t1"]))
        result = validate_plan(graph, _empty_map())
        dep_issues = [i for i in result.issues if i.category == "invalid_dependency"]
        assert len(dep_issues) == 0

    def test_three_way_cycle_is_fatal(self):
        graph = _graph(
            _task("t1", ["a.py"], depends_on=["t3"]),
            _task("t2", ["b.py"], depends_on=["t1"]),
            _task("t3", ["c.py"], depends_on=["t2"]),
        )
        result = validate_plan(graph, _empty_map())
        fatals = [i for i in result.issues if i.severity == "fatal"]
        assert len(fatals) >= 1


class TestTaskGranularity:
    def test_too_many_files_is_major(self):
        files = [f"file{i}.py" for i in range(12)]
        graph = _graph(_task("t1", files))
        result = validate_plan(graph, _empty_map())
        majors = [i for i in result.issues if i.category == "task_too_large"]
        assert len(majors) == 1

    def test_exactly_10_files_is_ok(self):
        files = [f"file{i}.py" for i in range(10)]
        graph = _graph(_task("t1", files))
        result = validate_plan(graph, _empty_map())
        majors = [i for i in result.issues if i.category == "task_too_large"]
        assert len(majors) == 0

    def test_vague_description_is_minor(self):
        graph = _graph(_task("t1", ["a.py"], description="Fix it"))
        result = validate_plan(graph, _empty_map())
        vague = [i for i in result.issues if i.category == "vague_description"]
        assert len(vague) == 1
        assert vague[0].severity == "minor"

    def test_sufficient_description_is_ok(self):
        description = "This is a sufficiently detailed description that exceeds 50 characters."
        graph = _graph(_task("t1", ["a.py"], description=description))
        result = validate_plan(graph, _empty_map())
        vague = [i for i in result.issues if i.category == "vague_description"]
        assert len(vague) == 0


class TestOverallStatus:
    def test_clean_graph_passes(self):
        graph = _graph(_task("t1", ["a.py"]), _task("t2", ["b.py"], depends_on=["t1"]))
        result = validate_plan(graph, _empty_map())
        assert result.status == "pass"

    def test_any_major_fails(self):
        graph = _graph(_task("t1", ["a.py"]), _task("t2", ["a.py"]))
        result = validate_plan(graph, _empty_map())
        assert result.status == "fail"

    def test_only_minor_issues_passes(self):
        graph = _graph(_task("t1", ["a.py"], description="Fix it"))
        result = validate_plan(graph, _empty_map())
        assert result.status == "pass"

    def test_fatal_issue_fails(self):
        graph = _graph(_task("t1", ["a.py"], depends_on=["t2"]), _task("t2", ["b.py"], depends_on=["t1"]))
        result = validate_plan(graph, _empty_map())
        assert result.status == "fail"

    def test_result_is_validation_result(self):
        graph = _graph(_task("t1", ["a.py"]))
        result = validate_plan(graph, _empty_map())
        assert isinstance(result, ValidationResult)


class TestRepoAssignments:
    def test_validate_plan_repo_ids_none_skips_repo_check(self):
        """Existing behavior unchanged when repo_ids is None."""
        graph = _graph(_task("t1", ["a.py"], repo="nonexistent"))
        result = validate_plan(graph, _empty_map(), repo_ids=None)
        repo_issues = [i for i in result.issues if i.category == "unknown_repo"]
        assert len(repo_issues) == 0

    def test_validate_plan_unknown_repo_detected(self):
        """Task with repo='unknown' when repo_ids={'backend','frontend'} → major issue."""
        graph = _graph(_task("t1", ["a.py"], repo="unknown"))
        result = validate_plan(graph, _empty_map(), repo_ids={"backend", "frontend"})
        majors = [i for i in result.issues if i.category == "unknown_repo"]
        assert len(majors) == 1
        assert majors[0].severity == "major"
        assert "unknown" in majors[0].description

    def test_validate_plan_valid_repos_pass(self):
        """All tasks have valid repos → pass."""
        graph = _graph(
            _task("t1", ["a.py"], repo="backend"),
            _task("t2", ["b.py"], repo="frontend"),
        )
        result = validate_plan(graph, _empty_map(), repo_ids={"backend", "frontend"})
        repo_issues = [i for i in result.issues if i.category == "unknown_repo"]
        assert len(repo_issues) == 0

    def test_validate_plan_default_repo_accepted(self):
        """Task with repo='default' when repo_ids={'default'} → pass."""
        graph = _graph(_task("t1", ["a.py"]))
        result = validate_plan(graph, _empty_map(), repo_ids={"default"})
        repo_issues = [i for i in result.issues if i.category == "unknown_repo"]
        assert len(repo_issues) == 0


class TestCrossRepoFilePaths:
    def test_cross_repo_file_path_absolute_rejected(self):
        """File path starting with '/' → major issue."""
        graph = _graph(_task("t1", ["/etc/config.py"]))
        result = validate_plan(graph, _empty_map(), repo_ids={"default"})
        issues = [i for i in result.issues if i.category == "absolute_path"]
        assert len(issues) == 1
        assert issues[0].severity == "major"

    def test_cross_repo_file_path_parent_traversal_rejected(self):
        """File path '../other/x.py' → major issue."""
        graph = _graph(_task("t1", ["../other/x.py"]))
        result = validate_plan(graph, _empty_map(), repo_ids={"default"})
        issues = [i for i in result.issues if i.category == "parent_traversal"]
        assert len(issues) == 1
        assert issues[0].severity == "major"

    def test_cross_repo_file_path_references_other_repo(self):
        """File 'backend/src/x.py' in frontend task → major issue."""
        graph = _graph(_task("t1", ["backend/src/x.py"], repo="frontend"))
        result = validate_plan(graph, _empty_map(), repo_ids={"backend", "frontend"})
        issues = [i for i in result.issues if i.category == "cross_repo_path"]
        assert len(issues) == 1
        assert issues[0].severity == "major"
        assert "backend" in issues[0].description

    def test_cross_repo_file_path_same_repo_prefix_ok(self):
        """File 'frontend/src/x.py' in frontend task → no cross_repo_path issue."""
        graph = _graph(_task("t1", ["frontend/src/x.py"], repo="frontend"))
        result = validate_plan(graph, _empty_map(), repo_ids={"backend", "frontend"})
        issues = [i for i in result.issues if i.category == "cross_repo_path"]
        assert len(issues) == 0


class TestFileOwnershipMultiRepo:
    def test_file_ownership_different_repos_no_conflict(self):
        """Two tasks in different repos can share same relative file path."""
        graph = _graph(
            _task("t1", ["src/main.py"], repo="backend"),
            _task("t2", ["src/main.py"], repo="frontend"),
        )
        result = validate_plan(graph, _empty_map())
        conflicts = [i for i in result.issues if i.category == "file_conflict"]
        assert len(conflicts) == 0

    def test_file_ownership_same_repo_still_conflicts(self):
        """Two independent tasks in same repo sharing a file still conflicts."""
        graph = _graph(
            _task("t1", ["src/main.py"], repo="backend"),
            _task("t2", ["src/main.py"], repo="backend"),
        )
        result = validate_plan(graph, _empty_map())
        conflicts = [i for i in result.issues if i.category == "file_conflict"]
        assert len(conflicts) == 1
