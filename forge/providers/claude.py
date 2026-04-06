"""Claude provider — wraps claude-code-sdk into the ProviderProtocol interface.

Extracts SDK interaction logic from forge/core/sdk_helpers.py and
forge/agents/adapter.py into a unified provider that can be registered
in the ProviderRegistry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query
from claude_code_sdk._internal import client as _sdk_client
from claude_code_sdk._internal import message_parser as _sdk_parser
from claude_code_sdk.types import StreamEvent

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
from forge.providers.catalog import (
    CLAUDE_TOOL_MAP,
    FORGE_MODEL_CATALOG,
)

logger = logging.getLogger("forge.providers.claude")

_CLAUDE_REASONING_APPEND_PROMPTS: dict[str, str] = {
    "low": (
        "Reasoning effort: low. Prioritize responsiveness and direct execution. "
        "Avoid exhaustive exploration unless the task clearly demands it."
    ),
    "medium": (
        "Reasoning effort: medium. Balance speed with care. Verify key assumptions "
        "before committing to a plan or implementation."
    ),
    "high": (
        "Reasoning effort: high. Think carefully, inspect relevant context before "
        "deciding, verify assumptions, and prefer stronger validation before finalizing."
    ),
}

# ---------------------------------------------------------------------------
# SDK monkey-patch for unknown message types (e.g. rate_limit_event)
# ---------------------------------------------------------------------------

_original_parse = _sdk_parser.parse_message


class _SkipMessage:
    """Sentinel for messages the SDK doesn't know about."""

    pass


def _patched_parse(data: dict) -> Any:
    try:
        return _original_parse(data)
    except Exception as e:
        if "Unknown message type" in str(e):
            logger.debug("Skipping unknown SDK message type: %s", data.get("type", "?"))
            return _SkipMessage()
        raise


# Patch both references so the fix takes effect everywhere
_sdk_parser.parse_message = _patched_parse
_sdk_client.parse_message = _patched_parse

# ---------------------------------------------------------------------------
# Forge denied_operations -> Claude disallowed_tools translation
# ---------------------------------------------------------------------------

# Maps Forge syntax patterns to Claude SDK Bash(...) disallowed_tools entries
_FORGE_TO_CLAUDE_DISALLOWED: dict[str, list[str]] = {
    "git:push": ["Bash(git push)", "Bash(git push *)"],
    "git:rebase": ["Bash(git rebase)", "Bash(git rebase *)"],
    "git:checkout": ["Bash(git checkout)", "Bash(git checkout *)"],
    "git:reset_hard": ["Bash(git reset --hard)", "Bash(git reset --hard *)"],
    "git:branch_delete": ["Bash(git branch -D *)", "Bash(git branch -d *)"],
    "git:merge": ["Bash(git merge)", "Bash(git merge *)"],
    "git:clean": ["Bash(git clean *)"],
    "git:stash": ["Bash(git stash)", "Bash(git stash *)"],
    "git:cherry_pick": ["Bash(git cherry-pick *)"],
    "git:tag": ["Bash(git tag *)"],
    "git:remote": ["Bash(git remote *)"],
    "net:curl": ["Bash(curl *)"],
    "net:wget": ["Bash(wget *)"],
    "net:ssh": ["Bash(ssh *)"],
    "net:scp": ["Bash(scp *)"],
    "net:rsync": ["Bash(rsync *)"],
    "net:nc": ["Bash(nc *)", "Bash(ncat *)"],
    "net:telnet": ["Bash(telnet *)"],
    "net:ftp": ["Bash(ftp *)"],
    "priv:sudo": ["Bash(sudo *)"],
    "priv:su": ["Bash(su *)"],
    "priv:doas": ["Bash(doas *)"],
    "perm:chmod": ["Bash(chmod *)"],
    "perm:chown": ["Bash(chown *)"],
    "perm:chgrp": ["Bash(chgrp *)"],
    "proc:kill": ["Bash(kill *)"],
    "proc:pkill": ["Bash(pkill *)"],
    "proc:killall": ["Bash(killall *)"],
    "container:docker": ["Bash(docker *)"],
    "container:podman": ["Bash(podman *)"],
    "sys:systemctl": ["Bash(systemctl *)"],
    "sys:service": ["Bash(service *)"],
    "sys:mount": ["Bash(mount *)"],
    "sys:umount": ["Bash(umount *)"],
    "env:export": ["Bash(export *)"],
    "env:unset": ["Bash(unset *)"],
    "file:read_dotenv": ["Read(.env)", "Read(.env.*)"],
}


