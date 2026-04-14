"""Forge error hierarchy. Every error carries context: what failed, why, what to do."""

from __future__ import annotations


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
        super().__init__(f"File conflict: '{file_path}' claimed by both '{task_a}' and '{task_b}'")


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
        super().__init__(f"Agent '{agent_id}' timed out after {timeout_seconds}s")


class StateTransitionError(ForgeError):
    """Invalid task state transition attempted.

    Raised when code attempts a transition not permitted by the TaskStateMachine.
    Recovery paths that genuinely need to override should use ``force=True``
    on the DB method rather than catching this exception.
    """

    def __init__(self, task_id: str, current: str, target: str) -> None:
        self.task_id = task_id
        self.current_state = current
        self.target_state = target
        super().__init__(
            f"Invalid state transition for task {task_id}: {current} -> {target}"
        )


class ProviderError(ForgeError):
    """Base class for provider-related errors."""


class ProviderTransientError(ProviderError):
    """Transient provider failure (429, 529, timeout). Safe to retry with backoff."""

    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


class ProviderAuthError(ProviderError):
    """Authentication/authorization failure (401, 403). Refresh credentials and retry once."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class ProviderSchemaError(ProviderError):
    """Output format/schema error. Retry with corrective feedback."""


class ProviderPermanentError(ProviderError):
    """Permanent provider failure (400, model not found). Do not retry."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class ProviderResourceError(ProviderError):
    """Resource exhaustion at provider level (OOM, context too long). Pause and reassess."""


class DegradedOperationWarning(ForgeError):
    """Raised/logged when a subsystem is operating in degraded mode.

    Not necessarily fatal — informs callers that quality may be compromised.
    """

    def __init__(self, subsystem: str, reason: str) -> None:
        self.subsystem = subsystem
        self.reason = reason
        super().__init__(f"[DEGRADED] {subsystem}: {reason}")


class ReviewError(ForgeError):
    """Review pipeline failed."""


class MergeError(ForgeError):
    """Merge operation failed."""


class MergeConflictError(MergeError):
    """Merge produced conflicts."""

    def __init__(self, conflicting_files: list[str]) -> None:
        self.conflicting_files = conflicting_files
        super().__init__(f"Merge conflicts in: {', '.join(conflicting_files)}")


class SdkCallError(ForgeError):
    """SDK call failed (rate limit, network, auth) — distinct from validation failures."""

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        self.original_error = original_error
        super().__init__(message)
