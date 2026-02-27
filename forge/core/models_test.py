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