def _translate_denied_operations(denied_ops: list[str]) -> list[str]:
    """Translate Forge denied_operations to Claude SDK disallowed_tools format."""
    result: list[str] = []
    for pattern in denied_ops:
        entries = _FORGE_TO_CLAUDE_DISALLOWED.get(pattern)
        if entries:
            result.extend(entries)
        else:
            # Pass through unknown patterns as-is (may be raw Bash(...) already)
            result.append(pattern)
    return result


# ---------------------------------------------------------------------------
# Event conversion helpers
# ---------------------------------------------------------------------------


def _normalize_tool_name(raw_name: str) -> str:
    """Map a Claude SDK tool name to its CoreTool string."""
    core = CLAUDE_TOOL_MAP.get(raw_name)
    if core is not None:
        return core.value
    return raw_name


def _convert_assistant_message(msg: Any) -> list[ProviderEvent]:
    """Convert an AssistantMessage to ProviderEvent(s)."""
    events: list[ProviderEvent] = []
    content = getattr(msg, "content", None)
    if not content:
        return events

    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            events.append(
                ProviderEvent(
                    kind=EventKind.TEXT,
                    text=getattr(block, "text", ""),
                    raw=block,
                )
            )
        elif block_type == "thinking":
            events.append(
                ProviderEvent(
                    kind=EventKind.STATUS,
                    status="thinking",
                    raw=block,
                )
            )
        elif block_type == "tool_use":
            tool_name = getattr(block, "name", "")
            tool_input_raw = getattr(block, "input", "")
            if isinstance(tool_input_raw, dict):
                tool_input_str = json.dumps(tool_input_raw)
            else:
                tool_input_str = str(tool_input_raw) if tool_input_raw else ""
            events.append(
                ProviderEvent(
                    kind=EventKind.TOOL_USE,
                    tool_name=_normalize_tool_name(tool_name),
                    tool_call_id=getattr(block, "id", None),
                    tool_input=tool_input_str,
                    raw=block,
                )
            )
        elif block_type == "tool_result":
            content_text = getattr(block, "content", "")
            if isinstance(content_text, list):
                content_text = "\n".join(getattr(b, "text", str(b)) for b in content_text)
            events.append(
                ProviderEvent(
                    kind=EventKind.TOOL_RESULT,
                    tool_call_id=getattr(block, "tool_use_id", None),
                    tool_output=str(content_text),
                    is_tool_error=getattr(block, "is_error", False),
                    raw=block,
                )
            )
    return events


def _convert_stream_event(msg: StreamEvent) -> list[ProviderEvent]:
    """Convert a Claude SDK partial stream event into ProviderEvent(s).

    Claude emits rich streaming updates through StreamEvent objects when
    include_partial_messages is enabled. Most of these are useful as
    high-level activity signals rather than literal text chunks, so we
    convert them to status/tool events the TUI can humanize cleanly.
    """
    raw_event = getattr(msg, "event", None) or {}
    if not isinstance(raw_event, dict):
        return []

    event_type = raw_event.get("type")
    if event_type == "message_start":
        return [ProviderEvent(kind=EventKind.STATUS, status="thinking", raw=msg)]

    if event_type == "content_block_start":
        block = raw_event.get("content_block") or {}
        if not isinstance(block, dict):
            return []
        block_type = block.get("type")
        if block_type == "text":
            return [ProviderEvent(kind=EventKind.STATUS, status="typing", raw=msg)]
        if block_type == "thinking":
            return [ProviderEvent(kind=EventKind.STATUS, status="thinking", raw=msg)]
        if block_type == "tool_use":
            tool_name = block.get("name", "")
            tool_input = block.get("input")
            tool_input_str = json.dumps(tool_input) if isinstance(tool_input, dict) else None
            return [
                ProviderEvent(
                    kind=EventKind.TOOL_USE,
                    tool_name=_normalize_tool_name(tool_name),
                    tool_call_id=block.get("id"),
                    tool_input=tool_input_str,
                    raw=msg,
                )
            ]
        return []

    if event_type == "content_block_delta":
        delta = raw_event.get("delta") or {}
        if not isinstance(delta, dict):
            return []
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            return [ProviderEvent(kind=EventKind.STATUS, status="typing", raw=msg)]
        if delta_type in {"thinking_delta", "input_json_delta"}:
            return [ProviderEvent(kind=EventKind.STATUS, status="thinking", raw=msg)]
        return []

    if event_type == "message_stop":
        return [ProviderEvent(kind=EventKind.STATUS, status="completed", raw=msg)]

    return []

