import pytest

from forge.core.errors import CyclicDependencyError, FileConflictError, ValidationError
from forge.core.models import Complexity, TaskDefinition, TaskGraph
from forge.core.validator import validate_task_graph


def _task(id: str, files: list[str], depends_on: list[str] | None = None) -> TaskDefinition:
    return TaskDefinition(
        id=id,
        title=f"Task {id}",
        description=f"Description for {id}",
        files=files,
        depends_on=depends_on or [],
        complexity=Complexity.LOW,
    )


class TestCycleDetection:
    def test_no_cycles_passes(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("b", ["b.py"], depends_on=["a"]),
            _task("c", ["c.py"], depends_on=["b"]),
        ])
        validate_task_graph(graph)

    def test_self_cycle_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["a"]),
        ])
        with pytest.raises(CyclicDependencyError) as exc_info:
            validate_task_graph(graph)
        assert "a" in exc_info.value.cycle

    def test_two_node_cycle_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["b"]),
            _task("b", ["b.py"], depends_on=["a"]),
        ])
        with pytest.raises(CyclicDependencyError):
            validate_task_graph(graph)

    def test_three_node_cycle_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["c"]),
            _task("b", ["b.py"], depends_on=["a"]),
            _task("c", ["c.py"], depends_on=["b"]),
        ])
        with pytest.raises(CyclicDependencyError):
            validate_task_graph(graph)

    def test_diamond_no_cycle(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("b", ["b.py"], depends_on=["a"]),
            _task("c", ["c.py"], depends_on=["a"]),
            _task("d", ["d.py"], depends_on=["b", "c"]),
        ])
        validate_task_graph(graph)


class TestFileConflictDetection:
    def test_no_conflicts_passes(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("b", ["b.py"]),
        ])
        validate_task_graph(graph)

    def test_same_file_two_tasks_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["shared.py"]),
            _task("b", ["shared.py"]),
        ])
        with pytest.raises(FileConflictError) as exc_info:
            validate_task_graph(graph)
        assert exc_info.value.file_path == "shared.py"

    def test_partial_overlap_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py", "shared.py"]),
            _task("b", ["b.py", "shared.py"]),
        ])
        with pytest.raises(FileConflictError):
            validate_task_graph(graph)


class TestDependencyRefValidation:
    def test_unknown_dependency_rejected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["nonexistent"]),
        ])
        with pytest.raises(ValidationError, match="nonexistent"):
            validate_task_graph(graph)

    def test_duplicate_task_ids_rejected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("a", ["b.py"]),
        ])
        with pytest.raises(ValidationError, match="Duplicate"):
            validate_task_graph(graph)
