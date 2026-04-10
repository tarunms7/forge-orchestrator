"""Terminal-first settings screen for provider setup and advanced routing."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Select, Static

from forge.config.user_settings import (
    export_settings_snapshot,
    local_user_settings_path,
    save_local_user_settings,
)
from forge.core.provider_config import (
    default_routing_overrides_for_provider,
    ensure_routing_defaults,
    normalize_routing_settings,
)
from forge.providers.base import ModelSpec
from forge.providers.registry import ProviderRegistry
from forge.providers.status import (
    ProviderConnectionStatus,
    collect_provider_connection_statuses,
    preferred_default_provider,
)
from forge.tui.theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    BG_PANEL,
    BORDER_DEFAULT,
    BORDER_PANEL,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from forge.tui.widgets.shortcut_bar import ShortcutBar

_PROVIDER_LABELS = {
    "claude": "Claude",
    "openai": "Codex",
}

_PROVIDER_UI_TO_INTERNAL = {
    "claude": "claude",
    "codex": "openai",
}

_INTERNAL_TO_PROVIDER_UI = {
    "claude": "claude",
    "openai": "codex",
}

_TIER_ORDER = {
    "primary": 0,
    "supported": 1,
    "experimental": 2,
}

_STATUS_COLORS = {
    "Connected": ACCENT_GREEN,
    "Needs login": ACCENT_ORANGE,
    "Not installed": ACCENT_RED,
}

_EFFORT_OPTIONS = [
    ("Auto", "__auto__"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
]


@dataclass(frozen=True)
class StageRow:
    settings_attr: str
    label: str
    stage: str

    @property
    def provider_select_id(self) -> str:
        return f"provider-{self.settings_attr}"

    @property
    def model_select_id(self) -> str:
        return f"model-{self.settings_attr}"

    @property
    def effort_select_id(self) -> str:
        return f"effort-{self.settings_attr}"


_ROUTING_ROWS = [
    StageRow("planner_model", "Planner", "planner"),
    StageRow("contract_builder_model", "Contract Builder", "contract_builder"),
    StageRow("agent_model_low", "Agent Low", "agent"),
    StageRow("agent_model_medium", "Agent Medium", "agent"),
    StageRow("agent_model_high", "Agent High", "agent"),
    StageRow("reviewer_model", "Reviewer", "reviewer"),
    StageRow("ci_fix_model", "CI Fix", "ci_fix"),
]

_MODEL_TO_REASONING_ATTR = {
    "planner_model": "planner_reasoning_effort",
    "agent_model_low": "agent_model_low_reasoning_effort",
    "agent_model_medium": "agent_model_medium_reasoning_effort",
    "agent_model_high": "agent_model_high_reasoning_effort",
    "reviewer_model": "reviewer_reasoning_effort",
    "contract_builder_model": "contract_builder_reasoning_effort",
    "ci_fix_model": "ci_fix_reasoning_effort",
}


def _format_status(status: ProviderConnectionStatus) -> tuple[str, str]:
    color = _STATUS_COLORS.get(status.status, TEXT_SECONDARY)
    detail = status.detail or "Not configured"
    if status.auth_source:
        detail = f"{detail}\n[{TEXT_SECONDARY}]Auth:[/] {status.auth_source}"
    return f"[bold {color}]{status.status}[/]", detail


def _model_label(alias: str) -> str:
    return alias.replace("-", " ").upper() if alias.startswith("gpt-") else alias.title()


def _provider_options() -> list[tuple[str, str]]:
    return [
        ("Claude", "claude"),
        ("Codex", "openai"),
    ]


def _effort_display(value: str | None) -> str:
    return "__auto__" if value is None else value


class SettingsScreen(Screen):
    """Provider setup plus advanced per-stage routing."""

    DEFAULT_CSS = f"""
    SettingsScreen {{
        layout: vertical;
    }}
    #settings-header {{
        height: 4;
        padding: 1 2;
        background: {BG_PANEL};
        color: {ACCENT_BLUE};
        border-bottom: tall {BORDER_PANEL};
    }}
    #settings-body {{
        padding: 1 2;
        background: #0d1117;
        overflow-y: auto;
    }}
    .section-title {{
        margin: 1 0 1 0;
        color: {ACCENT_BLUE};
    }}
    #provider-grid {{
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }}
    .provider-card {{
        width: 1fr;
        height: auto;
        min-height: 11;
        margin-right: 1;
        padding: 1;
        border: tall {BORDER_PANEL};
        background: {BG_PANEL};
    }}
    .provider-card:last-child {{
        margin-right: 0;
    }}
    .provider-title {{
        color: {TEXT_PRIMARY};
        margin-bottom: 1;
    }}
    .provider-status {{
        margin-bottom: 1;
    }}
    .provider-detail {{
        color: {TEXT_SECONDARY};
        height: auto;
        min-height: 2;
    }}
    .provider-actions {{
        margin-top: 1;
        height: auto;
    }}
    .provider-actions Button {{
        margin-right: 1;
    }}
    .routing-help {{
        color: {TEXT_SECONDARY};
        margin-bottom: 1;
    }}
    #routing-table {{
        width: auto;
        height: auto;
    }}
    .routing-header {{
        width: auto;
        margin-bottom: 1;
    }}
    .routing-row {{
        width: auto;
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }}
    .routing-stage {{
        width: 18;
        color: {TEXT_PRIMARY};
    }}
    .routing-column-title {{
        color: {TEXT_MUTED};
    }}
    .routing-provider {{
        width: 14;
        margin-right: 1;
    }}
    .routing-model {{
        width: 24;
        margin-right: 1;
    }}
    .routing-effort {{
        width: 12;
    }}
    #settings-save-note {{
        margin-top: 1;
        color: {TEXT_MUTED};
    }}
    #settings-footer {{
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: {BG_PANEL};
        color: {TEXT_SECONDARY};
        border-top: tall {BORDER_DEFAULT};
    }}
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("r", "refresh_status", "Refresh", show=True),
    ]

    def __init__(self, project_dir: str, settings: Any, registry: ProviderRegistry) -> None:
        super().__init__()
        self._project_dir = os.path.abspath(project_dir)
        self._settings = settings
        self._registry = registry
        self._statuses = collect_provider_connection_statuses()
        self._preferred_provider = preferred_default_provider(self._statuses)
        ensure_routing_defaults(self._settings, self._preferred_provider)
        normalize_routing_settings(
            self._settings,
            self._registry,
            preferred_provider=self._preferred_provider,
        )
        self._suspend_events = False

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold {ACCENT_BLUE}]PROVIDERS & ROUTING[/]\n"
            f"[{TEXT_SECONDARY}]Connect Claude or Codex, then pick the exact model and effort used at every pipeline stage.[/]",
            id="settings-header",
        )
        with VerticalScroll(id="settings-body"):
            yield Static("[bold]Providers[/]", classes="section-title")
            with Horizontal(id="provider-grid"):
                for ui_key in ("claude", "codex"):
                    with Vertical(classes="provider-card", id=f"provider-card-{ui_key}"):
                        yield Static("", classes="provider-title", id=f"provider-title-{ui_key}")
                        yield Static("", classes="provider-status", id=f"provider-status-{ui_key}")
                        yield Static("", classes="provider-detail", id=f"provider-detail-{ui_key}")
                        with Horizontal(classes="provider-actions"):
                            yield Button("Connect", id=f"connect-{ui_key}")
                            yield Button("Recheck", id=f"recheck-{ui_key}")

            yield Static("[bold]Routing[/]", classes="section-title")
            yield Static(
                f"Changes save automatically to [bold]{local_user_settings_path()}[/]. "
                f"Claude stays the default when both providers are connected. Effort uses native controls on Codex and guidance on Claude.",
                classes="routing-help",
            )
            with Vertical(id="routing-table"):
                with Horizontal(classes="routing-header"):
                    yield Static("Stage", classes="routing-stage routing-column-title")
                    yield Static("Provider", classes="routing-provider routing-column-title")
                    yield Static("Model", classes="routing-model routing-column-title")
                    yield Static("Effort", classes="routing-effort routing-column-title")

                for row in _ROUTING_ROWS:
                    with Horizontal(classes="routing-row", id=f"row-{row.settings_attr}"):
                        yield Static(row.label, classes="routing-stage")
                        yield Select(
                            _provider_options(),
                            value=self._current_provider_value(row.settings_attr),
                            allow_blank=False,
                            compact=True,
                            classes="routing-provider",
                            id=row.provider_select_id,
                        )
                        yield Select(
                            self._model_options_for_attr(row.settings_attr),
                            value=self._current_model_value(row.settings_attr),
                            allow_blank=False,
                            compact=True,
                            classes="routing-model",
                            id=row.model_select_id,
                        )
                        yield Select(
                            _EFFORT_OPTIONS,
                            value=self._current_effort_value(row.settings_attr),
                            allow_blank=False,
                            compact=True,
                            classes="routing-effort",
                            id=row.effort_select_id,
                        )
            yield Static("", id="settings-save-note")

        yield Static(
            "[Tab] move  [Enter] open or apply  [R] refresh provider status  [Esc] close",
            id="settings-footer",
        )
        yield ShortcutBar(
            [
                ("Tab", "Next"),
                ("Enter", "Use"),
                ("R", "Refresh"),
                ("Esc", "Back"),
            ]
        )

    def on_mount(self) -> None:
        self._refresh_provider_cards()
        self._persist_settings(show_message=False)

    def _stage_row(self, settings_attr: str) -> StageRow:
        for row in _ROUTING_ROWS:
            if row.settings_attr == settings_attr:
                return row
        raise KeyError(settings_attr)

    def _current_provider_value(self, settings_attr: str) -> str:
        raw = getattr(self._settings, settings_attr, "") or ""
        try:
            return ModelSpec.parse(raw).provider
        except ValueError:
            return self._preferred_provider

    def _current_model_value(self, settings_attr: str) -> str:
        raw = getattr(self._settings, settings_attr, "") or ""
        try:
            return ModelSpec.parse(raw).model
        except ValueError:
            provider = self._current_provider_value(settings_attr)
            defaults = default_routing_overrides_for_provider(provider)
            return ModelSpec.parse(defaults[settings_attr]).model

    def _current_effort_value(self, settings_attr: str) -> str:
        value = getattr(self._settings, _MODEL_TO_REASONING_ATTR[settings_attr], None)
        return _effort_display(value)

    def _model_options_for_attr(self, settings_attr: str, provider: str | None = None) -> list[tuple[str, str]]:
        row = self._stage_row(settings_attr)
        target_provider = provider or self._current_provider_value(settings_attr)
        entries = []
        for entry in self._registry.all_catalog_entries():
            if target_provider == "claude" and entry.provider != "claude":
                continue
            if target_provider == "openai" and (
                entry.provider != "openai" or entry.backend != "codex-sdk"
            ):
                continue
            if row.stage not in entry.validated_stages:
                continue
            issues = self._registry.validate_model_for_stage(entry.spec, row.stage)
            if any(issue.startswith("BLOCKED:") for issue in issues):
                continue
            entries.append(entry)

        entries.sort(key=lambda entry: (_TIER_ORDER.get(entry.tier, 99), entry.alias))
        return [(_model_label(entry.alias), entry.alias) for entry in entries]

    def _refresh_provider_cards(self) -> None:
        for ui_key in ("claude", "codex"):
            status = self._statuses[ui_key]
            title = self.query_one(f"#provider-title-{ui_key}", Static)
            status_widget = self.query_one(f"#provider-status-{ui_key}", Static)
            detail = self.query_one(f"#provider-detail-{ui_key}", Static)
            title.update(f"[bold]{status.display_name}[/]")
            badge, detail_text = _format_status(status)
            status_widget.update(badge)
            detail.update(detail_text)

    def _set_save_message(self, message: str) -> None:
        self.query_one("#settings-save-note", Static).update(message)

    def _persist_settings(self, *, show_message: bool = True) -> None:
        self._settings.model_strategy = "auto"
        save_local_user_settings(export_settings_snapshot(self._settings))
        if show_message:
            self._set_save_message(
                f"[{TEXT_SECONDARY}]Saved locally.[/] [dim]{local_user_settings_path()}[/dim]"
            )

    def _refresh_model_select(self, settings_attr: str, provider: str) -> None:
        model_select = self.query_one(f"#{self._stage_row(settings_attr).model_select_id}", Select)
        options = self._model_options_for_attr(settings_attr, provider)
        if not options:
            return
        current_model = self._current_model_value(settings_attr)
        available_models = {value for _, value in options}
        defaults = default_routing_overrides_for_provider(provider)
        preferred_model = ModelSpec.parse(defaults[settings_attr]).model
        chosen_model = (
            current_model
            if current_model in available_models
            else preferred_model
            if preferred_model in available_models
            else options[0][1]
        )
        self._suspend_events = True
        try:
            model_select.set_options(options)
            model_select.value = chosen_model
        finally:
            self._suspend_events = False
        setattr(self._settings, settings_attr, f"{provider}:{chosen_model}")

    def _refresh_all_statuses(self) -> None:
        self._statuses = collect_provider_connection_statuses()
        self._preferred_provider = preferred_default_provider(self._statuses)
        self._refresh_provider_cards()
        self._set_save_message(f"[{TEXT_SECONDARY}]Provider status refreshed.[/]")

    def action_refresh_status(self) -> None:
        self._refresh_all_statuses()

    def action_close(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("recheck-"):
            self._refresh_all_statuses()
            return
        if not button_id.startswith("connect-"):
            return

        ui_key = button_id.split("-", 1)[1]
        command = ["claude", "auth", "login"] if ui_key == "claude" else ["codex", "login"]
        status = self._statuses.get(ui_key)
        if status is not None and not status.installed:
            self._set_save_message(
                f"[{ACCENT_RED}]Install the {status.display_name} CLI first, then recheck.[/]"
            )
            return
        try:
            try:
                with self.app.suspend():
                    subprocess.run(command, cwd=self._project_dir, check=False)
            except SuspendNotSupported:
                subprocess.run(command, cwd=self._project_dir, check=False)
        except Exception as exc:
            self._set_save_message(f"[{ACCENT_RED}]Failed to launch login:[/] {exc}")
        self._refresh_all_statuses()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._suspend_events or event.select.id is None:
            return
        control_id = event.select.id
        if control_id.startswith("provider-"):
            settings_attr = control_id[len("provider-") :]
            provider = str(event.value)
            self._refresh_model_select(settings_attr, provider)
            self._persist_settings()
            return

        if control_id.startswith("model-"):
            settings_attr = control_id[len("model-") :]
            provider = self._current_provider_value(settings_attr)
            setattr(self._settings, settings_attr, f"{provider}:{event.value}")
            self._persist_settings()
            return

        if control_id.startswith("effort-"):
            settings_attr = control_id[len("effort-") :]
            effort_attr = _MODEL_TO_REASONING_ATTR[settings_attr]
            effort_value = None if str(event.value) == "__auto__" else str(event.value)
            setattr(self._settings, effort_attr, effort_value)
            self._persist_settings()
