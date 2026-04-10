"""OpenAI provider — wraps Codex SDK and Agents SDK into the ProviderProtocol interface.

Supports two backends:
- codex-sdk: Full coding mode with shell/file tools (gpt-5.4 family)
- openai-agents-sdk: Intelligence mode via Responses API (o3)

Both SDKs are lazily imported and degrade gracefully if not installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from forge.providers._stream_text import drain_stream_text as _drain_stream_text
from forge.providers.base import (
    CatalogEntry,
    EventKind,
    ExecutionHandle,
    ExecutionMode,
    MCPServerConfig,
    OutputContract,
    ProviderEvent,
    ProviderHealthStatus,
    ProviderResult,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.catalog import CODEX_TOOL_MAP, FORGE_MODEL_CATALOG
from forge.providers.status import get_codex_connection_status

logger = logging.getLogger("forge.providers.openai")


def _default_reasoning_effort(
    execution_mode: ExecutionMode,
) -> Literal["low", "medium", "high"]:
    return "high" if execution_mode == ExecutionMode.INTELLIGENCE else "medium"


# ---------------------------------------------------------------------------
# Lazy SDK imports
# ---------------------------------------------------------------------------


def _try_import_codex() -> Any | None:
    """Lazily import the OpenAI Codex SDK. Returns module or None."""
    try:
        import openai_codex_sdk

        return openai_codex_sdk
    except ImportError:
        return None


def _try_import_agents() -> Any | None:
    """Lazily import the OpenAI Agents SDK. Returns module or None."""
    try:
        import agents

        return agents
    except ImportError:
        return None


def _codex_home() -> Path:
    """Resolve the Codex home directory used by CLI auth and model cache."""
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def _read_json_file(path: Path) -> Any | None:
    """Best-effort JSON reader used for local Codex metadata."""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception:
        logger.debug("Failed to read JSON file %s", path, exc_info=True)
        return None


def _codex_auth_payload() -> dict[str, Any] | None:
    """Load Codex auth metadata without depending on the CLI binary."""
    data = _read_json_file(_codex_home() / "auth.json")
    return data if isinstance(data, dict) else None


def _codex_auth_description() -> str | None:
    """Describe the currently available Codex auth source, if any."""
    if os.environ.get("CODEX_API_KEY"):
        return "Codex API key configured"

    payload = _codex_auth_payload()
    if not payload:
        return None

    auth_mode = str(payload.get("auth_mode", "")).strip().lower()
    tokens = payload.get("tokens") or {}
    has_session = isinstance(tokens, dict) and bool(tokens.get("access_token"))
    embedded_api_key = payload.get("OPENAI_API_KEY")

    if auth_mode == "chatgpt" and has_session:
        return "Codex ChatGPT subscription login configured"
    if auth_mode in {"api_key", "apikey"} and (embedded_api_key or has_session):
        return "Codex CLI API-key auth configured"
    if has_session or embedded_api_key:
        return "Codex CLI auth configured"
    return None


def _has_codex_auth() -> bool:
    """Return True when Codex execution can authenticate without more setup."""
    return _codex_auth_description() is not None


def _codex_models_cache() -> list[dict[str, Any]] | None:
    """Load the local Codex models cache if available."""
    data = _read_json_file(_codex_home() / "models_cache.json")
    if not isinstance(data, dict):
        return None
    models = data.get("models")
    if not isinstance(models, list):
        return None
    return [model for model in models if isinstance(model, dict)]


def _available_codex_model_aliases() -> set[str] | None:
    """Return model aliases exposed by the local Codex subscription cache."""
    models = _codex_models_cache()
    if models is None:
        return None

    aliases: set[str] = set()
    for model in models:
        slug = model.get("slug")
        if isinstance(slug, str) and slug.strip():
            aliases.add(slug.strip().lower())
            continue
        display_name = model.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            aliases.add(display_name.strip().lower())
    return aliases


def _codex_execution_model(catalog_entry: CatalogEntry) -> str:
    """Use subscription-compatible aliases when invoking the Codex CLI backend."""
    return catalog_entry.alias


def _codex_cli_path() -> str | None:
    """Prefer the user's installed Codex CLI so Forge shares the same auth/session model."""
    override = os.environ.get("FORGE_CODEX_PATH", "").strip()
    if override:
        return override
    return shutil.which("codex")


