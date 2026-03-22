"""Tests for SettingsScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.settings import (
    SettingsScreen,
    _render_autonomy,
    format_settings,
)


class MockSettings:
    """Minimal mock to avoid pydantic import issues."""
    model_strategy = "auto"
    max_agents = 2
    agent_timeout_seconds = 600
    max_retries = 5
    build_cmd = None
    test_cmd = None
    budget_limit_usd = 0.0
    pipeline_timeout_seconds = 3600
    require_approval = False
    contracts_required = False
    cpu_threshold = 80.0
    memory_threshold_pct = 10.0
    disk_threshold_gb = 5.0
    # Human-in-the-loop / autonomy fields
    autonomy = "balanced"
    question_limit = 3
    question_timeout = 1800
    auto_pr = False


def test_format_settings():
    text = format_settings(MockSettings())
    assert "model_strategy" in text
    assert "max_agents" in text
    assert "budget_limit_usd" in text


class SettingsTestApp(App):
    def compose(self) -> ComposeResult:
        yield SettingsScreen(MockSettings())


@pytest.mark.asyncio
async def test_settings_screen_mounts():
    app = SettingsTestApp()
    async with app.run_test() as _pilot:
        pass  # mount without crash is the test


# ---------------------------------------------------------------------------
# Tests for autonomy rendering helper
# ---------------------------------------------------------------------------


def test_render_autonomy_shows_selected_mode():
    s = MockSettings()
    s.autonomy = "full"
    text = _render_autonomy(s, 0)
    assert "[[full]]" in text  # active mode is wrapped in double brackets
    assert "[balanced]" in text
    assert "[supervised]" in text


def test_render_autonomy_highlights_selected_field():
    s = MockSettings()
    # Field index 0 = autonomy row should show cursor indicator.
    text = _render_autonomy(s, 0)
    assert ">" in text


def test_render_autonomy_question_limit():
    s = MockSettings()
    s.question_limit = 5
    text = _render_autonomy(s, 1)
    assert "5" in text
    assert "question_limit" in text


def test_render_autonomy_auto_pr_off():
    s = MockSettings()
    s.auto_pr = False
    text = _render_autonomy(s, 3)
    assert "[OFF]" in text


def test_render_autonomy_auto_pr_on():
    s = MockSettings()
    s.auto_pr = True
    text = _render_autonomy(s, 3)
    assert "[ON]" in text


# ---------------------------------------------------------------------------
# Tests for SettingsScreen keyboard actions (via pilot)
# ---------------------------------------------------------------------------


class AutonomyTestApp(App):
    def on_mount(self) -> None:
        self.push_screen(SettingsScreen(MockSettings()))


@pytest.mark.asyncio
async def test_settings_navigate_down():
    """Down arrow moves selection to next autonomy field."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        assert screen._selected == 0
        await pilot.press("down")
        assert screen._selected == 1


@pytest.mark.asyncio
async def test_settings_navigate_up_wraps():
    """Up arrow from field 0 wraps to last field."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        await pilot.press("up")
        assert screen._selected == 3  # wrapped around


@pytest.mark.asyncio
async def test_settings_right_cycles_autonomy():
    """Right arrow cycles autonomy from balanced -> supervised."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        assert screen._settings.autonomy == "balanced"
        await pilot.press("right")
        assert screen._settings.autonomy == "supervised"


@pytest.mark.asyncio
async def test_settings_left_cycles_autonomy_backward():
    """Left arrow cycles autonomy from balanced -> full."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        await pilot.press("left")
        assert screen._settings.autonomy == "full"


@pytest.mark.asyncio
async def test_settings_toggle_auto_pr():
    """Enter on auto_pr (field 3) flips the boolean."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        # Navigate to auto_pr row (index 3).
        for _ in range(3):
            await pilot.press("down")
        assert screen._selected == 3
        assert screen._settings.auto_pr is False
        await pilot.press("enter")
        assert screen._settings.auto_pr is True


@pytest.mark.asyncio
async def test_settings_increase_question_limit():
    """Right on question_limit increases the value."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        await pilot.press("down")  # navigate to question_limit (index 1)
        assert screen._selected == 1
        original = screen._settings.question_limit
        await pilot.press("right")
        assert screen._settings.question_limit == original + 1


@pytest.mark.asyncio
async def test_settings_decrease_question_limit_clamps():
    """Left on question_limit does not go below 1."""
    app = AutonomyTestApp()
    async with app.run_test() as pilot:
        screen: SettingsScreen = app.screen  # type: ignore[assignment]
        await pilot.press("down")  # question_limit
        screen._settings.question_limit = 1
        await pilot.press("left")
        assert screen._settings.question_limit == 1
