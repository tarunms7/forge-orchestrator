import pytest

from forge.core.errors import ForgeError
from forge.core.models import TaskState
from forge.core.state import TaskStateMachine


class TestValidTransitions:
    def test_todo_to_in_progress(self):
        assert TaskStateMachine.transition(TaskState.TODO, TaskState.IN_PROGRESS) == TaskState.IN_PROGRESS

    def test_in_progress_to_in_review(self):
        assert TaskStateMachine.transition(TaskState.IN_PROGRESS, TaskState.IN_REVIEW) == TaskState.IN_REVIEW

    def test_in_review_to_merging(self):
        assert TaskStateMachine.transition(TaskState.IN_REVIEW, TaskState.MERGING) == TaskState.MERGING

    def test_merging_to_done(self):
        assert TaskStateMachine.transition(TaskState.MERGING, TaskState.DONE) == TaskState.DONE

    def test_in_review_rejected_back_to_in_progress(self):
        assert TaskStateMachine.transition(TaskState.IN_REVIEW, TaskState.IN_PROGRESS) == TaskState.IN_PROGRESS

    def test_merging_rejected_back_to_in_progress(self):
        assert TaskStateMachine.transition(TaskState.MERGING, TaskState.IN_PROGRESS) == TaskState.IN_PROGRESS

    def test_any_to_cancelled(self):
        for state in [TaskState.TODO, TaskState.IN_PROGRESS, TaskState.IN_REVIEW]:
            assert TaskStateMachine.transition(state, TaskState.CANCELLED) == TaskState.CANCELLED

    def test_any_to_error(self):
        for state in [TaskState.TODO, TaskState.IN_PROGRESS, TaskState.IN_REVIEW, TaskState.MERGING]:
            assert TaskStateMachine.transition(state, TaskState.ERROR) == TaskState.ERROR


class TestInvalidTransitions:
    def test_done_to_anything_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.DONE, TaskState.TODO)

    def test_cancelled_to_anything_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.CANCELLED, TaskState.TODO)

    def test_todo_to_done_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.TODO, TaskState.DONE)

    def test_todo_to_merging_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.TODO, TaskState.MERGING)


class TestAwaitingApprovalTransitions:
    def test_in_review_to_awaiting_approval(self):
        assert TaskStateMachine.transition(TaskState.IN_REVIEW, TaskState.AWAITING_APPROVAL) == TaskState.AWAITING_APPROVAL

    def test_awaiting_approval_to_merging(self):
        assert TaskStateMachine.transition(TaskState.AWAITING_APPROVAL, TaskState.MERGING) == TaskState.MERGING

    def test_awaiting_approval_to_cancelled(self):
        assert TaskStateMachine.transition(TaskState.AWAITING_APPROVAL, TaskState.CANCELLED) == TaskState.CANCELLED

    def test_awaiting_approval_to_error(self):
        assert TaskStateMachine.transition(TaskState.AWAITING_APPROVAL, TaskState.ERROR) == TaskState.ERROR

    def test_awaiting_approval_to_todo_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.AWAITING_APPROVAL, TaskState.TODO)


class TestCanTransition:
    def test_valid_returns_true(self):
        assert TaskStateMachine.can_transition(TaskState.TODO, TaskState.IN_PROGRESS) is True

    def test_invalid_returns_false(self):
        assert TaskStateMachine.can_transition(TaskState.DONE, TaskState.TODO) is False
