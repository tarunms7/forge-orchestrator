"""Task state machine. Enforces valid transitions deterministically."""

from forge.core.errors import ForgeError
from forge.core.models import TaskState

_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.TODO: {TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.IN_PROGRESS: {TaskState.IN_REVIEW, TaskState.AWAITING_INPUT, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.IN_REVIEW: {TaskState.MERGING, TaskState.IN_PROGRESS, TaskState.AWAITING_APPROVAL, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.AWAITING_APPROVAL: {TaskState.MERGING, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.AWAITING_INPUT: {TaskState.IN_PROGRESS, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.BLOCKED: {TaskState.TODO, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.MERGING: {TaskState.DONE, TaskState.IN_PROGRESS, TaskState.ERROR},
    TaskState.DONE: set(),
    TaskState.CANCELLED: {TaskState.TODO},
    TaskState.ERROR: {TaskState.TODO},
}


class TaskStateMachine:
    """Deterministic task state transitions. No LLM involved."""

    @staticmethod
    def can_transition(current: TaskState, target: TaskState) -> bool:
        return target in _TRANSITIONS.get(current, set())

    @staticmethod
    def transition(current: TaskState, target: TaskState) -> TaskState:
        if not TaskStateMachine.can_transition(current, target):
            raise ForgeError(
                f"Invalid transition: {current.value} -> {target.value}"
            )
        return target
