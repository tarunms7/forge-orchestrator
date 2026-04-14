"""Tests for cost velocity tracking."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from forge.core.cost_velocity import CostVelocityTracker


def _make_event(created_at: str, total_cost_usd: float):
    """Create a mock PipelineEventRow."""
    ev = AsyncMock()
    ev.created_at = created_at
    ev.payload = {"total_cost_usd": total_cost_usd}
    return ev


def _make_db(events=None, pipeline_cost: float = 0.0):
    """Create a mock database."""
    db = AsyncMock()
    db.list_events = AsyncMock(return_value=events or [])
    db.get_pipeline_cost = AsyncMock(return_value=pipeline_cost)
    return db


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestCostVelocityTrackerInit:
    def test_initial_state(self):
        db = _make_db()
        tracker = CostVelocityTracker(db, "pipe-1")
        assert tracker.current_spent == 0.0
        assert tracker.burn_rate_per_min == 0.0

    def test_class_constant(self):
        assert CostVelocityTracker.WINDOW_SECONDS == 300


class TestUpdate:
    async def test_update_sets_current_spent_from_db(self):
        db = _make_db(pipeline_cost=1.25)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        assert tracker.current_spent == 1.25
        db.get_pipeline_cost.assert_awaited_once_with("pipe-1")

    async def test_update_queries_cost_events(self):
        db = _make_db()
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        db.list_events.assert_awaited_once_with("pipe-1", event_type="pipeline:cost_update")

    async def test_no_events_gives_zero_burn_rate(self):
        db = _make_db(events=[], pipeline_cost=0.5)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        assert tracker.burn_rate_per_min == 0.0
        assert tracker.current_spent == 0.5

    async def test_single_event_gives_zero_burn_rate(self):
        now = datetime.now(UTC)
        events = [_make_event(_iso(now - timedelta(seconds=60)), 0.10)]
        db = _make_db(events=events, pipeline_cost=0.10)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        assert tracker.burn_rate_per_min == 0.0

    async def test_two_events_computes_burn_rate(self):
        now = datetime.now(UTC)
        events = [
            _make_event(_iso(now - timedelta(seconds=120)), 0.10),
            _make_event(_iso(now - timedelta(seconds=60)), 0.20),
        ]
        db = _make_db(events=events, pipeline_cost=0.20)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        # cost_delta=0.10, time_delta=60s=1min → 0.10/min
        assert abs(tracker.burn_rate_per_min - 0.10) < 0.001

    async def test_old_events_excluded_from_window(self):
        now = datetime.now(UTC)
        events = [
            # This event is outside the 5-min window
            _make_event(_iso(now - timedelta(seconds=600)), 0.05),
            _make_event(_iso(now - timedelta(seconds=120)), 0.10),
            _make_event(_iso(now - timedelta(seconds=60)), 0.20),
        ]
        db = _make_db(events=events, pipeline_cost=0.20)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        # Only the last two events are in the window
        # cost_delta=0.10, time_delta=60s=1min → 0.10/min
        assert abs(tracker.burn_rate_per_min - 0.10) < 0.001

    async def test_events_at_same_time_give_zero_rate(self):
        now = datetime.now(UTC)
        ts = _iso(now - timedelta(seconds=30))
        events = [
            _make_event(ts, 0.10),
            _make_event(ts, 0.20),
        ]
        db = _make_db(events=events, pipeline_cost=0.20)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        assert tracker.burn_rate_per_min == 0.0

    async def test_naive_datetime_treated_as_utc(self):
        """Events with naive timestamps should be treated as UTC."""
        now = datetime.now(UTC)
        events = [
            _make_event((now - timedelta(seconds=120)).replace(tzinfo=None).isoformat(), 0.10),
            _make_event((now - timedelta(seconds=60)).replace(tzinfo=None).isoformat(), 0.20),
        ]
        db = _make_db(events=events, pipeline_cost=0.20)
        tracker = CostVelocityTracker(db, "pipe-1")

        await tracker.update()

        assert abs(tracker.burn_rate_per_min - 0.10) < 0.001


class TestProjectedFinalCost:
    async def test_projection(self):
        now = datetime.now(UTC)
        events = [
            _make_event(_iso(now - timedelta(seconds=120)), 0.10),
            _make_event(_iso(now - timedelta(seconds=60)), 0.20),
        ]
        db = _make_db(events=events, pipeline_cost=0.20)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        # current_spent=0.20, burn_rate≈0.10/min, remaining=10min → 0.20 + 1.0 = 1.20
        projected = tracker.projected_final_cost(10.0)
        assert abs(projected - 1.20) < 0.01

    def test_projection_before_update(self):
        db = _make_db()
        tracker = CostVelocityTracker(db, "pipe-1")
        assert tracker.projected_final_cost(10.0) == 0.0


class TestTimeToExhaustion:
    async def test_returns_minutes_remaining(self):
        now = datetime.now(UTC)
        events = [
            _make_event(_iso(now - timedelta(seconds=120)), 0.10),
            _make_event(_iso(now - timedelta(seconds=60)), 0.20),
        ]
        db = _make_db(events=events, pipeline_cost=0.20)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        # burn_rate≈0.10/min, budget=1.0, remaining=(1.0-0.20)/0.10=8.0 min
        tte = tracker.time_to_exhaustion(1.0)
        assert tte is not None
        assert abs(tte - 8.0) < 0.1

    async def test_returns_none_when_no_burn(self):
        db = _make_db(pipeline_cost=0.5)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.time_to_exhaustion(1.0) is None

    def test_returns_none_before_update(self):
        db = _make_db()
        tracker = CostVelocityTracker(db, "pipe-1")
        assert tracker.time_to_exhaustion(1.0) is None


class TestShouldWarn:
    async def test_warns_at_80_percent(self):
        db = _make_db(pipeline_cost=0.80)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_warn(1.0) is True

    async def test_no_warn_under_80_percent(self):
        db = _make_db(pipeline_cost=0.79)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_warn(1.0) is False

    async def test_no_warn_zero_budget(self):
        db = _make_db(pipeline_cost=0.80)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_warn(0.0) is False

    async def test_no_warn_negative_budget(self):
        db = _make_db(pipeline_cost=0.80)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_warn(-1.0) is False


class TestShouldPause:
    async def test_pauses_at_95_percent(self):
        db = _make_db(pipeline_cost=0.95)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_pause(1.0) is True

    async def test_no_pause_under_95_percent(self):
        db = _make_db(pipeline_cost=0.94)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_pause(1.0) is False

    async def test_no_pause_zero_budget(self):
        db = _make_db(pipeline_cost=0.95)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_pause(0.0) is False

    async def test_no_pause_negative_budget(self):
        db = _make_db(pipeline_cost=0.95)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_pause(-1.0) is False

    async def test_pause_when_over_budget(self):
        db = _make_db(pipeline_cost=1.50)
        tracker = CostVelocityTracker(db, "pipe-1")
        await tracker.update()

        assert tracker.should_pause(1.0) is True


class TestTimeoutBehavior:
    async def test_update_timeout_on_slow_list_events(self):
        import asyncio

        db = AsyncMock()

        async def _slow(*args, **kwargs):
            await asyncio.sleep(60)
            return []

        db.list_events = _slow
        db.get_pipeline_cost = AsyncMock(return_value=0.0)
        tracker = CostVelocityTracker(db, "pipe-1")

        with pytest.raises(asyncio.TimeoutError):
            await tracker.update()

    async def test_update_timeout_on_slow_get_cost(self):
        import asyncio

        db = AsyncMock()
        db.list_events = AsyncMock(return_value=[])

        async def _slow(*args, **kwargs):
            await asyncio.sleep(60)
            return 0.0

        db.get_pipeline_cost = _slow
        tracker = CostVelocityTracker(db, "pipe-1")

        with pytest.raises(asyncio.TimeoutError):
            await tracker.update()
