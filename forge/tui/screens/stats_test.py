"""Tests for StatsScreen."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from forge.tui.screens.stats import (
    PipelineDetailScreen,
    PurgeConfirmScreen,
    StatsScreen,
    _fmt_duration,
    _fmt_tokens,
    _trend_arrow,
    format_cost_breakdown,
    format_pipeline_summary,
    format_retry_hotspots,
    format_token_usage,
    format_trend_indicators,
)

# ---------------------------------------------------------------------------
# Helper formatting tests
# ---------------------------------------------------------------------------


def test_fmt_duration_seconds():
    assert _fmt_duration(5.3) == "5.3s"


def test_fmt_duration_minutes():
    assert _fmt_duration(125) == "2.1m"


def test_fmt_duration_hours():
    assert _fmt_duration(7200) == "2.0h"


def test_fmt_tokens_raw():
    assert _fmt_tokens(500) == "500"


def test_fmt_tokens_k():
    assert _fmt_tokens(12500) == "12.5K"


def test_fmt_tokens_m():
    assert _fmt_tokens(2_500_000) == "2.5M"


def test_trend_arrow_not_enough_data():
    result = _trend_arrow([1.0])
    assert "\u2014" in result


def test_trend_arrow_up():
    result = _trend_arrow([10.0, 9.0, 8.0, 1.0, 1.0, 1.0])
    assert "\u25b2" in result


def test_trend_arrow_down():
    result = _trend_arrow([1.0, 1.0, 1.0, 10.0, 9.0, 8.0])
    assert "\u25bc" in result


def test_trend_arrow_flat():
    result = _trend_arrow([5.0, 5.0, 5.0, 5.0])
    assert "\u2014" in result


# ---------------------------------------------------------------------------
# Section formatting tests
# ---------------------------------------------------------------------------


_SAMPLE_TRENDS = [
    {
        "id": "pipe-1",
        "description": "Build REST API",
        "status": "done",
        "duration_s": 842.5,
        "total_cost_usd": 1.23,
        "total_input_tokens": 125000,
        "total_output_tokens": 45000,
        "tasks_succeeded": 4,
        "tasks_failed": 0,
        "total_retries": 1,
        "total_tasks": 4,
        "created_at": "2026-03-23T10:00:00+00:00",
    },
    {
        "id": "pipe-2",
        "description": "Add auth",
        "status": "error",
        "duration_s": 200.0,
        "total_cost_usd": 0.50,
        "total_input_tokens": 50000,
        "total_output_tokens": 20000,
        "tasks_succeeded": 1,
        "tasks_failed": 1,
        "total_retries": 3,
        "total_tasks": 2,
        "created_at": "2026-03-22T10:00:00+00:00",
    },
]


_SAMPLE_STATS = {
    "id": "pipe-1",
    "description": "Build REST API",
    "status": "done",
    "created_at": "2026-03-23T10:00:00+00:00",
    "completed_at": "2026-03-23T10:15:00+00:00",
    "duration_s": 842.5,
    "total_cost_usd": 1.23,
    "planner_cost_usd": 0.08,
    "total_input_tokens": 125000,
    "total_output_tokens": 45000,
    "tasks_succeeded": 4,
    "tasks_failed": 0,
    "total_retries": 1,
    "tasks": [
        {
            "id": "task-1",
            "title": "Add auth endpoints",
            "state": "done",
            "started_at": "2026-03-23T10:01:00+00:00",
            "completed_at": "2026-03-23T10:08:00+00:00",
            "agent_duration_s": 120.5,
            "review_duration_s": 35.2,
            "lint_duration_s": 4.1,
            "merge_duration_s": 2.8,
            "cost_usd": 0.45,
            "agent_cost_usd": 0.35,
            "review_cost_usd": 0.10,
            "input_tokens": 32000,
            "output_tokens": 12000,
            "retry_count": 0,
            "num_turns": 8,
            "max_turns": 25,
            "error_message": None,
        },
    ],
}


_SAMPLE_RETRIES = [
    {
        "error_pattern": "lint check failed",
        "total_retries": 5,
        "task_count": 3,
        "task_ids": ["task-1", "task-3", "task-5"],
    },
    {
        "error_pattern": "test timeout exceeded",
        "total_retries": 2,
        "task_count": 1,
        "task_ids": ["task-2"],
    },
]

_SAMPLE_ANALYTICS = {
    "total": 42,
    "passed": 30,
    "failed": 5,
    "partial": 3,
    "cancelled": 2,
    "other": 2,
    "current_streak": 7,
    "longest_streak": 12,
}


def test_format_pipeline_summary_empty():
    text = format_pipeline_summary([])
    assert "No pipeline data" in text


def test_format_pipeline_summary_with_data():
    text = format_pipeline_summary(_SAMPLE_TRENDS)
    assert "Build REST API" in text
    assert "$1.23" in text


def test_format_cost_breakdown_empty():
    text = format_cost_breakdown({})
    assert "No cost data" in text


def test_format_cost_breakdown_with_data():
    text = format_cost_breakdown(_SAMPLE_STATS)
    assert "Total" in text
    assert "$1.230" in text
    assert "Planner" in text
    assert "Agent" in text
    assert "Review" in text


def test_format_retry_hotspots_empty():
    text = format_retry_hotspots([])
    assert "clean runs" in text


def test_format_retry_hotspots_with_data():
    text = format_retry_hotspots(_SAMPLE_RETRIES)
    assert "lint check failed" in text
    assert "5\u00d7" in text
    assert "task-1" in text


def test_format_token_usage_empty():
    text = format_token_usage({})
    assert "No token data" in text


def test_format_token_usage_with_data():
    text = format_token_usage(_SAMPLE_STATS)
    assert "Input tokens" in text
    assert "Output tokens" in text
    assert "125.0K" in text


def test_format_trend_indicators_not_enough():
    text = format_trend_indicators([_SAMPLE_TRENDS[0]])
    assert "Need at least 2" in text


def test_format_trend_indicators_with_data():
    text = format_trend_indicators(_SAMPLE_TRENDS)
    assert "Cost" in text
    assert "Duration" in text
    assert "Retries" in text


# ---------------------------------------------------------------------------
# Mock DB helper
# ---------------------------------------------------------------------------


def _make_mock_db():
    """Create a mock Database with async methods."""
    db = MagicMock()
    db.get_pipeline_trends = AsyncMock(return_value=_SAMPLE_TRENDS)
    db.get_pipeline_analytics = AsyncMock(return_value=_SAMPLE_ANALYTICS)
    db.get_pipeline_stats = AsyncMock(return_value=_SAMPLE_STATS)
    db.purge_old_pipelines = AsyncMock(return_value=3)
    return db


# ---------------------------------------------------------------------------
# Screen mount tests
# ---------------------------------------------------------------------------


class StatsTestApp(App):
    def compose(self) -> ComposeResult:
        yield StatsScreen(db=_make_mock_db())


@pytest.mark.asyncio
async def test_stats_screen_mounts():
    app = StatsTestApp()
    async with app.run_test() as _pilot:
        pass  # mount without crash is the test


class StatsEmptyApp(App):
    def compose(self) -> ComposeResult:
        yield StatsScreen(db=None)


@pytest.mark.asyncio
async def test_stats_screen_mounts_empty():
    """StatsScreen handles empty/None db gracefully."""
    app = StatsEmptyApp()
    async with app.run_test() as _pilot:
        pass


class StatsPushApp(App):
    def __init__(self, db=None):
        super().__init__()
        self._test_db = db

    def on_mount(self) -> None:
        self.push_screen(StatsScreen(db=self._test_db))


@pytest.mark.asyncio
async def test_stats_screen_escape_closes():
    """Escape pops the StatsScreen."""
    app = StatsPushApp(db=_make_mock_db())
    async with app.run_test() as pilot:
        assert isinstance(app.screen, StatsScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, StatsScreen)


@pytest.mark.asyncio
async def test_stats_screen_refresh_without_db():
    """Refresh action when no DB is available doesn't crash."""
    app = StatsPushApp(db=None)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, StatsScreen)
        await pilot.press("r")
        # Should not crash — just a no-op when DB is unavailable


