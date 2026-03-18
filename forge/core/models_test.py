from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from forge.core.models import (
    Complexity,
    TaskDefinition,
    TaskGraph,
    TaskState,
    TaskRecord,
    AgentRecord,
    AgentState,
    row_to_record,
    row_to_agent,
)


class TestTaskDefinition:
    def test_valid_task(self):
        task = TaskDefinition(
            id="task-1",
            title="Create user model",
            description="Build the user data model",
            files=["src/models/user.py"],
            depends_on=[],
            complexity=Complexity.LOW,
        )
        assert task.id == "task-1"
        assert task.files == ["src/models/user.py"]

    def test_empty_id_rejected(self):
        with pytest.raises(PydanticValidationError):
            TaskDefinition(
                id="",
                title="Bad",
                description="No",
                files=[],
                depends_on=[],
                complexity=Complexity.LOW,
            )

    def test_empty_title_rejected(self):
        with pytest.raises(PydanticValidationError):
            TaskDefinition(
                id="task-1",
                title="",
                description="No",
                files=[],
                depends_on=[],
                complexity=Complexity.LOW,
            )

    def test_depends_on_defaults_empty(self):
        task = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
        )
        assert task.depends_on == []
        assert task.complexity == Complexity.MEDIUM


class TestTaskGraph:
    def test_valid_graph(self):
        graph = TaskGraph(
            tasks=[
                TaskDefinition(
                    id="task-1",
                    title="First",
                    description="Do first",
                    files=["a.py"],
                ),
                TaskDefinition(
                    id="task-2",
                    title="Second",
                    description="Do second",
                    files=["b.py"],
                    depends_on=["task-1"],
                ),
            ]
        )
        assert len(graph.tasks) == 2

    def test_empty_tasks_rejected(self):
        with pytest.raises(PydanticValidationError):
            TaskGraph(tasks=[])


class TestTaskRecord:
    def test_default_state_is_todo(self):
        record = TaskRecord(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            depends_on=[],
            complexity=Complexity.LOW,
        )
        assert record.state == TaskState.TODO
        assert record.retry_count == 0
        assert record.assigned_agent is None


class TestAgentRecord:
    def test_default_state_is_idle(self):
        agent = AgentRecord(id="agent-1")
        assert agent.state == AgentState.IDLE
        assert agent.current_task is None


class TestTaskState:
    def test_awaiting_input_state_exists(self):
        assert TaskState.AWAITING_INPUT == "awaiting_input"
        assert TaskState.AWAITING_INPUT.value == "awaiting_input"

    def test_awaiting_input_distinct_from_awaiting_approval(self):
        assert TaskState.AWAITING_INPUT != TaskState.AWAITING_APPROVAL

    def test_blocked_state_exists(self):
        assert TaskState.BLOCKED == "blocked"
        assert TaskState.BLOCKED.value == "blocked"

    def test_blocked_is_distinct_from_error(self):
        assert TaskState.BLOCKED != TaskState.ERROR


# --- Tests migrated from engine_test.py ---


def _make_task_row(**overrides):
    defaults = {
        "id": "task-1",
        "title": "Build feature",
        "description": "Build it",
        "files": ["a.py"],
        "depends_on": [],
        "complexity": "low",
        "state": "todo",
        "assigned_agent": None,
        "retry_count": 0,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def _make_agent_row(**overrides):
    defaults = {
        "id": "agent-1",
        "state": "idle",
        "current_task": None,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestRowToRecord:
    def test_row_to_record(self):
        row = _make_task_row()
        record = row_to_record(row)
        assert record.id == "task-1"
        assert record.title == "Build feature"
        assert record.state == TaskState.TODO
        assert record.assigned_agent is None
        assert record.retry_count == 0

    def test_row_to_record_in_progress(self):
        row = _make_task_row(state="in_progress", assigned_agent="agent-1")
        record = row_to_record(row)
        assert record.state == TaskState.IN_PROGRESS
        assert record.assigned_agent == "agent-1"


class TestRowToAgent:
    def test_row_to_agent(self):
        row = _make_agent_row()
        record = row_to_agent(row)
        assert record.id == "agent-1"
        assert record.state == AgentState.IDLE
        assert record.current_task is None

    def test_row_to_agent_working(self):
        row = _make_agent_row(state="working", current_task="task-1")
        record = row_to_agent(row)
        assert record.state == AgentState.WORKING
        assert record.current_task == "task-1"
