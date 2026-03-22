"""DAG-aware task scheduler. Deterministic dispatch based on dependency state."""

from forge.core.models import AgentRecord, AgentState, TaskRecord, TaskState


class Scheduler:
    """Pure-function scheduler. No side effects — returns dispatch plans."""

    @staticmethod
    def ready_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
        """Return tasks that are TODO and have all dependencies DONE."""
        done_ids = {t.id for t in tasks if t.state == TaskState.DONE}
        return [
            t
            for t in tasks
            if t.state == TaskState.TODO and all(dep in done_ids for dep in (t.depends_on or []))
        ]

    @staticmethod
    def dispatch_plan(
        tasks: list[TaskRecord],
        agents: list[AgentRecord],
        max_agents: int,
    ) -> list[tuple[str, str]]:
        """Produce (task_id, agent_id) pairs for dispatch."""
        ready = Scheduler.ready_tasks(tasks)
        idle = [a for a in agents if a.state == AgentState.IDLE]

        working_count = sum(1 for a in agents if a.state == AgentState.WORKING)
        available_slots = max(0, max_agents - working_count)

        plan: list[tuple[str, str]] = []
        for task, agent in zip(ready, idle, strict=False):
            if len(plan) >= available_slots:
                break
            plan.append((task.id, agent.id))

        return plan
