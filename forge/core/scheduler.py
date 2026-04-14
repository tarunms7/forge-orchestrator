"""DAG-aware task scheduler with deterministic, priority-aware dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from forge.core.models import AgentRecord, AgentState, TaskRecord, TaskState

_TERMINAL_STATES = frozenset({TaskState.DONE, TaskState.CANCELLED})
_ACTIVE_STATES = frozenset({TaskState.IN_PROGRESS, TaskState.IN_REVIEW, TaskState.MERGING})
_HUMAN_WAIT_STATES = frozenset({TaskState.AWAITING_APPROVAL, TaskState.AWAITING_INPUT})
_COMPLEXITY_SCORE = {"low": 1.0, "medium": 1.7, "high": 2.5}
# Backpressure: tasks with high retry counts get a priority penalty.
# This ensures the scheduler doesn't keep feeding dependents of struggling
# tasks when other healthy branches of the DAG could make progress.
# Inspired by Claude Code's streaming tool executor sibling-abort pattern.
_RETRY_PENALTY_PER_ATTEMPT = 30  # Deducted from priority score per retry


def _normalize_state(raw_state) -> TaskState | None:
    """Return a TaskState when possible, tolerating raw string test doubles."""
    if isinstance(raw_state, TaskState):
        return raw_state
    try:
        return TaskState(str(raw_state))
    except ValueError:
        return None


@dataclass(frozen=True)
class TaskSchedulingInsight:
    """Priority and blocking metadata for a single task."""

    task_id: str
    state: str
    status: str
    priority_rank: int | None
    priority_score: float
    critical_path_length: int
    downstream_count: int
    blocking_task_ids: tuple[str, ...] = ()
    reason: str = ""

    def to_payload(self) -> dict:
        """Return a JSON-serializable representation for events and TUI state."""
        return {
            "task_id": self.task_id,
            "state": self.state,
            "status": self.status,
            "priority_rank": self.priority_rank,
            "priority_score": round(self.priority_score, 2),
            "critical_path_length": self.critical_path_length,
            "downstream_count": self.downstream_count,
            "blocking_task_ids": list(self.blocking_task_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SchedulingAnalysis:
    """Snapshot of the scheduler's current view of the DAG."""

    ready_task_ids: tuple[str, ...]
    waiting_task_ids: tuple[str, ...]
    blocked_task_ids: tuple[str, ...]
    active_task_ids: tuple[str, ...]
    human_wait_task_ids: tuple[str, ...]
    error_task_ids: tuple[str, ...]
    done_task_ids: tuple[str, ...]
    cancelled_task_ids: tuple[str, ...]
    critical_path_length: int
    task_insights: dict[str, TaskSchedulingInsight]

    def to_payload(self, *, dispatching_now: list[str] | None = None) -> dict:
        """Return a compact, JSON-serializable queue summary."""
        dispatching = list(dispatching_now or [])
        next_up = []
        for task_id in self.ready_task_ids[:3]:
            insight = self.task_insights[task_id]
            next_up.append(
                {
                    "task_id": task_id,
                    "priority_rank": insight.priority_rank,
                    "priority_score": round(insight.priority_score, 2),
                    "critical_path_length": insight.critical_path_length,
                    "downstream_count": insight.downstream_count,
                }
            )

        return {
            "ready_count": len(self.ready_task_ids),
            "waiting_count": len(self.waiting_task_ids),
            "blocked_count": len(self.blocked_task_ids),
            "active_count": len(self.active_task_ids),
            "human_wait_count": len(self.human_wait_task_ids),
            "error_count": len(self.error_task_ids),
            "done_count": len(self.done_task_ids),
            "cancelled_count": len(self.cancelled_task_ids),
            "critical_path_length": self.critical_path_length,
            "ready_task_ids": list(self.ready_task_ids),
            "waiting_task_ids": list(self.waiting_task_ids),
            "blocked_task_ids": list(self.blocked_task_ids),
            "active_task_ids": list(self.active_task_ids),
            "human_wait_task_ids": list(self.human_wait_task_ids),
            "dispatching_now": dispatching,
            "next_up": next_up,
            "tasks": {task_id: info.to_payload() for task_id, info in self.task_insights.items()},
        }


