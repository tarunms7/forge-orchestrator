"""Tests for PipelineScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.state import TuiState


class PipelineTestApp(App):
    def on_mount(self) -> None:
        self.push_screen(PipelineScreen(TuiState()))


@pytest.mark.asyncio
async def test_pipeline_screen_mounts():
    app = PipelineTestApp()
    async with app.run_test() as pilot:
        assert app.screen.query_one("TaskList") is not None
        assert app.screen.query_one("AgentOutput") is not None
        assert app.screen.query_one("PipelineProgress") is not None


@pytest.mark.asyncio
async def test_pipeline_screen_dag_toggle():
    app = PipelineTestApp()
    async with app.run_test() as pilot:
        dag = app.screen.query_one("DagOverlay")
        assert not dag.has_class("visible")
        await pilot.press("g")
        assert dag.has_class("visible")
        await pilot.press("g")
        assert not dag.has_class("visible")
