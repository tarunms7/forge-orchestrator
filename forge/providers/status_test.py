"""Tests for provider connection status helpers."""

from __future__ import annotations

from unittest.mock import patch

from forge.providers.status import (
    ProviderConnectionStatus,
    collect_provider_connection_statuses,
    get_claude_connection_status,
    get_codex_connection_status,
    preferred_default_provider,
)


def test_get_claude_connection_status_parses_logged_in_json():
    with (
        patch("forge.providers.status.shutil.which", return_value="/usr/bin/claude"),
        patch(
            "forge.providers.status._run_status_command",
            return_value=(
                0,
                (
                    '{"loggedIn": true, "authMethod": "claude.ai", '
                    '"email": "dev@example.com", "orgName": "Forge", '
                    '"subscriptionType": "team"}'
                ),
                "",
            ),
        ),
    ):
        status = get_claude_connection_status()

    assert status.installed is True
    assert status.connected is True
    assert status.status == "Connected"
    assert status.auth_source == "claude.ai"
    assert "dev@example.com" in status.detail


def test_get_codex_connection_status_parses_cli_status():
    with (
        patch("forge.providers.status.shutil.which", return_value="/usr/bin/codex"),
        patch(
            "forge.providers.status._run_status_command",
            return_value=(0, "Logged in using ChatGPT", ""),
        ),
    ):
        status = get_codex_connection_status()

    assert status.installed is True
    assert status.connected is True
    assert status.status == "Connected"
    assert "ChatGPT" in status.detail


def test_collect_provider_connection_statuses_returns_both():
    with (
        patch("forge.providers.status.get_claude_connection_status") as claude,
        patch("forge.providers.status.get_codex_connection_status") as codex,
    ):
        claude.return_value = ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=True,
            status="Connected",
            detail="Ready",
        )
        codex.return_value = ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=False,
            status="Needs login",
            detail="Run codex login",
        )

        statuses = collect_provider_connection_statuses()

    assert set(statuses.keys()) == {"claude", "codex"}
    assert statuses["claude"].connected is True
    assert statuses["codex"].connected is False


def test_preferred_default_provider_prefers_claude_when_both_connected():
    statuses = {
        "claude": ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=True,
            status="Connected",
            detail="Ready",
        ),
        "codex": ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=True,
            status="Connected",
            detail="Ready",
        ),
    }

    assert preferred_default_provider(statuses) == "claude"


def test_preferred_default_provider_falls_back_to_codex_when_claude_missing():
    statuses = {
        "claude": ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=False,
            connected=False,
            status="Not installed",
            detail="Missing",
        ),
        "codex": ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=True,
            status="Connected",
            detail="Ready",
        ),
    }

    assert preferred_default_provider(statuses) == "openai"
