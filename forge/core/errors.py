"""Forge error hierarchy. Every error carries context: what failed, why, what to do."""


class ForgeError(Exception):
    """Base error for all Forge operations."""


class ValidationError(ForgeError):
    """TaskGraph validation failed."""


class CyclicDependencyError(ValidationError):
    """TaskGraph contains a dependency cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Cyclic dependency detected: {' -> '.join(cycle)}")


class FileConflictError(ValidationError):
    """Two tasks claim ownership of the same file."""

    def __init__(self, file_path: str, task_a: str, task_b: str) -> None:
        self.file_path = file_path
        self.task_a = task_a
        self.task_b = task_b
        super().__init__(
            f"File conflict: '{file_path}' claimed by both '{task_a}' and '{task_b}'"
        )


class SchedulerError(ForgeError):
    """Scheduler operation failed."""


class ResourceExhaustedError(SchedulerError):
    """System resources exceeded threshold."""

    def __init__(self, metric: str, value: float, threshold: float) -> None:
        self.metric = metric
        self.value = value
        self.threshold = threshold
        super().__init__(
            f"Resource exhausted: {metric} at {value:.1f}% (threshold: {threshold:.1f}%)"
        )


class AgentError(ForgeError):
    """Agent operation failed."""


class AgentTimeoutError(AgentError):
    """Agent exceeded its time limit."""

    def __init__(self, agent_id: str, timeout_seconds: int) -> None:
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Agent '{agent_id}' timed out after {timeout_seconds}s"
        )


class ReviewError(ForgeError):
    """Review pipeline failed."""


class MergeError(ForgeError):
    """Merge operation failed."""


class MergeConflictError(MergeError):
    """Merge produced conflicts."""

    def __init__(self, conflicting_files: list[str]) -> None:
        self.conflicting_files = conflicting_files
        super().__init__(
            f"Merge conflicts in: {', '.join(conflicting_files)}"
        )
