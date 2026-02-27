from forge.tui.dashboard import format_status_table
from forge.core.models import TaskRecord, TaskState, Complexity


def test_format_empty():
    output = format_status_table([])
    assert "No tasks" in output


def test_format_with_tasks():
    tasks = [
        TaskRecord(
            id="task-1", title="Build model", description="D",
            files=["a.py"], depends_on=[], complexity=Complexity.LOW,
            state=TaskState.IN_PROGRESS, assigned_agent="agent-1",
        ),
        TaskRecord(
            id="task-2", title="Build API", description="D",
            files=["b.py"], depends_on=["task-1"], complexity=Complexity.MEDIUM,
            state=TaskState.TODO,
        ),
    ]
    output = format_status_table(tasks)
    assert "task-1" in output
    assert "Build model" in output
    assert "in_progress" in output
    assert "task-2" in output
