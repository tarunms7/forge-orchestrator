"""Tests for HomeScreen."""

import pytest
from textual.app import App
from textual.widgets import Static

from forge.tui.screens.home import HomeScreen, PromptTextArea, format_recent_pipelines
from forge.tui.widgets.pipeline_list import PipelineList

SAMPLE_PIPELINES = [
    {
        "id": "abc",
        "description": "Build auth system",
        "status": "complete",
        "created_at": "2026-03-10",
        "cost": 2.50,
        "total_cost_usd": 2.50,
        "task_count": 3,
    },
    {
        "id": "def",
        "description": "Fix login bug",
        "status": "error",
        "created_at": "2026-03-09",
        "cost": 0.80,
        "total_cost_usd": 0.80,
        "task_count": 2,
    },
]


class HomeTestApp(App):
    def __init__(self, pipelines=None):
        super().__init__()
        self._pipelines = pipelines

    def on_mount(self) -> None:
        self.push_screen(HomeScreen(recent_pipelines=self._pipelines))


@pytest.mark.asyncio
async def test_home_screen_mounts():
    app = HomeTestApp()
    async with app.run_test():
        assert app.screen.query_one("ForgeLogo") is not None
        assert app.screen.query_one("PromptTextArea") is not None


def test_format_recent_pipelines():
    pipelines = [
        {
            "id": "abc",
            "description": "Build auth system",
            "status": "complete",
            "created_at": "2026-03-10",
            "cost": 2.50,
        },
        {
            "id": "def",
            "description": "Fix login bug",
            "status": "error",
            "created_at": "2026-03-09",
            "cost": 0.80,
        },
    ]
    result = format_recent_pipelines(pipelines)
    assert "Build auth system" in result
    assert "Fix login bug" in result
    assert "\u2714" in result
    assert "\u2716" in result


def test_format_recent_pipelines_empty():
    result = format_recent_pipelines([])
    assert "No recent pipelines" in result


@pytest.mark.asyncio
async def test_home_screen_has_pipeline_list():
    """HomeScreen should contain a PipelineList widget."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test():
        pl = app.screen.query_one(PipelineList)
        assert pl is not None


@pytest.mark.asyncio
async def test_home_screen_pipeline_list_populated():
    """PipelineList should be populated with recent pipelines."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test():
        pl = app.screen.query_one(PipelineList)
        assert len(pl._pipelines) == 2
        assert pl._pipelines[0]["id"] == "abc"


@pytest.mark.asyncio
async def test_home_screen_tab_switches_focus():
    """Tab should switch focus between PromptTextArea and PipelineList."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test() as pilot:
        # Initial focus should be somewhere — we force it
        prompt = app.screen.query_one("PromptTextArea")
        prompt.focus()
        assert prompt.has_focus

        # Tab should switch focus
        app.screen.action_cycle_focus()
        await pilot.pause()
        pl = app.screen.query_one(PipelineList)
        assert pl.has_focus


@pytest.mark.asyncio
async def test_pipeline_list_selected_posts_message():
    """Pressing Enter on PipelineList should post Selected message."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test():
        pl = app.screen.query_one(PipelineList)
        pl.focus()
        messages = []
        original_post = pl.post_message
        pl.post_message = lambda m: messages.append(m) or original_post(m)
        pl.action_select_pipeline()
        assert any(isinstance(m, PipelineList.Selected) for m in messages)


@pytest.mark.asyncio
async def test_prompt_text_area_clear_input_empties_text():
    """action_clear_input() should clear all text in PromptTextArea."""
    app = HomeTestApp()
    async with app.run_test() as pilot:
        prompt = app.screen.query_one(PromptTextArea)
        prompt.load_text("hello world")
        await pilot.pause()
        prompt.action_clear_input()
        await pilot.pause()
        assert prompt.text == ""


@pytest.mark.asyncio
async def test_prompt_text_area_clear_input_resets_cursor():
    """action_clear_input() should reset cursor position to (0, 0)."""
    app = HomeTestApp()
    async with app.run_test() as pilot:
        prompt = app.screen.query_one(PromptTextArea)
        prompt.load_text("hello world")
        await pilot.pause()
        prompt.action_clear_input()
        await pilot.pause()
        assert prompt.cursor_location == (0, 0)


@pytest.mark.asyncio
async def test_home_screen_has_shortcuts_panel():
    """HomeScreen should contain a shortcuts panel with id 'shortcuts-panel'."""
    app = HomeTestApp()
    async with app.run_test():
        panel = app.screen.query_one("#shortcuts-panel", Static)
        assert panel is not None


@pytest.mark.asyncio
async def test_shortcuts_panel_contains_all_shortcuts():
    """Shortcuts panel should list all applicable keybindings."""
    app = HomeTestApp()
    async with app.run_test():
        panel = app.screen.query_one("#shortcuts-panel", Static)
        rendered = str(panel.content)
        assert "Ctrl+S" in rendered
        assert "Submit pipeline" in rendered
        assert "Ctrl+U" in rendered
        assert "Clear input" in rendered
        assert "Tab" in rendered
        assert "Switch focus" in rendered
        assert "Ctrl+P" in rendered
        assert "Command palette" in rendered
        assert "Esc" in rendered
        assert "Quit" in rendered
        assert "?" in rendered
        assert "Help" in rendered


@pytest.mark.asyncio
async def test_shortcuts_panel_has_color_codes():
    """Shortcuts panel should use the specified color codes."""
    app = HomeTestApp()
    async with app.run_test():
        panel = app.screen.query_one("#shortcuts-panel", Static)
        rendered = str(panel.content)
        assert "#D8DEE9" in rendered  # section title color
        assert "#5FA8FF" in rendered  # shortcut key color
        assert "#A9C7E8" in rendered  # description color


@pytest.mark.asyncio
async def test_home_screen_no_submit_hint():
    """Old submit-hint Static should be removed."""
    app = HomeTestApp()
    async with app.run_test():
        results = app.screen.query("#submit-hint")
        # submit-hint should not exist, or if query returns empty
        assert len(results) == 0


@pytest.mark.asyncio
async def test_home_screen_has_input_row():
    """HomeScreen should have a Horizontal input-row container."""
    app = HomeTestApp()
    async with app.run_test():
        row = app.screen.query_one("#input-row")
        assert row is not None
