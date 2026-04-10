"""Tests for the provider and routing settings screen."""

from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import Button, Select

from forge.config.settings import ForgeSettings
from forge.core.provider_config import build_provider_registry
from forge.providers.status import ProviderConnectionStatus
from forge.tui.screens.settings import SettingsScreen


def _statuses(*, claude_connected: bool = True, codex_connected: bool = False):
    return {
        "claude": ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=claude_connected,
            status="Connected" if claude_connected else "Needs login",
            detail="Claude ready" if claude_connected else "Run claude auth login",
            auth_source="claude.ai" if claude_connected else None,
        ),
        "codex": ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=codex_connected,
            status="Connected" if codex_connected else "Needs login",
            detail="Codex ready" if codex_connected else "Run codex login",
            auth_source="chatgpt" if codex_connected else None,
        ),
    }


class SettingsTestApp(App):
    def __init__(self, settings: ForgeSettings) -> None:
        super().__init__()
        self._settings = settings
        self._provider_registry = build_provider_registry(settings)

    def on_mount(self) -> None:
        with patch(
            "forge.tui.screens.settings.collect_provider_connection_statuses",
            return_value=_statuses(),
        ):
            self.push_screen(SettingsScreen(".", self._settings, self._provider_registry))


@pytest.mark.asyncio
async def test_settings_screen_mounts(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    app = SettingsTestApp(ForgeSettings())

    async with app.run_test() as _pilot:
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        assert app._settings.planner_model == "claude:opus"
        assert app._settings.agent_model_low == "claude:sonnet"


@pytest.mark.asyncio
async def test_provider_change_updates_stage_model(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    app = SettingsTestApp(ForgeSettings())

    async with app.run_test() as pilot:
        provider_select = app.screen.query_one("#provider-planner_model", Select)
        provider_select.value = "openai"
        await pilot.pause()

        assert app._settings.planner_model.startswith("openai:")


@pytest.mark.asyncio
async def test_model_change_updates_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    app = SettingsTestApp(ForgeSettings())

    async with app.run_test() as pilot:
        provider_select = app.screen.query_one("#provider-reviewer_model", Select)
        provider_select.value = "openai"
        await pilot.pause()

        model_select = app.screen.query_one("#model-reviewer_model", Select)
        model_select.value = "gpt-5.4-mini"
        await pilot.pause()

        assert app._settings.reviewer_model == "openai:gpt-5.4-mini"


@pytest.mark.asyncio
async def test_effort_change_updates_reasoning_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    app = SettingsTestApp(ForgeSettings())

    async with app.run_test() as pilot:
        effort_select = app.screen.query_one("#effort-reviewer_model", Select)
        effort_select.value = "high"
        await pilot.pause()

        assert app._settings.reviewer_reasoning_effort == "high"


@pytest.mark.asyncio
async def test_connect_button_uses_suspend_context_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    app = SettingsTestApp(ForgeSettings())

    async with app.run_test() as pilot:
        screen = app.screen
        button = screen.query_one("#connect-claude", Button)

        with (
            patch.object(screen.app, "suspend", return_value=nullcontext()) as suspend_mock,
            patch(
                "forge.tui.screens.settings.collect_provider_connection_statuses",
                return_value=_statuses(),
            ),
            patch("forge.tui.screens.settings.subprocess.run") as subprocess_run,
        ):
            screen.on_button_pressed(Button.Pressed(button))
            await pilot.pause()

        suspend_mock.assert_called_once_with()
        subprocess_run.assert_called_once_with(
            ["claude", "auth", "login"],
            cwd=screen._project_dir,
            check=False,
        )