def _codex_api_key_override() -> str | None:
    """Only pass an API key to Codex when CLI auth is unavailable or explicitly set."""
    explicit = os.environ.get("CODEX_API_KEY")
    if explicit:
        return explicit
    if _has_codex_auth():
        return None
    fallback = os.environ.get("OPENAI_API_KEY", "").strip()
    return fallback or None


# ---------------------------------------------------------------------------
# Forge denied_operations -> developer message instructions
# ---------------------------------------------------------------------------

# Git operations translated to natural-language instructions for the Codex sandbox
_DENIED_OP_INSTRUCTIONS: dict[str, str] = {
    "git:push": "Do NOT run 'git push'.",
    "git:rebase": "Do NOT run 'git rebase'.",
    "git:checkout": "Do NOT run 'git checkout'.",
    "git:reset_hard": "Do NOT run 'git reset --hard'.",
    "git:branch_delete": "Do NOT run 'git branch -D' or 'git branch -d'.",
    "git:merge": "Do NOT run 'git merge'.",
    "git:clean": "Do NOT run 'git clean'.",
    "git:stash": "Do NOT run 'git stash'.",
    "git:cherry_pick": "Do NOT run 'git cherry-pick'.",
    "git:tag": "Do NOT run 'git tag'.",
    "git:remote": "Do NOT run 'git remote'.",
    "net:curl": "Do NOT run 'curl'.",
    "net:wget": "Do NOT run 'wget'.",
    "net:ssh": "Do NOT run 'ssh'.",
    "net:scp": "Do NOT run 'scp'.",
    "net:rsync": "Do NOT run 'rsync'.",
    "net:nc": "Do NOT run 'nc' or 'ncat'.",
    "net:telnet": "Do NOT run 'telnet'.",
    "net:ftp": "Do NOT run 'ftp'.",
    "priv:sudo": "Do NOT run 'sudo'.",
    "priv:su": "Do NOT run 'su'.",
    "priv:doas": "Do NOT run 'doas'.",
    "perm:chmod": "Do NOT run 'chmod'.",
    "perm:chown": "Do NOT run 'chown'.",
    "perm:chgrp": "Do NOT run 'chgrp'.",
    "proc:kill": "Do NOT run 'kill'.",
    "proc:pkill": "Do NOT run 'pkill'.",
    "proc:killall": "Do NOT run 'killall'.",
    "container:docker": "Do NOT run 'docker'.",
    "container:podman": "Do NOT run 'podman'.",
    "sys:systemctl": "Do NOT run 'systemctl'.",
    "sys:service": "Do NOT run 'service'.",
    "sys:mount": "Do NOT run 'mount'.",
    "sys:umount": "Do NOT run 'umount'.",
    "env:export": "Do NOT run 'export'.",
    "env:unset": "Do NOT run 'unset'.",
    "file:read_dotenv": "Do NOT read .env files.",
}


def _translate_denied_to_instructions(denied_ops: list[str]) -> str:
    """Convert Forge denied_operations to a developer message instruction block."""
    lines: list[str] = []
    for op in denied_ops:
        instruction = _DENIED_OP_INSTRUCTIONS.get(op)
        if instruction:
            lines.append(instruction)
    if not lines:
        return ""
    return "SAFETY RESTRICTIONS:\n" + "\n".join(lines)


def _translate_allowlist_to_instructions(allowed_tools: list[str]) -> str:
    """Convert an allowlist ToolPolicy into human-readable Codex instructions."""
    if not allowed_tools:
        return (
            "TOOL ACCESS:\n"
            "Do NOT use shell commands, file editing tools, MCP tools, or web search.\n"
            "Answer directly from the prompt and any supplied context."
        )
    allowed = ", ".join(allowed_tools)
    return (
        "TOOL ACCESS:\n"
        f"Only use these Forge-approved tools if needed: {allowed}.\n"
        "Treat every other tool or external capability as unavailable unless the system "
        "prompt explicitly says otherwise."
    )