class Scheduler:
    """Pure-function scheduler. No side effects — returns dispatch plans."""

    @staticmethod
    def ready_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
        """Return ready tasks ordered by execution priority."""
        analysis = Scheduler.analyze(tasks)
        task_index = {task.id: task for task in tasks}
        return [task_index[task_id] for task_id in analysis.ready_task_ids if task_id in task_index]

    @staticmethod
    def blocked_by_error(tasks: list[TaskRecord]) -> list[TaskRecord]:
        """Return TODO tasks whose dependencies include at least one ERROR task."""
        error_ids = frozenset(t.id for t in tasks if _normalize_state(t.state) == TaskState.ERROR)
        return [
            t
            for t in tasks
            if _normalize_state(t.state) == TaskState.TODO
            and any(dep in error_ids for dep in (t.depends_on or []))
        ]

    @staticmethod
    def dispatch_plan(
        tasks: list[TaskRecord],
        agents: list[AgentRecord],
        max_agents: int,
        *,
        analysis: SchedulingAnalysis | None = None,
    ) -> list[tuple[str, str]]:
        """Produce (task_id, agent_id) pairs for dispatch."""
        analysis = analysis or Scheduler.analyze(tasks)
        task_index = {task.id: task for task in tasks}
        ready = [
            task_index[task_id] for task_id in analysis.ready_task_ids if task_id in task_index
        ]
        idle = [a for a in agents if a.state == AgentState.IDLE]

        working_count = sum(1 for a in agents if a.state == AgentState.WORKING)
        available_slots = max(0, max_agents - working_count)

        plan: list[tuple[str, str]] = []
        for task, agent in zip(ready, idle, strict=False):
            if len(plan) >= available_slots:
                break
            plan.append((task.id, agent.id))

        return plan

    @staticmethod
    def analyze(tasks: list[TaskRecord]) -> SchedulingAnalysis:
        """Return queue-level insight for the current task graph state."""
        if not tasks:
            return SchedulingAnalysis(
                ready_task_ids=(),
                waiting_task_ids=(),
                blocked_task_ids=(),
                active_task_ids=(),
                human_wait_task_ids=(),
                error_task_ids=(),
                done_task_ids=(),
                cancelled_task_ids=(),
                critical_path_length=0,
                task_insights={},
            )

        task_index = {task.id: task for task in tasks}
        original_order = {task.id: idx for idx, task in enumerate(tasks)}
        dependents: dict[str, list[str]] = {task.id: [] for task in tasks}
        for task in tasks:
            for dep_id in task.depends_on or []:
                if dep_id in dependents:
                    dependents[dep_id].append(task.id)

        depth_cache: dict[str, int] = {}

        def remaining_depth(task_id: str, stack: frozenset[str] | None = None) -> int:
            if task_id in depth_cache:
                return depth_cache[task_id]

            task = task_index[task_id]
            state = _normalize_state(task.state)
            if state in _TERMINAL_STATES:
                depth_cache[task_id] = 0
                return 0

            path = stack or frozenset()
            if task_id in path:
                return 1

            child_depth = 0
            next_path = path | {task_id}
            for child_id in dependents.get(task_id, []):
                child_depth = max(child_depth, remaining_depth(child_id, next_path))
            result = 1 + child_depth
            depth_cache[task_id] = result
            return result

        def downstream_count(task_id: str) -> int:
            seen: set[str] = set()
            stack = list(dependents.get(task_id, []))
            while stack:
                child_id = stack.pop()
                if child_id in seen:
                    continue
                seen.add(child_id)
                stack.extend(dependents.get(child_id, []))
            return sum(
                1
                for child_id in seen
                if child_id in task_index
                and _normalize_state(task_index[child_id].state) not in _TERMINAL_STATES
            )

        ready_tasks: list[TaskRecord] = []
        waiting_ids: list[str] = []
        blocked_ids: list[str] = []
        active_ids: list[str] = []
        human_wait_ids: list[str] = []
        error_ids: list[str] = []
        done_ids: list[str] = []
        cancelled_ids: list[str] = []
        task_insights: dict[str, TaskSchedulingInsight] = {}

        for task in tasks:
            deps = [task_index[dep] for dep in (task.depends_on or []) if dep in task_index]
            state = _normalize_state(task.state)
            error_deps = tuple(
                dep.id for dep in deps if _normalize_state(dep.state) == TaskState.ERROR
            )
            unfinished_deps = tuple(
                dep.id for dep in deps if _normalize_state(dep.state) != TaskState.DONE
            )
            critical_path = remaining_depth(task.id)
            downstream = downstream_count(task.id)

            status = "ready"
            reason = ""
            priority_rank: int | None = None
            priority_score = 0.0
            blocking_task_ids: tuple[str, ...] = ()

            if state == TaskState.DONE:
                status = "done"
                done_ids.append(task.id)
            elif state == TaskState.CANCELLED:
                status = "cancelled"
                cancelled_ids.append(task.id)
            elif state == TaskState.ERROR:
                status = "error"
                reason = "Task failed and needs retry or skip"
                error_ids.append(task.id)
            elif state in _ACTIVE_STATES:
                status = "active"
                reason = f"Currently {state.value.replace('_', ' ')}"
                active_ids.append(task.id)
            elif state in _HUMAN_WAIT_STATES:
                status = "human_wait"
                if state == TaskState.AWAITING_INPUT:
                    reason = "Human decision required before resume"
                else:
                    reason = "Human approval required before merge"
                human_wait_ids.append(task.id)
            elif error_deps:
                status = "blocked"
                blocking_task_ids = error_deps
                if len(error_deps) == 1:
                    reason = f"Blocked by failed dependency: {error_deps[0]}"
                else:
                    reason = f"Blocked by failed dependencies: {', '.join(error_deps)}"
                blocked_ids.append(task.id)
            elif unfinished_deps:
                status = "waiting"
                blocking_task_ids = unfinished_deps
                reason = f"Waiting on {', '.join(unfinished_deps)}"
                waiting_ids.append(task.id)
            elif state == TaskState.BLOCKED:
                status = "blocked"
                reason = "Blocked - waiting for manual intervention"
                blocked_ids.append(task.id)
            else:
                status = "ready"
                ready_tasks.append(task)

            task_insights[task.id] = TaskSchedulingInsight(
                task_id=task.id,
                state=state.value if state else str(task.state),
                status=status,
                priority_rank=priority_rank,
                priority_score=priority_score,
                critical_path_length=critical_path,
                downstream_count=downstream,
                blocking_task_ids=blocking_task_ids,
                reason=reason,
            )

        def priority_score(task: TaskRecord) -> float:
            complexity_key = (
                task.complexity.value if hasattr(task.complexity, "value") else str(task.complexity)
            )
            insight = task_insights[task.id]
            # Base score: critical path dominates, then downstream impact
            base = (
                insight.critical_path_length * 100
                + insight.downstream_count * 12
                + _COMPLEXITY_SCORE.get(complexity_key, 1.0)
            )
            # Backpressure: high retry counts signal the task is struggling.
            # Apply a penalty to deprioritize in favor of healthier branches
            # of the DAG. Retry count 0-1 has no penalty; 2+ gets penalized.
            retry_penalty = max(0, task.retry_count - 1) * _RETRY_PENALTY_PER_ATTEMPT
            # Also check if any upstream dependency has retried — this signals
            # the whole branch may be unstable
            upstream_retry_pressure = sum(
                max(0, task_index[dep_id].retry_count - 1) * (_RETRY_PENALTY_PER_ATTEMPT // 2)
                for dep_id in (task.depends_on or [])
                if dep_id in task_index
            )
            return base - retry_penalty - upstream_retry_pressure

        ready_tasks.sort(
            key=lambda task: (
                -priority_score(task),
                original_order.get(task.id, 0),
                task.id,
            )
        )

        for rank, task in enumerate(ready_tasks, start=1):
            info = task_insights[task.id]
            task_insights[task.id] = TaskSchedulingInsight(
                task_id=info.task_id,
                state=info.state,
                status=info.status,
                priority_rank=rank,
                priority_score=priority_score(task),
                critical_path_length=info.critical_path_length,
                downstream_count=info.downstream_count,
                blocking_task_ids=info.blocking_task_ids,
                reason=(
                    "Highest-leverage ready task"
                    if rank == 1
                    else f"Ready to run (priority {rank})"
                ),
            )

        critical_path_length = max(
            (info.critical_path_length for info in task_insights.values()),
            default=0,
        )

        return SchedulingAnalysis(
            ready_task_ids=tuple(task.id for task in ready_tasks),
            waiting_task_ids=tuple(waiting_ids),
            blocked_task_ids=tuple(blocked_ids),
            active_task_ids=tuple(active_ids),
            human_wait_task_ids=tuple(human_wait_ids),
            error_task_ids=tuple(error_ids),
            done_task_ids=tuple(done_ids),
            cancelled_task_ids=tuple(cancelled_ids),
            critical_path_length=critical_path_length,
            task_insights=task_insights,
        )