def _convert_result_message(msg: ResultMessage) -> tuple[list[ProviderEvent], ProviderResult]:
    """Convert a ResultMessage to events + final ProviderResult."""
    events: list[ProviderEvent] = []

    # Text event for the result content
    result_text = msg.result or ""
    if result_text:
        events.append(
            ProviderEvent(
                kind=EventKind.TEXT,
                text=result_text,
                raw=msg,
            )
        )

    # Usage event
    usage = msg.usage or {}
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    if input_tokens or output_tokens:
        events.append(
            ProviderEvent(
                kind=EventKind.USAGE,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw=msg,
            )
        )

    # Status completed
    events.append(
        ProviderEvent(
            kind=EventKind.STATUS,
            status="completed",
            raw=msg,
        )
    )

    # Build ResumeState from session_id
    resume_state = None
    if msg.session_id:
        now_iso = datetime.now(UTC).isoformat()
        resume_state = ResumeState(
            provider="claude",
            backend="claude-code-sdk",
            session_token=msg.session_id,
            created_at=now_iso,
            last_active_at=now_iso,
            turn_count=msg.num_turns or 0,
            is_resumable=True,
        )

    provider_result = ProviderResult(
        text=result_text,
        is_error=msg.is_error,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        resume_state=resume_state,
        duration_ms=msg.duration_ms or 0,
        provider_reported_cost_usd=msg.total_cost_usd,
        model_canonical_id="",  # Filled by caller from catalog_entry
        raw=msg,
    )
    return events, provider_result


# ---------------------------------------------------------------------------
# _ClaudeExecutionHandle
# ---------------------------------------------------------------------------


class _ClaudeExecutionHandle(ExecutionHandle):
    """Wraps the async SDK query() generator into an ExecutionHandle."""

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
# ClaudeProvider
# ---------------------------------------------------------------------------


