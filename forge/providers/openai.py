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
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

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

logger = logging.getLogger("forge.providers.openai")

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
        import openai_agents

        return openai_agents
    except ImportError:
        return None


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


# ---------------------------------------------------------------------------
# Tool name normalization
# ---------------------------------------------------------------------------


def _normalize_codex_tool(raw_name: str) -> str:
    """Map a Codex SDK tool/event name to its CoreTool string."""
    core = CODEX_TOOL_MAP.get(raw_name)
    if core is not None:
        return core.value
    return raw_name


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
        item_content = item.get("content", "")
        item_id = item.get("id", "")
    else:
        item_type = getattr(item, "type", "")
        item_content = getattr(item, "content", "")
        item_id = getattr(item, "id", "")

    if event_type == "item.completed" and item_type == "agent_message":
        text = item_content if isinstance(item_content, str) else str(item_content)
        return ProviderEvent(kind=EventKind.TEXT, text=text, raw=event)

    if event_type == "item.started" and item_type in CODEX_TOOL_MAP:
        return ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name=_normalize_codex_tool(item_type),
            tool_call_id=str(item_id) if item_id else str(uuid.uuid4()),
            tool_input=json.dumps(item_content) if item_content else None,
            raw=event,
        )

    if event_type == "item.completed" and item_type in CODEX_TOOL_MAP:
        output = ""
        if isinstance(item_content, dict):
            output = item_content.get("output", "")
        elif isinstance(item_content, str):
            output = item_content
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
        return ProviderEvent(kind=EventKind.STATUS, status="completed", raw=event)

    if event_type in ("turn.failed", "error"):
        error_msg = _get("message", "") or _get("error", "") or str(event)
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
        """Return OpenAI entries from FORGE_MODEL_CATALOG."""
        return [e for e in FORGE_MODEL_CATALOG if e.provider == "openai"]

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        """Check OpenAI SDK availability and API key.

        If backend is specified, only check that specific backend.
        Otherwise check all OpenAI backends.
        """
        errors: list[str] = []
        details_parts: list[str] = []

        # Check API key (required for all backends)
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            errors.append("OPENAI_API_KEY environment variable not set")
        else:
            details_parts.append("API key configured")

        # Check codex-sdk
        if backend is None or backend == "codex-sdk":
            codex = _try_import_codex()
            if codex is not None:
                details_parts.append(f"codex-sdk v{getattr(codex, '__version__', 'unknown')}")
            else:
                errors.append("openai-codex-sdk not installed")

        # Check openai-agents-sdk
        if backend is None or backend == "openai-agents-sdk":
            agents = _try_import_agents()
            if agents is not None:
                details_parts.append(f"agents-sdk v{getattr(agents, '__version__', 'unknown')}")
            else:
                errors.append("openai-agents not installed")

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
                tool_policy=tool_policy,
                workspace=workspace,
                max_turns=max_turns,
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

    def _start_codex(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        tool_policy: ToolPolicy,
        workspace: WorkspaceRoots,
        max_turns: int,
        resume_state: ResumeState | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> _CodexExecutionHandle:
        """Launch a Codex SDK execution."""
        task = asyncio.ensure_future(
            self._run_codex(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                tool_policy=tool_policy,
                workspace=workspace,
                max_turns=max_turns,
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
        tool_policy: ToolPolicy,
        workspace: WorkspaceRoots,
        max_turns: int,
        resume_state: ResumeState | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> ProviderResult:
        """Execute via Codex SDK, streaming events."""
        start_time = time.monotonic()
        total_input_tokens = 0
        total_output_tokens = 0
        final_text = ""
        session_token: str | None = None

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="started"))

        try:
            codex = _try_import_codex()
            if codex is None:
                raise ImportError("openai-codex-sdk is not installed")
            # Build safety instructions from denied operations
            safety_instructions = ""
            if tool_policy.mode == "denylist" and tool_policy.denied_operations:
                safety_instructions = _translate_denied_to_instructions(
                    tool_policy.denied_operations
                )

            full_system = system_prompt
            if safety_instructions:
                full_system = f"{system_prompt}\n\n{safety_instructions}"

            # Configure Codex options
            codex_config = {
                "model": catalog_entry.canonical_id,
                "prompt": prompt,
                "instructions": full_system,
                "sandbox_mode": "workspace-write",
                "cwd": workspace.primary_cwd,
                "max_turns": max_turns,
                "stream": True,
            }

            # Resume vs new thread
            if resume_state and resume_state.session_token:
                start_fn = getattr(codex, "resumeThread", None) or getattr(
                    codex, "resume_thread", None
                )
                if start_fn:
                    stream = start_fn(resume_state.session_token, **codex_config)
                else:
                    raise RuntimeError("Codex SDK does not support thread resume")
            else:
                start_fn = getattr(codex, "startThread", None) or getattr(
                    codex, "start_thread", None
                )
                if start_fn:
                    stream = start_fn(**codex_config)
                else:
                    raise RuntimeError("Codex SDK start function not found")

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
                    final_text += provider_event.text

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
            text=final_text,
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
        mcp_servers: list[MCPServerConfig] | None,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> ProviderResult:
        """Execute via Agents SDK (Responses API), streaming events."""
        start_time = time.monotonic()
        total_input_tokens = 0
        total_output_tokens = 0
        final_text = ""

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="started"))

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

                if provider_event.kind == EventKind.USAGE:
                    total_input_tokens += provider_event.input_tokens or 0
                    total_output_tokens += provider_event.output_tokens or 0

                if provider_event.kind == EventKind.TEXT and provider_event.text:
                    final_text += provider_event.text

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

        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="completed"))

        return ProviderResult(
            text=final_text,
            is_error=False,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            resume_state=None,  # Agents SDK doesn't support resume
            duration_ms=elapsed_ms,
            provider_reported_cost_usd=None,
            model_canonical_id=catalog_entry.canonical_id,
        )
