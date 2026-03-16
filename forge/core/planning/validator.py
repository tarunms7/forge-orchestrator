"""Structural validation for task graphs produced by the planning pipeline."""

from __future__ import annotations

import re
from collections import deque

from forge.core.models import TaskGraph
from forge.core.planning.models import CodebaseMap, MinorFix, ValidationIssue, ValidationResult


_MAX_FILES_PER_TASK = 10
_MIN_DESCRIPTION_LENGTH = 50


def validate_plan(graph: TaskGraph, codebase_map: CodebaseMap, spec_text: str = "") -> ValidationResult:
    """Run all structural checks and return a ValidationResult.

    ``codebase_map`` and ``spec_text`` are accepted for future semantic
    checks (e.g. verifying plan files exist in the codebase) but are not
    used by the current structural checks.
    """
    issues: list[ValidationIssue] = []
    # Structural checks cannot auto-fix issues (that requires LLM reasoning).
    # The minor_fixes list is populated by the LLM validator stage downstream.
    minor_fixes: list[MinorFix] = []
    issues.extend(_check_file_ownership(graph))
    issues.extend(_check_dependency_validity(graph))
    issues.extend(_check_cycles(graph))
    issues.extend(_check_task_granularity(graph))
    issues.extend(_check_file_completeness(graph))
    has_major_or_fatal = any(i.severity in ("major", "fatal") for i in issues)
    status = "fail" if has_major_or_fatal else "pass"
    return ValidationResult(status=status, issues=issues, minor_fixes=minor_fixes)


def _build_transitive_deps(graph: TaskGraph) -> dict[str, set[str]]:
    """Build a mapping of task_id -> set of all transitive dependency task IDs (BFS)."""
    adjacency: dict[str, list[str]] = {t.id: list(t.depends_on) for t in graph.tasks}
    transitive: dict[str, set[str]] = {}

    for start_id in adjacency:
        visited: set[str] = set()
        queue: deque[str] = deque(adjacency.get(start_id, []))
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for dep in adjacency.get(node, []):
                if dep not in visited:
                    queue.append(dep)
        transitive[start_id] = visited

    return transitive


def _check_file_ownership(graph: TaskGraph) -> list[ValidationIssue]:
    """Detect when two independent tasks share the same file."""
    transitive_deps = _build_transitive_deps(graph)
    issues: list[ValidationIssue] = []

    # Build file -> list of owner task IDs
    file_owners: dict[str, list[str]] = {}
    for task in graph.tasks:
        for file_path in task.files:
            file_owners.setdefault(file_path, []).append(task.id)

    for file_path, owners in file_owners.items():
        if len(owners) < 2:
            continue
        # Check each pair of owners
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                task_a = owners[i]
                task_b = owners[j]
                # OK if either depends on the other transitively
                if task_b in transitive_deps.get(task_a, set()):
                    continue
                if task_a in transitive_deps.get(task_b, set()):
                    continue
                issues.append(
                    ValidationIssue(
                        severity="major",
                        category="file_conflict",
                        affected_tasks=[task_a, task_b],
                        description=(
                            f"File '{file_path}' is claimed by independent tasks '{task_a}' and '{task_b}'. "
                            "Add a dependency between them or split the file."
                        ),
                        suggested_fix=f"Make '{task_b}' depend on '{task_a}', or rename the conflicting file.",
                    )
                )

    return issues


def _check_dependency_validity(graph: TaskGraph) -> list[ValidationIssue]:
    """Check that all depends_on references point to existing task IDs."""
    valid_ids = {t.id for t in graph.tasks}
    issues: list[ValidationIssue] = []

    for task in graph.tasks:
        for dep in task.depends_on:
            if dep not in valid_ids:
                issues.append(
                    ValidationIssue(
                        severity="major",
                        category="invalid_dependency",
                        affected_tasks=[task.id],
                        description=(
                            f"Task '{task.id}' depends on unknown task '{dep}'."
                        ),
                        suggested_fix=f"Remove '{dep}' from depends_on or add a task with that ID.",
                    )
                )

    return issues


