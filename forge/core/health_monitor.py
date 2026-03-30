"""Pipeline health monitor — detects stuck tasks and auto-heals.

Runs as a background asyncio task alongside the execution loop.
Checks every 30 seconds for:
- Tasks stuck in IN_PROGRESS for too long (agent died without reporting)
- Tasks stuck in IN_REVIEW for too long (review hung)
- Tasks stuck in MERGING for too long (merge deadlock)
- All tasks blocked with no path forward (dependency deadlock)

When a stuck task is detected, the monitor either:
- Retries it (if under retry limit)
- Marks it ERROR (if retries exhausted)
- Logs a clear diagnostic message
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("forge.health")


@dataclass
class HealthConfig:
    """Thresholds for health monitoring."""

    check_interval_s: float = 30.0
    task_stuck_timeout_s: float = 900.0  # 15 min — task in_progress with no output
    review_stuck_timeout_s: float = 600.0  # 10 min — review gate hung
    merge_stuck_timeout_s: float = 300.0  # 5 min — merge taking too long
    deadlock_check_enabled: bool = True


class PipelineHealthMonitor:
    """Background monitor that detects and heals stuck pipeline states.

    Usage::

        monitor = PipelineHealthMonitor(db, pipeline_id, settings)
        monitor_task = asyncio.create_task(monitor.run())
        # ... pipeline execution ...
        monitor.stop()
        await monitor_task
    """

    def __init__(
        self,
        db,
        pipeline_id: str,
        config: HealthConfig | None = None,
        on_stuck_task=None,
    ):
        self._db = db
        self._pipeline_id = pipeline_id
        self._config = config or HealthConfig()
        self._on_stuck_task = on_stuck_task  # async callback(task_id, reason)
        self._running = False
        self._task_last_output: dict[str, float] = {}  # task_id → last output timestamp

    def record_task_activity(self, task_id: str) -> None:
        """Call when a task produces output. Resets the stuck timer."""
        self._task_last_output[task_id] = time.monotonic()

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._running = False
        self.cleanup()

    def cleanup(self) -> None:
        """Clear all tracked task state to prevent memory leaks."""
        self._task_last_output.clear()

    async def run(self) -> None:
        """Main monitoring loop. Runs until stop() is called."""
        self._running = True
        logger.info(
            "Health monitor started (interval=%ds, task_timeout=%ds)",
            self._config.check_interval_s,
            self._config.task_stuck_timeout_s,
        )
        while self._running:
            try:
                await asyncio.sleep(self._config.check_interval_s)
                if not self._running:
                    break
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Health check failed")
        logger.info("Health monitor stopped")

    async def _check_health(self) -> None:
        """Run all health checks."""
        tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
        if not tasks:
            return

        now = time.monotonic()
        stuck_tasks = []

        for task in tasks:
            state = task.state
            task_id = task.id

            if state == "in_progress":
                last_activity = self._task_last_output.get(task_id, now)
                idle_time = now - last_activity
                if idle_time > self._config.task_stuck_timeout_s:
                    stuck_tasks.append((task_id, state, idle_time, "no agent output"))

            elif state == "in_review":
                last_activity = self._task_last_output.get(task_id, now)
                idle_time = now - last_activity
                if idle_time > self._config.review_stuck_timeout_s:
                    stuck_tasks.append((task_id, state, idle_time, "review hung"))

            elif state == "merging":
                last_activity = self._task_last_output.get(task_id, now)
                idle_time = now - last_activity
                if idle_time > self._config.merge_stuck_timeout_s:
                    stuck_tasks.append((task_id, state, idle_time, "merge stuck"))

        # Clean up tracking entries for tasks in terminal states
        terminal_states = {"done", "error", "cancelled"}
        for task in tasks:
            if task.state in terminal_states:
                self._task_last_output.pop(task.id, None)

        # Check for deadlock: all remaining tasks form a blocked cycle
        if self._config.deadlock_check_enabled:
            remaining = [
                t for t in tasks if t.state not in ("done", "error", "cancelled")
            ]
            if remaining:
                states = {t.id: t.state for t in remaining}
                # Human-resolvable states are not deadlocks
                human_resolvable = {"awaiting_input", "awaiting_approval"}
                if any(s in human_resolvable for s in states.values()):
                    pass  # Not a deadlock — humans can unblock
                elif all(s == "blocked" for s in states.values()):
                    # All remaining are blocked — check for true circular dependency
                    cycle = self._find_blocked_cycle(remaining, states)
                    if cycle:
                        cycle_str = " → ".join(cycle + [cycle[0]])
                        logger.error(
                            "Deadlock detected: %d tasks blocked in cycle: [%s]",
                            len(cycle),
                            cycle_str,
                        )
                        if self._on_stuck_task:
                            for task_id in cycle:
                                await self._on_stuck_task(
                                    task_id,
                                    f"deadlock — blocked in cycle: [{cycle_str}]",
                                )

        for task_id, state, idle_time, reason in stuck_tasks:
            logger.warning(
                "STUCK: Task %s in state '%s' idle for %.0fs — %s",
                task_id,
                state,
                idle_time,
                reason,
            )
            if self._on_stuck_task:
                await self._on_stuck_task(task_id, reason)

    @staticmethod
    def _find_blocked_cycle(
        remaining: list, states: dict[str, str]
    ) -> list[str] | None:
        """Return task IDs forming a dependency cycle, or None if no cycle exists.

        A true deadlock requires every blocked task to depend only on other
        blocked tasks (circular).  If any blocked task depends on a TODO task,
        it is merely waiting for dispatch — not a deadlock.
        """
        blocked_ids = {t.id for t in remaining if t.state == "blocked"}
        # Build adjacency: blocked task → its blocked dependencies
        deps: dict[str, list[str]] = {}
        for t in remaining:
            if t.state != "blocked":
                continue
            task_deps = getattr(t, "depends_on", []) or []
            blocked_deps = [d for d in task_deps if d in blocked_ids]
            # If a dependency is not in blocked_ids, it's TODO or another
            # non-terminal state — this task is just waiting, not deadlocked.
            if len(blocked_deps) != len(task_deps):
                return None
            deps[t.id] = blocked_deps

        # DFS cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {tid: WHITE for tid in blocked_ids}
        path: list[str] = []

        def dfs(node: str) -> list[str] | None:
            color[node] = GRAY
            path.append(node)
            for dep in deps.get(node, []):
                if color[dep] == GRAY:
                    # Found a cycle — extract it
                    idx = path.index(dep)
                    return path[idx:]
                if color[dep] == WHITE:
                    result = dfs(dep)
                    if result is not None:
                        return result
            path.pop()
            color[node] = BLACK
            return None

        for tid in blocked_ids:
            if color[tid] == WHITE:
                cycle = dfs(tid)
                if cycle is not None:
                    return cycle
        return None