def _build_codex_policy_instructions(tool_policy: ToolPolicy) -> str:
    """Translate ToolPolicy into best-effort Codex instructions."""
    sections: list[str] = []
    if tool_policy.mode == "allowlist":
        sections.append(_translate_allowlist_to_instructions(tool_policy.allowed_tools))
    if tool_policy.denied_operations:
        denied = _translate_denied_to_instructions(tool_policy.denied_operations)
        if denied:
            sections.append(denied)
    return "\n\n".join(section for section in sections if section)


# ---------------------------------------------------------------------------
# Tool name normalization
# ---------------------------------------------------------------------------


def _normalize_codex_tool(raw_name: str) -> str:
    """Map a Codex SDK tool/event name to its CoreTool string."""
    core = CODEX_TOOL_MAP.get(raw_name)
    if core is not None:
        return core.value
    return raw_name


def _build_codex_input(system_prompt: str, prompt: str) -> str:
    """Compose system instructions + user task for SDKs without a separate instructions field."""
    if not system_prompt:
        return prompt
    return f"{system_prompt}\n\nUSER TASK:\n{prompt}"


# ---------------------------------------------------------------------------
# _CodexExecutionHandle
# ---------------------------------------------------------------------------


class _CodexExecutionHandle(ExecutionHandle):
    """Wraps an async Codex SDK execution into an ExecutionHandle."""

    def __init__(
        self,
        task: asyncio.Task[ProviderResult],
        catalog_entry: CatalogEntry,
    ) -> None:
        self._task = task
        self._catalog_entry = catalog_entry
        self._result: ProviderResult | None = None

    @property
    def is_running(self) -> bool:
        return not self._task.done()

    async def abort(self) -> None:
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def result(self) -> ProviderResult:
        if self._result is not None:
            return self._result
        self._result = await self._task
        return self._result


# ---------------------------------------------------------------------------
# _AgentsExecutionHandle
# ---------------------------------------------------------------------------


class _AgentsExecutionHandle(ExecutionHandle):
    """Wraps an async Agents SDK execution into an ExecutionHandle."""

    def __init__(
        self,
        task: asyncio.Task[ProviderResult],
        catalog_entry: CatalogEntry,
    ) -> None:
        self._task = task
        self._catalog_entry = catalog_entry
        self._result: ProviderResult | None = None

    @property
    def is_running(self) -> bool:
        return not self._task.done()

    async def abort(self) -> None:
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def result(self) -> ProviderResult:
        if self._result is not None:
            return self._result
        self._result = await self._task
        return self._result


# ---------------------------------------------------------------------------
# Codex event conversion
# ---------------------------------------------------------------------------


