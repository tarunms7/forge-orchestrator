from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from forge.core.models import (
    AgentRecord,
    AgentState,
    Complexity,
    RepoConfig,
    TaskDefinition,
    TaskGraph,
    TaskRecord,
    TaskState,
    row_to_agent,
    row_to_record,
)


class TestRepoConfig:
    def test_fields_stored_correctly(self):
        cfg = RepoConfig(id="backend", path="/home/user/backend", base_branch="main")
        assert cfg.id == "backend"
        assert cfg.path == "/home/user/backend"
        assert cfg.base_branch == "main"

    def test_frozen_immutability(self):
        cfg = RepoConfig(id="frontend", path="/tmp/fe", base_branch="develop")
        with pytest.raises(FrozenInstanceError):
            cfg.id = "changed"  # type: ignore[misc]

    def test_equality(self):
        a = RepoConfig(id="svc", path="/p", base_branch="main")
        b = RepoConfig(id="svc", path="/p", base_branch="main")
        assert a == b

    def test_inequality(self):
        a = RepoConfig(id="svc", path="/p", base_branch="main")
        b = RepoConfig(id="svc", path="/p", base_branch="develop")
        assert a != b


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

    def test_repo_defaults_to_default(self):
        task = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
        )
        assert task.repo == "default"

    def test_repo_custom_value(self):
        task = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            repo="backend",
        )
        assert task.repo == "backend"

    def test_repo_invalid_empty_rejected(self):
        with pytest.raises(PydanticValidationError, match="repo"):
            TaskDefinition(
                id="task-1",
                title="Something",
                description="Desc",
                files=["a.py"],
                repo="",
            )

    def test_repo_invalid_uppercase_rejected(self):
        with pytest.raises(PydanticValidationError, match="repo"):
            TaskDefinition(
                id="task-1",
                title="Something",
                description="Desc",
                files=["a.py"],
                repo="Backend",
            )

    def test_repo_invalid_starts_with_hyphen_rejected(self):
        with pytest.raises(PydanticValidationError, match="repo"):
            TaskDefinition(
                id="task-1",
                title="Something",
                description="Desc",
                files=["a.py"],
                repo="-bad",
            )

    def test_repo_with_hyphens_accepted(self):
        task = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            repo="my-service-2",
        )
        assert task.repo == "my-service-2"

    def test_repo_preserved_in_serialization(self):
        task = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            repo="frontend",
        )
        data = task.model_dump()
        assert data["repo"] == "frontend"
        restored = TaskDefinition.model_validate(data)
        assert restored.repo == "frontend"


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

    def test_from_definition_copies_repo(self):
        defn = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            repo="backend",
        )
        record = TaskRecord.from_definition(defn)
        assert record.repo == "backend"

    def test_from_definition_copies_default_repo(self):
        defn = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
        )
        record = TaskRecord.from_definition(defn)
        assert record.repo == "default"

    def test_repo_defaults_to_default(self):
        record = TaskRecord(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            depends_on=[],
            complexity=Complexity.LOW,
        )
        assert record.repo == "default"


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
        "repo_id": None,
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

    def test_row_to_record_with_repo_id(self):
        row = _make_task_row(repo_id="backend")
        record = row_to_record(row)
        assert record.repo == "backend"

    def test_row_to_record_without_repo_id(self):
        """Backward compat: rows without repo_id default to 'default'."""
        # Use a simple namespace without repo_id attribute (MagicMock auto-creates attrs)
        class FakeRow:
            id = "task-1"
            title = "Build feature"
            description = "Build it"
            files = ["a.py"]
            depends_on = []  # noqa: RUF012
            complexity = "low"
            state = "todo"
            assigned_agent = None
            retry_count = 0

        record = row_to_record(FakeRow())
        assert record.repo == "default"

    def test_row_to_record_with_none_repo_id(self):
        """Rows with repo_id=None also default to 'default'."""
        row = _make_task_row(repo_id=None)
        record = row_to_record(row)
        assert record.repo == "default"


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