class ClaudeProvider:
    """ProviderProtocol implementation for Claude via claude-code-sdk."""

    @property
    def name(self) -> str:
        return "claude"

    def catalog_entries(self) -> list[CatalogEntry]:
        """Return Claude entries from FORGE_MODEL_CATALOG."""
        return [e for e in FORGE_MODEL_CATALOG if e.provider == "claude"]

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        """Check Claude CLI auth and SDK availability."""
        errors: list[str] = []
        details_parts: list[str] = []

        # Check SDK import
        try:
            import claude_code_sdk

            details_parts.append(
                f"claude-code-sdk v{getattr(claude_code_sdk, '__version__', 'unknown')}"
            )
        except ImportError:
            errors.append("claude-code-sdk not installed")

        # Check CLAUDECODE env var (blocks nested sessions)
        if "CLAUDECODE" in os.environ:
            errors.append(
                "CLAUDECODE env var is set — this blocks nested sessions. "
                "Remove it before running the forge daemon."
            )

        if not errors:
            details_parts.append("authenticated")

        return ProviderHealthStatus(
            healthy=len(errors) == 0,
            provider="claude",
            details=", ".join(details_parts),
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
        """Start a Claude execution. Returns handle for abort/result."""
        # Remove CLAUDECODE to prevent nested session block
        os.environ.pop("CLAUDECODE", None)

        # Build ClaudeCodeOptions
        options = self._build_options(
            system_prompt=system_prompt,
            catalog_entry=catalog_entry,
            tool_policy=tool_policy,
            workspace=workspace,
            max_turns=max_turns,
            reasoning_effort=reasoning_effort,
            resume_state=resume_state,
        )

        # Launch the async query task
        task = asyncio.ensure_future(self._run_query(prompt, options, catalog_entry, on_event))
        return _ClaudeExecutionHandle(task, catalog_entry)

    def can_resume(self, state: ResumeState) -> bool:
        """Return True if session_token is present and provider matches."""
        return state.provider == "claude" and bool(state.session_token) and state.is_resumable

    def cleanup_session(self, state: ResumeState) -> None:
        """No-op for Claude — sessions are managed by the CLI."""
        pass

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _build_options(
        system_prompt: str,
        catalog_entry: CatalogEntry,
        tool_policy: ToolPolicy,
        workspace: WorkspaceRoots,
        max_turns: int,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        resume_state: ResumeState | None = None,
    ) -> ClaudeCodeOptions:
        """Assemble ClaudeCodeOptions from provider parameters."""
        append_system_prompt = system_prompt
        if reasoning_effort and catalog_entry.supports_reasoning:
            effort_prompt = _CLAUDE_REASONING_APPEND_PROMPTS[reasoning_effort]
            append_system_prompt = (
                f"{system_prompt}\n\n{effort_prompt}" if system_prompt else effort_prompt
            )

        kwargs: dict[str, Any] = {
            "append_system_prompt": append_system_prompt,
            "permission_mode": "bypassPermissions",
            "cwd": workspace.primary_cwd,
            "model": catalog_entry.canonical_id,
            "max_turns": max_turns,
            # Surface partial Claude activity so planner/agent/review logs
            # stream progressively instead of only showing the final summary.
            "include_partial_messages": True,
        }

        # Resume from prior session
        if resume_state and resume_state.session_token:
            kwargs["resume"] = resume_state.session_token

        # Tool policy translation
        if tool_policy.mode == "allowlist":
            kwargs["allowed_tools"] = list(tool_policy.allowed_tools)
        elif tool_policy.mode == "denylist":
            kwargs["disallowed_tools"] = _translate_denied_operations(tool_policy.denied_operations)

        return ClaudeCodeOptions(**kwargs)

    @staticmethod
    async def _run_query(
        prompt: str,
        options: ClaudeCodeOptions,
        catalog_entry: CatalogEntry,
        on_event: Callable[[ProviderEvent], None] | None,
    ) -> ProviderResult:
        """Run the SDK query, emitting ProviderEvents and returning ProviderResult."""
        start_time = time.monotonic()
        last_result: ResultMessage | None = None
        partial_text_buffer = ""
        saw_partial_text = False

        # Emit STATUS started
        if on_event:
            on_event(ProviderEvent(kind=EventKind.STATUS, status="started"))

        def _emit_partial_fragments(*, force: bool = False, raw: Any | None = None) -> None:
            nonlocal partial_text_buffer, saw_partial_text
            fragments, partial_text_buffer = _drain_stream_text(
                partial_text_buffer,
                force=force,
            )
            if not on_event:
                return
            for fragment in fragments:
                saw_partial_text = True
                on_event(
                    ProviderEvent(
                        kind=EventKind.TEXT,
                        text=fragment,
                        raw=raw,
                    )
                )

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, _SkipMessage):
                    continue

                if isinstance(message, ResultMessage):
                    last_result = message
                    continue

                if isinstance(message, StreamEvent):
                    raw_event = getattr(message, "event", None) or {}
                    if isinstance(raw_event, dict):
                        event_type = raw_event.get("type")
                        if event_type == "content_block_delta":
                            delta = raw_event.get("delta") or {}
                            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                                text_delta = delta.get("text", "")
                                if isinstance(text_delta, str) and text_delta:
                                    partial_text_buffer += text_delta
                                    _emit_partial_fragments(raw=message)
                                continue

                        if event_type == "content_block_start":
                            block = raw_event.get("content_block") or {}
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                _emit_partial_fragments(force=True, raw=message)

                        if event_type == "message_stop":
                            _emit_partial_fragments(force=True, raw=message)

                    if on_event:
                        for event in _convert_stream_event(message):
                            on_event(event)
                    continue

                # AssistantMessage or other SDK message types
                _emit_partial_fragments(force=True, raw=message)
                if on_event:
                    assistant_events = _convert_assistant_message(message)
                    if saw_partial_text:
                        assistant_events = [
                            event for event in assistant_events if event.kind != EventKind.TEXT
                        ]
                    for event in assistant_events:
                        on_event(event)

        except Exception as e:
            if "Unknown message type" in str(e):
                logger.debug("Ignoring unknown message type from CLI: %s", e)
            else:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                if on_event:
                    on_event(
                        ProviderEvent(
                            kind=EventKind.ERROR,
                            text=str(e),
                        )
                    )
                    on_event(
                        ProviderEvent(
                            kind=EventKind.STATUS,
                            status="failed",
                        )
                    )
                raise

        # Build final result
        if last_result is None:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            if on_event:
                on_event(ProviderEvent(kind=EventKind.STATUS, status="completed"))
            return ProviderResult(
                text="",
                is_error=True,
                input_tokens=0,
                output_tokens=0,
                resume_state=None,
                duration_ms=elapsed_ms,
                provider_reported_cost_usd=None,
                model_canonical_id=catalog_entry.canonical_id,
            )

        _emit_partial_fragments(force=True, raw=last_result)
        events, provider_result = _convert_result_message(last_result)
        provider_result.model_canonical_id = catalog_entry.canonical_id

        if on_event:
            if saw_partial_text:
                events = [event for event in events if event.kind != EventKind.TEXT]
            for event in events:
                on_event(event)

        return provider_result