def _convert_codex_event(event: Any) -> ProviderEvent | None:
    """Convert a Codex SDK streaming event to a ProviderEvent.

    Codex events use an item/turn structure:
    - item.completed with type=agent_message -> TEXT
    - item.started with type=command_execution/file_change -> TOOL_USE
    - turn.completed -> STATUS completed
    - turn.failed / error -> ERROR
    """
    event_type = getattr(event, "type", None) or (
        event.get("type") if isinstance(event, dict) else None
    )
    if event_type is None:
        return None

    # Normalize dict-style events to attribute access
    def _get(key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    item = _get("item", {})
    if isinstance(item, dict):
        item_type = item.get("type", "")
        item_text = item.get("text", "")
        item_content = item.get("content", item_text)
        item_id = item.get("id", "")
        item_command = item.get("command", "")
        item_output = item.get("aggregated_output", "")
        item_changes = item.get("changes", [])
        item_tool = item.get("tool", "")
        item_server = item.get("server", "")
        item_args = item.get("arguments")
        item_result = item.get("result")
        item_error = item.get("error")
    else:
        item_type = getattr(item, "type", "")
        item_text = getattr(item, "text", "")
        item_content = getattr(item, "content", item_text)
        item_id = getattr(item, "id", "")
        item_command = getattr(item, "command", "")
        item_output = getattr(item, "aggregated_output", "")
        item_changes = getattr(item, "changes", [])
        item_tool = getattr(item, "tool", "")
        item_server = getattr(item, "server", "")
        item_args = getattr(item, "arguments", None)
        item_result = getattr(item, "result", None)
        item_error = getattr(item, "error", None)

    if event_type == "item.completed" and item_type == "agent_message":
        text = item_content if isinstance(item_content, str) else str(item_content)
        return ProviderEvent(kind=EventKind.TEXT, text=text, raw=event)

    if event_type == "turn.started":
        return ProviderEvent(kind=EventKind.STATUS, status="thinking", raw=event)

    if event_type == "item.started" and item_type == "agent_message":
        return ProviderEvent(kind=EventKind.STATUS, status="typing", raw=event)

    if event_type == "item.started" and item_type in CODEX_TOOL_MAP:
        tool_input: str | None
        if item_type == "command_execution":
            command = item_command or (str(item_content) if item_content else None)
            tool_input = json.dumps({"command": command}, default=str) if command else None
        elif item_type == "mcp_tool_call":
            tool_input = json.dumps(
                {
                    "server": item_server,
                    "tool": item_tool,
                    "arguments": item_args,
                },
                default=str,
            )
        elif item_content:
            if item_type in {"file_read", "file_write", "file_change"} and isinstance(
                item_content, str
            ):
                tool_input = json.dumps({"file_path": item_content}, default=str)
            else:
                tool_input = (
                    json.dumps(item_content, default=str)
                    if isinstance(item_content, dict)
                    else str(item_content)
                )
        elif item_changes:
            tool_input = json.dumps(
                [
                    {
                        "path": getattr(change, "path", None)
                        if not isinstance(change, dict)
                        else change.get("path"),
                        "kind": getattr(change, "kind", None)
                        if not isinstance(change, dict)
                        else change.get("kind"),
                    }
                    for change in item_changes
                ]
            )
        else:
            tool_input = None
        return ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name=_normalize_codex_tool(item_type),
            tool_call_id=str(item_id) if item_id else str(uuid.uuid4()),
            tool_input=tool_input,
            raw=event,
        )

    if event_type == "item.completed" and item_type in CODEX_TOOL_MAP:
        output = ""
        if item_type == "command_execution":
            if item_output:
                output = item_output
            elif isinstance(item_content, dict):
                output = item_content.get("output", "")
        elif item_type == "mcp_tool_call":
            if item_error:
                output = (
                    item_error.get("message", "")
                    if isinstance(item_error, dict)
                    else getattr(item_error, "message", "")
                )
            elif item_result:
                output = json.dumps(item_result, default=str)
        elif isinstance(item_content, dict):
            output = item_content.get("output", "")
        elif isinstance(item_content, str):
            output = item_content
        elif item_changes:
            output = json.dumps(
                [
                    {
                        "path": getattr(change, "path", None)
                        if not isinstance(change, dict)
                        else change.get("path"),
                        "kind": getattr(change, "kind", None)
                        if not isinstance(change, dict)
                        else change.get("kind"),
                    }
                    for change in item_changes
                ]
            )
        return ProviderEvent(
            kind=EventKind.TOOL_RESULT,
            tool_name=_normalize_codex_tool(item_type),
            tool_call_id=str(item_id) if item_id else None,
            tool_output=str(output),
            is_tool_error=False,
            raw=event,
        )

    if event_type == "turn.completed":
        usage = _get("usage", {})
        if isinstance(usage, dict) and usage:
            return ProviderEvent(
                kind=EventKind.USAGE,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                raw=event,
            )
        if usage:
            return ProviderEvent(
                kind=EventKind.USAGE,
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                raw=event,
            )
        return ProviderEvent(kind=EventKind.STATUS, status="completed", raw=event)

    if event_type in ("turn.failed", "error"):
        error = _get("error", "")
        error_msg = _get("message", "") or getattr(error, "message", "") or error or str(event)
        return ProviderEvent(kind=EventKind.ERROR, text=str(error_msg), raw=event)

    return None


# ---------------------------------------------------------------------------
# Agents SDK event conversion
# ---------------------------------------------------------------------------


