"""Forge orchestration engine helpers."""

from forge.core.models import TaskRecord, TaskState, AgentRecord, AgentState


def _row_to_record(row) -> TaskRecord:
    return TaskRecord(
        id=row.id, title=row.title, description=row.description,
        files=row.files, depends_on=row.depends_on, complexity=row.complexity,
        state=TaskState(row.state),
        assigned_agent=row.assigned_agent,
        retry_count=row.retry_count,
    )


def _row_to_agent(row) -> AgentRecord:
    return AgentRecord(
        id=row.id,
        state=AgentState(row.state),
        current_task=row.current_task,
    )