def _check_cycles(graph: TaskGraph) -> list[ValidationIssue]:
    """DFS-based circular dependency detection."""
    adjacency: dict[str, list[str]] = {t.id: list(t.depends_on) for t in graph.tasks}
    visited: set[str] = set()
    in_stack: set[str] = set()
    stack_path: list[str] = []
    issues: list[ValidationIssue] = []

    def dfs(node: str) -> bool:
        visited.add(node)
        in_stack.add(node)
        stack_path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor in in_stack:
                # Found a cycle — extract it
                cycle_start = stack_path.index(neighbor)
                cycle = stack_path[cycle_start:] + [neighbor]
                issues.append(
                    ValidationIssue(
                        severity="fatal",
                        category="cycle",
                        affected_tasks=list(dict.fromkeys(cycle)),
                        description=f"Circular dependency detected: {' -> '.join(cycle)}",
                        suggested_fix="Remove one of the dependency edges to break the cycle.",
                    )
                )
                stack_path.pop()
                in_stack.remove(node)
                return True
            if neighbor not in visited:
                if dfs(neighbor):
                    stack_path.pop()
                    in_stack.remove(node)
                    return True
        stack_path.pop()
        in_stack.remove(node)
        return False

    for task_id in adjacency:
        if task_id not in visited:
            dfs(task_id)

    return issues


def _check_task_granularity(graph: TaskGraph) -> list[ValidationIssue]:
    """Flag tasks with >10 files as major task_too_large; <50 char description as minor vague_description."""
    issues: list[ValidationIssue] = []

    for task in graph.tasks:
        if len(task.files) > _MAX_FILES_PER_TASK:
            issues.append(
                ValidationIssue(
                    severity="major",
                    category="task_too_large",
                    affected_tasks=[task.id],
                    description=(
                        f"Task '{task.id}' touches {len(task.files)} files (limit: 10). "
                        "Split into smaller tasks."
                    ),
                    suggested_fix="Break this task into multiple smaller tasks, each touching fewer files.",
                )
            )

        if len(task.description) < _MIN_DESCRIPTION_LENGTH:
            issues.append(
                ValidationIssue(
                    severity="minor",
                    category="vague_description",
                    affected_tasks=[task.id],
                    description=(
                        f"Task '{task.id}' has a vague description ({len(task.description)} chars, minimum 50). "
                        "Provide more detail."
                    ),
                    suggested_fix="Expand the description to include what changes are needed and why.",
                )
            )

    return issues


# Regex to find file-path-like strings in task descriptions.
# Matches paths like src/foo.py, ./tests/test_bar.ts, review_bot/server/api.py, etc.
_FILE_PATH_RE = re.compile(
    r"""(?<![a-zA-Z0-9_/\\])"""        # not preceded by path-like chars
    r"""(?:\.?/)?"""                     # optional leading ./ or /
    r"""(?:[a-zA-Z0-9_\-]+/)+"""        # one or more directory segments ending in /
    r"""[a-zA-Z0-9_\-]+"""             # filename stem
    r"""\.[a-zA-Z]{1,10}"""            # file extension (.py, .ts, .json, etc.)
    r"""(?![a-zA-Z0-9_/\\])"""         # not followed by path-like chars
)


def _normalize_path(p: str) -> str:
    """Normalize a file path for comparison."""
    import posixpath

    p = p.replace("\\", "/")
    p = posixpath.normpath(p)
    if p.startswith("./"):
        p = p[2:]
    return p


def _check_file_completeness(graph: TaskGraph) -> list[ValidationIssue]:
    """Detect when a task description mentions files not in its files list.

    If the description references a file path (e.g. ``src/api.py``) but that
    file isn't in the task's ``files`` array, the agent won't be able to edit
    it at runtime due to file-scope enforcement.  This causes infinite review
    loops where the reviewer keeps asking the agent to modify a file it can't
    touch.
    """
    # Build a set of ALL files owned by ANY task for reference
    all_task_files: set[str] = set()
    for task in graph.tasks:
        for f in task.files:
            all_task_files.add(_normalize_path(f))

    issues: list[ValidationIssue] = []

    for task in graph.tasks:
        owned_files = {_normalize_path(f) for f in task.files}
        mentioned_paths = _FILE_PATH_RE.findall(task.description)

        for mentioned in mentioned_paths:
            norm = _normalize_path(mentioned)
            if norm in owned_files:
                continue
            # Only flag if this looks like a real project file (has a directory component)
            # and the description implies modification (not just referencing for context).
            # We flag it as major since it will cause runtime failures.
            issues.append(
                ValidationIssue(
                    severity="major",
                    category="missing_file_in_scope",
                    affected_tasks=[task.id],
                    description=(
                        f"Task '{task.id}' description mentions '{mentioned}' but this file "
                        f"is not in the task's files list. Agents can only edit files in their "
                        f"scope — this will cause the task to fail at runtime."
                    ),
                    suggested_fix=(
                        f"Add '{norm}' to task '{task.id}' files list, or remove the "
                        f"reference from the description if the file is owned by another task."
                    ),
                )
            )

    return issues
