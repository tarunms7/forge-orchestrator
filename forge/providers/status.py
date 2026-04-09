"""Provider connection status helpers for terminal and CLI setup flows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

_STATUS_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class ProviderConnectionStatus:
    """User-facing provider connection state."""

    ui_key: str
    provider_key: str
    display_name: str
    installed: bool
    connected: bool
    status: str
    detail: str
    auth_source: str | None = None
    command: str | None = None


def _run_status_command(argv: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_SECONDS,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _codex_auth_fallback_description() -> str | None:
    """Reuse OpenAI provider auth detection without a module-level import cycle."""
    try:
        from forge.providers.openai import _codex_auth_description

        return _codex_auth_description()
    except Exception:
        return None


def get_claude_connection_status() -> ProviderConnectionStatus:
    """Inspect Claude CLI installation and login state."""
    command = shutil.which("claude")
    if not command:
        return ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=False,
            connected=bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            status="Not installed",
            detail=(
                "Using ANTHROPIC_API_KEY"
                if os.environ.get("ANTHROPIC_API_KEY", "").strip()
                else "Claude CLI not found in PATH"
            ),
            auth_source="api_key" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else None,
            command=command,
        )

    returncode, stdout, stderr = _run_status_command([command, "auth", "status"])
    if returncode == 0 and stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("loggedIn"):
            email = str(payload.get("email", "")).strip()
            org_name = str(payload.get("orgName", "")).strip()
            subscription = str(payload.get("subscriptionType", "")).strip()
            auth_method = str(payload.get("authMethod", "")).strip() or "claude.ai"
            parts = [part for part in (email, org_name, subscription) if part]
            detail = " • ".join(parts) if parts else "Connected"
            return ProviderConnectionStatus(
                ui_key="claude",
                provider_key="claude",
                display_name="Claude",
                installed=True,
                connected=True,
                status="Connected",
                detail=detail,
                auth_source=auth_method,
                command=command,
            )

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return ProviderConnectionStatus(
            ui_key="claude",
            provider_key="claude",
            display_name="Claude",
            installed=True,
            connected=True,
            status="Connected",
            detail="ANTHROPIC_API_KEY configured",
            auth_source="api_key",
            command=command,
        )

    detail = stderr or stdout or "Run `claude auth login` to connect Claude."
    return ProviderConnectionStatus(
        ui_key="claude",
        provider_key="claude",
        display_name="Claude",
        installed=True,
        connected=False,
        status="Needs login",
        detail=detail,
        auth_source=None,
        command=command,
    )


def get_codex_connection_status() -> ProviderConnectionStatus:
    """Inspect Codex CLI installation and login state."""
    command = shutil.which("codex")
    codex_auth = _codex_auth_fallback_description()
    if not command:
        return ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=False,
            connected=bool(codex_auth),
            status="Not installed",
            detail=codex_auth or "Codex CLI not found in PATH",
            auth_source="api_key" if codex_auth and "key" in codex_auth.lower() else None,
            command=command,
        )

    returncode, stdout, stderr = _run_status_command([command, "login", "status"])
    detail = stdout or stderr
    if returncode == 0 and detail:
        normalized = detail.strip().lower()
        disconnected_markers = ("not logged in", "logged out", "no credentials", "sign in")
        if not any(marker in normalized for marker in disconnected_markers):
            return ProviderConnectionStatus(
                ui_key="codex",
                provider_key="openai",
                display_name="Codex",
                installed=True,
                connected=True,
                status="Connected",
                detail=detail.strip(),
                auth_source="chatgpt" if "chatgpt" in normalized else "codex",
                command=command,
            )

    if codex_auth:
        return ProviderConnectionStatus(
            ui_key="codex",
            provider_key="openai",
            display_name="Codex",
            installed=True,
            connected=True,
            status="Connected",
            detail=codex_auth,
            auth_source="api_key" if "key" in codex_auth.lower() else "codex",
            command=command,
        )

    return ProviderConnectionStatus(
        ui_key="codex",
        provider_key="openai",
        display_name="Codex",
        installed=True,
        connected=False,
        status="Needs login",
        detail=detail or "Run `codex login` to connect Codex.",
        auth_source=None,
        command=command,
    )


def collect_provider_connection_statuses() -> dict[str, ProviderConnectionStatus]:
    """Return the current Claude and Codex connection states."""
    claude = get_claude_connection_status()
    codex = get_codex_connection_status()
    return {
        claude.ui_key: claude,
        codex.ui_key: codex,
    }


def preferred_default_provider(
    statuses: dict[str, ProviderConnectionStatus] | None = None,
) -> str:
    """Choose the provider that should seed default routing."""
    statuses = statuses or collect_provider_connection_statuses()
    if statuses.get("claude") and statuses["claude"].connected:
        return "claude"
    if statuses.get("codex") and statuses["codex"].connected:
        return "openai"
    return "claude"
