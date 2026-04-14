"""Cost velocity tracking for Forge pipelines.

Computes rolling burn rate from pipeline cost events and provides
budget threshold checks (warn at 80%, pause at 95%).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.storage.db import Database, PipelineEventRow


WINDOW_SECONDS: int = 300  # 5-minute sliding window


class CostVelocityTracker:
    """Tracks cost burn rate over a sliding window for a pipeline."""

    WINDOW_SECONDS: int = WINDOW_SECONDS

    def __init__(self, db: Database, pipeline_id: str) -> None:
        self._db = db
        self._pipeline_id = pipeline_id
        self._current_spent: float = 0.0
        self._burn_rate: float = 0.0
        self._events: list[PipelineEventRow] = []

    @property
    def burn_rate_per_min(self) -> float:
        """Current $/min based on cost deltas within the sliding window."""
        return self._burn_rate

    @property
    def current_spent(self) -> float:
        """Latest total_cost_usd from db.get_pipeline_cost()."""
        return self._current_spent

    async def update(self) -> None:
        """Refresh cost data from the database.

        Queries events for time-series burn rate and the pipeline row
        for authoritative current_spent.
        """
        import asyncio

        events = await asyncio.wait_for(
            self._db.list_events(self._pipeline_id, event_type="pipeline:cost_update"),
            timeout=5,
        )
        self._events = events

        self._current_spent = await asyncio.wait_for(
            self._db.get_pipeline_cost(self._pipeline_id),
            timeout=5,
        )

        self._burn_rate = self._compute_burn_rate(events)

    def _compute_burn_rate(self, events: list[PipelineEventRow]) -> float:
        """Compute rolling burn rate from events within the sliding window."""
        now = datetime.now(UTC)

        windowed = []
        for ev in events:
            ts = datetime.fromisoformat(ev.created_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            elapsed = (now - ts).total_seconds()
            if elapsed <= self.WINDOW_SECONDS:
                windowed.append((ts, ev.payload.get("total_cost_usd", 0.0)))

        if len(windowed) < 2:
            return 0.0

        first_time, first_cost = windowed[0]
        last_time, last_cost = windowed[-1]

        time_delta_seconds = (last_time - first_time).total_seconds()
        if time_delta_seconds <= 0:
            return 0.0

        cost_delta = last_cost - first_cost
        time_delta_minutes = time_delta_seconds / 60.0
        return cost_delta / time_delta_minutes

    def projected_final_cost(self, estimated_remaining_minutes: float) -> float:
        """Project final cost based on current spend and burn rate."""
        return self._current_spent + self._burn_rate * estimated_remaining_minutes

    def time_to_exhaustion(self, budget_limit: float) -> float | None:
        """Minutes until budget is exhausted, or None if burn rate <= 0."""
        if self._burn_rate <= 0:
            return None
        return (budget_limit - self._current_spent) / self._burn_rate

    def should_warn(self, budget_limit: float) -> bool:
        """True when current_spent >= 80% of budget_limit."""
        if budget_limit <= 0:
            return False
        return self._current_spent >= 0.8 * budget_limit

    def should_pause(self, budget_limit: float) -> bool:
        """True when current_spent >= 95% of budget_limit."""
        if budget_limit <= 0:
            return False
        return self._current_spent >= 0.95 * budget_limit
