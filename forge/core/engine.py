"""Forge orchestration engine. The deterministic brain that ties everything together."""

from forge.agents.runtime import AgentRuntime
from forge.core.models import TaskGraph, TaskRecord, TaskState, AgentRecord, AgentState
from forge.core.monitor import ResourceMonitor
from forge.core.planner import Planner
from forge.core.scheduler import Scheduler
from forge.merge.worker import MergeWorker
from forge.merge.worktree import WorktreeManager
from forge.review.pipeline import ReviewPipeline
from forge.storage.db import Database


class ForgeEngine:
    """Central orchestration. LLMs propose, this code disposes."""

    def __init__(
        self,
        db: Database,
        planner: Planner,
        monitor: ResourceMonitor,
        worktree_manager: WorktreeManager,
        agent_runtime: AgentRuntime,
        review_pipeline: ReviewPipeline,
        merge_worker: MergeWorker,
        max_agents: int = 4,
    ) -> None:
        self._db = db
        self._planner = planner
        self._monitor = monitor
        self._worktrees = worktree_manager
        self._runtime = agent_runtime
        self._review = review_pipeline
        self._merge = merge_worker
        self._max_agents = max_agents

    async def plan(self, user_input: str) -> TaskGraph:
        """Decompose user input into a validated TaskGraph and persist tasks."""
        graph = await self._planner.plan(user_input)
        for task_def in graph.tasks:
            await self._db.create_task(
                id=task_def.id,
                title=task_def.title,
                description=task_def.description,
                files=task_def.files,
                depends_on=task_def.depends_on,
                complexity=task_def.complexity.value,
            )
        return graph

    async def dispatch_cycle(self) -> int:
        """Run one dispatch cycle. Returns number of tasks dispatched."""
        snapshot = self._monitor.take_snapshot()
        if not self._monitor.can_dispatch(snapshot):
            return 0

        tasks = await self._db.list_tasks()
        agents = await self._db.list_agents()

        task_records = [_row_to_record(t) for t in tasks]
        agent_records = [_row_to_agent(a) for a in agents]

        dispatch_plan = Scheduler.dispatch_plan(
            task_records, agent_records, self._max_agents,
        )

        for task_id, agent_id in dispatch_plan:
            await self._db.assign_task(task_id, agent_id)
            await self._db.update_task_state(task_id, TaskState.IN_PROGRESS.value)

        return len(dispatch_plan)


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
