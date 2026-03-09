from unittest.mock import MagicMock

from forge.core.engine import _row_to_record, _row_to_agent
from forge.core.models import TaskState, AgentState


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


def test_row_to_record():
    row = _make_task_row()
    record = _row_to_record(row)
    assert record.id == "task-1"
    assert record.title == "Build feature"
    assert record.state == TaskState.TODO
    assert record.assigned_agent is None
    assert record.retry_count == 0


def test_row_to_record_in_progress():
    row = _make_task_row(state="in_progress", assigned_agent="agent-1")
    record = _row_to_record(row)
    assert record.state == TaskState.IN_PROGRESS
    assert record.assigned_agent == "agent-1"


def test_row_to_agent():
    row = _make_agent_row()
    record = _row_to_agent(row)
    assert record.id == "agent-1"
    assert record.state == AgentState.IDLE
    assert record.current_task is None


def test_row_to_agent_working():
    row = _make_agent_row(state="working", current_task="task-1")
    record = _row_to_agent(row)
    assert record.state == AgentState.WORKING
    assert record.current_task == "task-1"
