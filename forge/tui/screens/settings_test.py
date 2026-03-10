"""Tests for SettingsScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.settings import SettingsScreen, format_settings


class MockSettings:
    """Minimal mock to avoid pydantic import issues."""
    model_strategy = "auto"
    max_agents = 4
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
    async with app.run_test() as pilot:
        pass  # mount without crash is the test
