"""Settings screen — displays current configuration."""

from __future__ import annotations

import os
import subprocess

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical
from textual.binding import Binding

_DISPLAY_GROUPS = {
    "Model": ["model_strategy"],
    "Agents": ["max_agents", "agent_timeout_seconds", "max_retries"],
    "Build & Test": ["build_cmd", "test_cmd"],
    "Budget": ["budget_limit_usd"],
    "Pipeline": ["pipeline_timeout_seconds", "require_approval", "contracts_required"],
    "Resources": ["cpu_threshold", "memory_threshold_pct", "disk_threshold_gb"],
    "Autonomy": ["autonomy", "question_limit", "question_timeout", "auto_pr"],
}


def format_settings(settings) -> str:
    """Format settings as Rich markup. Accepts any object with matching attributes."""
    lines = []
    for group_name, fields in _DISPLAY_GROUPS.items():
        lines.append(f"\n[bold #58a6ff]{group_name}[/]")
        for field in fields:
            value = getattr(settings, field, "?")
            env_var = f"FORGE_{field.upper()}"
            lines.append(f"  [#8b949e]{field}[/]: {value}  [dim]({env_var})[/dim]")
    return "\n".join(lines)


class SettingsScreen(Screen):
    """Settings display with $EDITOR launch."""

    DEFAULT_CSS = """
    SettingsScreen {
        layout: vertical;
    }
    #settings-header {
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #58a6ff;
    }
    #settings-body {
        padding: 1 2;
        overflow-y: auto;
    }
    #settings-footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("enter", "edit_config", "Edit config"),
    ]

    def __init__(self, settings) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        yield Static("[bold #58a6ff]SETTINGS[/]", id="settings-header")
        yield Static(format_settings(self._settings), id="settings-body")
        yield Static("[Enter] edit config with $EDITOR", id="settings-footer")

    def action_edit_config(self) -> None:
        editor = os.environ.get("EDITOR", "vim")
        config_path = os.path.join(os.getcwd(), ".forge", "config.toml")
        self.app.suspend()
        try:
            subprocess.run([editor, config_path])
        finally:
            self.app.resume()
