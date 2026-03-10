"""Tests for HomeScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.home import HomeScreen


class HomeTestApp(App):
    def compose(self) -> ComposeResult:
        yield HomeScreen()


@pytest.mark.asyncio
async def test_home_screen_mounts():
    app = HomeTestApp()
    async with app.run_test() as pilot:
        assert app.query_one("ForgeLogo") is not None
        assert app.query_one("Input") is not None


def test_format_recent_pipelines():
    from forge.tui.screens.home import format_recent_pipelines
    pipelines = [
        {"id": "abc", "description": "Build auth system", "status": "complete", "created_at": "2026-03-10", "cost": 2.50},
        {"id": "def", "description": "Fix login bug", "status": "error", "created_at": "2026-03-09", "cost": 0.80},
    ]
    result = format_recent_pipelines(pipelines)
    assert "Build auth system" in result
    assert "Fix login bug" in result
    assert "\u2714" in result
    assert "\u2716" in result


def test_format_recent_pipelines_empty():
    from forge.tui.screens.home import format_recent_pipelines
    result = format_recent_pipelines([])
    assert "No recent pipelines" in result