def _convert_agents_event(event: Any) -> ProviderEvent | None:
    """Convert an Agents SDK streaming event to a ProviderEvent."""
    event_type = getattr(event, "type", None) or (
        event.get("type") if isinstance(event, dict) else None
    )
    if event_type is None:
        return None

    def _get(key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    if event_type == "response.text.delta":
        text = _get("delta", "")
        return ProviderEvent(kind=EventKind.TEXT, text=str(text), raw=event)

    if event_type in {"response.created", "response.in_progress"}:
        return ProviderEvent(kind=EventKind.STATUS, status="thinking", raw=event)

    if event_type == "response.completed":
        usage = _get("usage", {})
        if isinstance(usage, dict) and usage:
            return ProviderEvent(
                kind=EventKind.USAGE,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                raw=event,
            )
        return ProviderEvent(kind=EventKind.STATUS, status="completed", raw=event)

    if event_type in ("error", "response.failed"):
        error_msg = _get("message", "") or _get("error", "") or str(event)
        return ProviderEvent(kind=EventKind.ERROR, text=str(error_msg), raw=event)

    return None


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class OpenAIProvider:
    """ProviderProtocol implementation for OpenAI via Codex SDK and Agents SDK."""

    @property
    def name(self) -> str:
        return "openai"

    def catalog_entries(self) -> list[CatalogEntry]:
        """Return OpenAI entries filtered to the locally executable subset when known."""
        codex_aliases = _available_codex_model_aliases()
        has_agents_auth = bool(os.environ.get("OPENAI_API_KEY", "").strip())

        entries: list[CatalogEntry] = []
        for entry in FORGE_MODEL_CATALOG:
            if entry.provider != "openai":
                continue
            if entry.backend == "codex-sdk" and codex_aliases is not None:
                if entry.alias.lower() not in codex_aliases:
                    continue
            if entry.backend == "openai-agents-sdk" and not has_agents_auth:
                continue
            entries.append(entry)
        return entries

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        """Check OpenAI backend availability for the requested auth mode(s)."""
        errors: list[str] = []
        details_parts: list[str] = []

        backends: set[str]
        if backend is not None:
            backends = {backend}
        else:
            backends = {entry.backend for entry in self.catalog_entries()} or {"codex-sdk"}

        if "codex-sdk" in backends:
            codex = _try_import_codex()
            if codex is not None:
                details_parts.append(f"codex-sdk v{getattr(codex, '__version__', 'unknown')}")
            else:
                errors.append("openai-codex-sdk not installed")
            codex_status = get_codex_connection_status()
            if codex_status.connected:
                details_parts.append(codex_status.detail)
            elif _codex_api_key_override():
                details_parts.append("OPENAI_API_KEY fallback configured for Codex")
            else:
                errors.append(codex_status.detail or "Codex auth not configured")

        if "openai-agents-sdk" in backends:
            agents = _try_import_agents()
            if agents is not None:
                details_parts.append(f"agents-sdk v{getattr(agents, '__version__', 'unknown')}")
            else:
                errors.append("openai-agents not installed")
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if api_key:
                details_parts.append("Responses API key configured")
            else:
                errors.append(
                    "OPENAI_API_KEY environment variable not set for Responses API models"
                )

        return ProviderHealthStatus(
            healthy=len(errors) == 0,
            provider="openai",
            details=", ".join(details_parts) if details_parts else "no SDKs available",
            errors=errors,
        )

    def start(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        execution_mode: ExecutionMode,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        workspace: WorkspaceRoots,
        max_turns: int,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        resume_state: ResumeState | None = None,
        on_event: Callable[[ProviderEvent], None] | None = None,
    ) -> ExecutionHandle:
        """Start an OpenAI execution. Routes to codex-sdk or agents-sdk based on catalog_entry."""
        if catalog_entry.backend == "codex-sdk":
            return self._start_codex(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                execution_mode=execution_mode,
                tool_policy=tool_policy,
                workspace=workspace,
                max_turns=max_turns,
                reasoning_effort=reasoning_effort,
                resume_state=resume_state,
                on_event=on_event,
            )
        elif catalog_entry.backend == "openai-agents-sdk":
            return self._start_agents(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=max_turns,
                reasoning_effort=reasoning_effort,
                mcp_servers=mcp_servers,
                on_event=on_event,
            )
        else:
            raise ValueError(f"Unknown backend for OpenAI: {catalog_entry.backend}")

    def can_resume(self, state: ResumeState) -> bool:
        """Check if a Codex thread still exists and is resumable."""
        return (
            state.provider == "openai"
            and state.backend == "codex-sdk"
            and bool(state.session_token)
            and state.is_resumable
        )

    def cleanup_session(self, state: ResumeState) -> None:
        """Clean up a completed Codex thread."""
        if state.backend != "codex-sdk" or not state.session_token:
            return
        # Thread cleanup would call codex.deleteThread(state.session_token)
        # but since the SDK may not be installed, we do best-effort
        codex = _try_import_codex()
        if codex is None:
            return
        try:
            delete_fn = getattr(codex, "deleteThread", None) or getattr(
                codex, "delete_thread", None
            )
            if delete_fn:
                delete_fn(state.session_token)
        except Exception:
            logger.debug("Failed to delete Codex thread %s", state.session_token)

    # ── Codex backend ───────────────────────────────────────────────────

    @staticmethod
    def _build_codex_thread_options(
        catalog_entry: CatalogEntry,
        workspace: WorkspaceRoots,
        execution_mode: ExecutionMode,
        reasoning_effort: Literal["low", "medium", "high"] | None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "model": _codex_execution_model(catalog_entry),
            "sandboxMode": "danger-full-access",
            "workingDirectory": workspace.primary_cwd,
            "approvalPolicy": "never",
            "skipGitRepoCheck": True,
            "networkAccessEnabled": True,
            "webSearchEnabled": True,
            "modelReasoningEffort": reasoning_effort or _default_reasoning_effort(execution_mode),
        }
        return options

    def _start_codex(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        execution_mode: ExecutionMode,
        tool_policy: ToolPolicy,
        workspace: WorkspaceRoots,
        max_turns: int,
        reasoning_effort: Literal["low", "medium", "high"] | None,
        resume_state: ResumeState | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> _CodexExecutionHandle:
        """Launch a Codex SDK execution."""
        task = asyncio.ensure_future(
            self._run_codex(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                execution_mode=execution_mode,
                tool_policy=tool_policy,
                workspace=workspace,
                max_turns=max_turns,
                reasoning_effort=reasoning_effort,
                resume_state=resume_state,
                on_event=on_event,
            )
        )
        return _CodexExecutionHandle(task, catalog_entry)

    async def _run_codex(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        execution_mode: ExecutionMode,
        tool_policy: ToolPolicy,
        workspace: WorkspaceRoots,
        max_turns: int,
        reasoning_effort: Literal["low", "medium", "high"] | None,
        resume_state: ResumeState | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> ProviderResult:
        """Execute via Codex SDK, streaming events."""
        start_time = time.monotonic()
        total_input_tokens = 0
        total_output_tokens = 0
        final_text_parts: list[str] = []
        session_token: str | None = None
        thread: Any | None = None

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="started"))

        try:
            codex = _try_import_codex()
            if codex is None:
                raise ImportError("openai-codex-sdk is not installed")
            # Best-effort stage safety and tool guidance for Codex.
            policy_instructions = _build_codex_policy_instructions(tool_policy)

            full_system = system_prompt
            if policy_instructions:
                full_system = (
                    f"{system_prompt}\n\n{policy_instructions}"
                    if system_prompt
                    else policy_instructions
                )

            # Configure Codex options
            codex_config = {
                "model": _codex_execution_model(catalog_entry),
                "prompt": prompt,
                "instructions": full_system,
                "sandbox_mode": "danger-full-access",
                "cwd": workspace.primary_cwd,
                "max_turns": max_turns,
                "stream": True,
                "approval_policy": "never",
                "skip_git_repo_check": True,
                "network_access_enabled": True,
                "web_search_enabled": True,
                "model_reasoning_effort": reasoning_effort
                or _default_reasoning_effort(execution_mode),
            }

            # Resume vs new thread
            start_fn = getattr(codex, "startThread", None) or getattr(codex, "start_thread", None)
            resume_fn = getattr(codex, "resumeThread", None) or getattr(
                codex, "resume_thread", None
            )

            # Legacy SDK path: module-level start_thread/resume_thread
            if start_fn or resume_fn:
                if resume_state and resume_state.session_token:
                    if resume_fn:
                        stream = resume_fn(resume_state.session_token, **codex_config)
                    else:
                        raise RuntimeError("Codex SDK does not support thread resume")
                else:
                    if start_fn:
                        stream = start_fn(**codex_config)
                    else:
                        raise RuntimeError("Codex SDK start function not found")
            else:
                # Current SDK path: instantiate Codex client, create Thread, then run_streamed().
                CodexClient = getattr(codex, "Codex", None)
                if CodexClient is None:
                    raise RuntimeError("Codex SDK start function not found")

                api_key = _codex_api_key_override()
                client_options: dict[str, Any] = {}
                if api_key:
                    client_options["apiKey"] = api_key
                codex_path = _codex_cli_path()
                if codex_path:
                    client_options["codexPathOverride"] = codex_path

                client = CodexClient(client_options or None)
                thread_options = self._build_codex_thread_options(
                    catalog_entry,
                    workspace,
                    execution_mode,
                    reasoning_effort,
                )

                if resume_state and resume_state.session_token:
                    thread_start = getattr(client, "resumeThread", None) or getattr(
                        client, "resume_thread", None
                    )
                    if thread_start is None:
                        raise RuntimeError("Codex SDK does not support thread resume")
                    thread = thread_start(resume_state.session_token, thread_options)
                else:
                    thread_start = getattr(client, "startThread", None) or getattr(
                        client, "start_thread", None
                    )
                    if thread_start is None:
                        raise RuntimeError("Codex SDK start function not found")
                    thread = thread_start(thread_options)

                run_streamed = getattr(thread, "runStreamed", None) or getattr(
                    thread, "run_streamed", None
                )
                if run_streamed is None:
                    raise RuntimeError("Codex SDK thread.run_streamed not found")

                streamed_turn = await run_streamed(_build_codex_input(full_system, prompt))
                stream = streamed_turn.events

            # Consume the event stream
            async for event in stream:
                provider_event = _convert_codex_event(event)
                if provider_event is None:
                    continue

                # Track usage
                if provider_event.kind == EventKind.USAGE:
                    total_input_tokens += provider_event.input_tokens or 0
                    total_output_tokens += provider_event.output_tokens or 0

                # Track text
                if provider_event.kind == EventKind.TEXT and provider_event.text:
                    text = provider_event.text.strip()
                    if text:
                        final_text_parts.append(text)

                if on_event:
                    on_event(provider_event)

                # Extract session token from stream metadata
                event_session = None
                if isinstance(event, dict):
                    event_session = event.get("thread_id")
                else:
                    event_session = getattr(event, "thread_id", None)
                if event_session:
                    session_token = event_session
                elif thread is not None:
                    session_token = getattr(thread, "id", None) or session_token

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            if on_event:
                on_event(ProviderEvent(kind=EventKind.ERROR, text=str(exc)))
                on_event(ProviderEvent(kind=EventKind.STATUS, status="failed"))
            return ProviderResult(
                text=str(exc),
                is_error=True,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                resume_state=None,
                duration_ms=elapsed_ms,
                provider_reported_cost_usd=None,
                model_canonical_id=catalog_entry.canonical_id,
            )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Build resume state if we got a session token
        resume = None
        if session_token:
            now_iso = datetime.now(UTC).isoformat()
            resume = ResumeState(
                provider="openai",
                backend="codex-sdk",
                session_token=session_token,
                created_at=now_iso,
                last_active_at=now_iso,
                turn_count=max_turns,
                is_resumable=True,
            )

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="completed"))

        return ProviderResult(
            text="\n\n".join(final_text_parts),
            is_error=False,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            resume_state=resume,
            duration_ms=elapsed_ms,
            provider_reported_cost_usd=None,  # OpenAI doesn't report cost directly
            model_canonical_id=catalog_entry.canonical_id,
        )

    # ── Agents SDK backend ──────────────────────────────────────────────

    def _start_agents(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        workspace: WorkspaceRoots,
        max_turns: int,
        reasoning_effort: Literal["low", "medium", "high"] | None,
        mcp_servers: list[MCPServerConfig] | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> _AgentsExecutionHandle:
        """Launch an Agents SDK execution."""
        task = asyncio.ensure_future(
            self._run_agents(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=max_turns,
                reasoning_effort=reasoning_effort,
                mcp_servers=mcp_servers,
                on_event=on_event,
            )
        )
        return _AgentsExecutionHandle(task, catalog_entry)

    async def _run_agents(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        workspace: WorkspaceRoots,
        max_turns: int,
        reasoning_effort: Literal["low", "medium", "high"] | None,
        mcp_servers: list[MCPServerConfig] | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> ProviderResult:
        """Execute via Agents SDK (Responses API), streaming events."""
        start_time = time.monotonic()
        total_input_tokens = 0
        total_output_tokens = 0
        final_text_parts: list[str] = []
        partial_text_buffer = ""

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="started"))

        def _emit_partial_fragments(*, force: bool = False, raw: Any | None = None) -> None:
            nonlocal partial_text_buffer
            fragments, partial_text_buffer = _drain_stream_text(
                partial_text_buffer,
                force=force,
            )
            for fragment in fragments:
                final_text_parts.append(fragment)
                if on_event:
                    on_event(ProviderEvent(kind=EventKind.TEXT, text=fragment, raw=raw))

        try:
            agents = _try_import_agents()
            if agents is None:
                raise ImportError("openai-agents is not installed")

            # Build agent configuration
            agent_config: dict[str, Any] = {
                "model": catalog_entry.canonical_id,
                "instructions": system_prompt,
            }

            # Configure MCP servers if supported and provided
            if mcp_servers:
                mcp_configs = []
                MCPServerStdio = getattr(agents, "MCPServerStdio", None)
                if MCPServerStdio:
                    for server in mcp_servers:
                        mcp_configs.append(
                            MCPServerStdio(
                                name=server.name,
                                command=server.command,
                                args=list(server.args),
                                env=dict(server.env) if server.env else None,
                            )
                        )
                    agent_config["mcp_servers"] = mcp_configs

            # Create agent
            Agent = getattr(agents, "Agent", None)
            Runner = getattr(agents, "Runner", None)
            if not Agent or not Runner:
                raise RuntimeError("Agents SDK missing Agent or Runner class")

            agent = Agent(**agent_config)

            # Run with streaming
            run_streamed = getattr(Runner, "run_streamed", None)
            if not run_streamed:
                raise RuntimeError("Agents SDK Runner.run_streamed not found")

            stream = run_streamed(
                agent=agent,
                input=prompt,
                max_turns=max_turns,
            )

            async for event in stream:
                provider_event = _convert_agents_event(event)
                if provider_event is None:
                    continue

                if provider_event.kind == EventKind.TEXT and provider_event.text:
                    partial_text_buffer += provider_event.text
                    _emit_partial_fragments(raw=event)
                    continue

                if provider_event.kind == EventKind.USAGE:
                    total_input_tokens += provider_event.input_tokens or 0
                    total_output_tokens += provider_event.output_tokens or 0

                if provider_event.kind == EventKind.STATUS and provider_event.status == "completed":
                    _emit_partial_fragments(force=True, raw=event)

                if on_event:
                    on_event(provider_event)

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            if on_event:
                on_event(ProviderEvent(kind=EventKind.ERROR, text=str(exc)))
                on_event(ProviderEvent(kind=EventKind.STATUS, status="failed"))
            return ProviderResult(
                text=str(exc),
                is_error=True,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                resume_state=None,
                duration_ms=elapsed_ms,
                provider_reported_cost_usd=None,
                model_canonical_id=catalog_entry.canonical_id,
            )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        _emit_partial_fragments(force=True)

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="completed"))

        return ProviderResult(
            text="\n\n".join(final_text_parts),
            is_error=False,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            resume_state=None,  # Agents SDK doesn't support resume
            duration_ms=elapsed_ms,
            provider_reported_cost_usd=None,
            model_canonical_id=catalog_entry.canonical_id,
        )
