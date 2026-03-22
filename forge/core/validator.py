"""TaskGraph validation. Enforces structural correctness before any work begins."""

from forge.core.errors import CyclicDependencyError, FileConflictError, ValidationError
from forge.core.models import TaskGraph


def validate_task_graph(graph: TaskGraph) -> None:
    """Validate a TaskGraph. Raises on first error found."""
    _check_duplicate_ids(graph)
    _check_dependency_refs(graph)
    _check_cycles(graph)
    _check_file_conflicts(graph)
    _check_integration_hints(graph)


def _check_duplicate_ids(graph: TaskGraph) -> None:
    seen: set[str] = set()
    for task in graph.tasks:
        if task.id in seen:
            raise ValidationError(f"Duplicate task id: '{task.id}'")
        seen.add(task.id)


def _check_dependency_refs(graph: TaskGraph) -> None:
    valid_ids = {t.id for t in graph.tasks}
    for task in graph.tasks:
        for dep in task.depends_on:
            if dep not in valid_ids:
                raise ValidationError(f"Task '{task.id}' depends on unknown task '{dep}'")


def _check_cycles(graph: TaskGraph) -> None:
    adjacency = {t.id: list(t.depends_on) for t in graph.tasks}
    visited: set[str] = set()
    in_stack: set[str] = set()
    stack_path: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        stack_path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor in in_stack:
                cycle_start = stack_path.index(neighbor)
                cycle = stack_path[cycle_start:] + [neighbor]
                raise CyclicDependencyError(cycle=cycle)
            if neighbor not in visited:
                dfs(neighbor)
        stack_path.pop()
        in_stack.remove(node)

    for task_id in adjacency:
        if task_id not in visited:
            dfs(task_id)


def _check_file_conflicts(graph: TaskGraph) -> None:
    file_owners: dict[str, str] = {}
    for task in graph.tasks:
        for file_path in task.files:
            if file_path in file_owners:
                raise FileConflictError(
                    file_path=file_path,
                    task_a=file_owners[file_path],
                    task_b=task.id,
                )
            file_owners[file_path] = task.id


def _check_integration_hints(graph: TaskGraph) -> None:
    """Validate integration hints reference existing task IDs (optional check)."""
    if not graph.integration_hints:
        return
    valid_ids = {t.id for t in graph.tasks}
    for hint in graph.integration_hints:
        if not isinstance(hint, dict):
            continue
        producer = hint.get("producer_task_id")
        if producer and producer not in valid_ids:
            raise ValidationError(
                f"Integration hint references unknown producer task: '{producer}'"
            )
        for consumer in hint.get("consumer_task_ids", []):
            if consumer not in valid_ids:
                raise ValidationError(
                    f"Integration hint references unknown consumer task: '{consumer}'"
                )
