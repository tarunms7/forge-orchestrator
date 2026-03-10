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
