"""Tests for ReviewScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.review import ReviewScreen
from forge.tui.state import TuiState


class ReviewTestApp(App):
    def compose(self) -> ComposeResult:
        yield ReviewScreen(TuiState())


@pytest.mark.asyncio
async def test_review_screen_mounts():
    app = ReviewTestApp()
    async with app.run_test() as pilot:
        assert app.query_one("DiffViewer") is not None