# ---------------------------------------------------------------------------
# DataTable j/k navigation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jk_navigation():
    """j and k keys navigate the DataTable cursor."""
    app = StatsPushApp(db=_make_mock_db())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#pipeline-table", DataTable)
        # Initial cursor at row 0
        assert table.cursor_row == 0
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("k")
        assert table.cursor_row == 0


# ---------------------------------------------------------------------------
# PipelineDetailScreen tests
# ---------------------------------------------------------------------------


class DetailTestApp(App):
    def on_mount(self) -> None:
        self.push_screen(PipelineDetailScreen(stats=_SAMPLE_STATS))


@pytest.mark.asyncio
async def test_pipeline_detail_screen_mounts():
    """PipelineDetailScreen mounts with stats data."""
    app = DetailTestApp()
    async with app.run_test() as _pilot:
        assert isinstance(app.screen, PipelineDetailScreen)


@pytest.mark.asyncio
async def test_pipeline_detail_screen_escape():
    """Escape closes PipelineDetailScreen."""
    app = DetailTestApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, PipelineDetailScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, PipelineDetailScreen)


# ---------------------------------------------------------------------------
# Purge confirmation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_confirmation_cancel():
    """Pressing 'n' on PurgeConfirmScreen cancels without callback."""
    called = []

    class PurgeTestApp(App):
        def on_mount(self) -> None:
            self.push_screen(PurgeConfirmScreen(callback=lambda: called.append(True)))

    app = PurgeTestApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, PurgeConfirmScreen)
        await pilot.press("n")
        assert not isinstance(app.screen, PurgeConfirmScreen)
        assert called == []


@pytest.mark.asyncio
async def test_purge_confirmation_confirm():
    """Pressing 'y' on PurgeConfirmScreen triggers callback."""
    called = []

    class PurgeTestApp(App):
        def on_mount(self) -> None:
            self.push_screen(PurgeConfirmScreen(callback=lambda: called.append(True)))

    app = PurgeTestApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, PurgeConfirmScreen)
        await pilot.press("y")
        assert not isinstance(app.screen, PurgeConfirmScreen)
        assert called == [True]


# ---------------------------------------------------------------------------
# Auto-refresh timer test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_refresh_timer_setup():
    """StatsScreen sets up a 30s refresh timer on mount."""
    app = StatsPushApp(db=_make_mock_db())
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StatsScreen)
        assert screen._refresh_timer is not None


# ---------------------------------------------------------------------------
# Empty data graceful handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_data_graceful_handling():
    """StatsScreen with db that returns empty data doesn't crash."""
    db = MagicMock()
    db.get_pipeline_trends = AsyncMock(return_value=[])
    db.get_pipeline_analytics = AsyncMock(return_value={})

    app = StatsPushApp(db=db)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StatsScreen)
        assert screen._pipeline_ids == []
