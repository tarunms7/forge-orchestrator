"""Shared helpers for claude-code-sdk interaction."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query
from claude_code_sdk._internal import client as _sdk_client
from claude_code_sdk._internal import message_parser as _sdk_parser

logger = logging.getLogger("forge.sdk")


@dataclass
class SdkResult:
    """Structured result from an SDK query with cost and token tracking."""

    result_text: str
    is_error: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_id: str
    duration_ms: int

    @property
    def result(self) -> str:
        """Backward-compatible alias for result_text."""
        return self.result_text

    @classmethod
    def from_result_message(cls, msg: ResultMessage) -> "SdkResult":
        """Extract cost and token info from a ResultMessage."""
        usage = msg.usage or {}
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        return cls(
            result_text=msg.result or "",
            is_error=msg.is_error,
            cost_usd=msg.total_cost_usd or 0.0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=msg.session_id,
            duration_ms=msg.duration_ms,
        )


# Patch the SDK's message parser to gracefully skip unknown message types
# (e.g. rate_limit_event) instead of crashing the entire query.
# We patch BOTH the module AND the client's local reference.
_original_parse = _sdk_parser.parse_message


class _SkipMessage:
    """Sentinel for messages the SDK doesn't know about."""

    pass


def _patched_parse(data):
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


async def sdk_query(
    prompt: str,
    options: ClaudeCodeOptions,
    on_message: Callable[[Any], Any] | None = None,
) -> SdkResult | None:
    """Run a claude-code-sdk query with clean environment. Returns an SdkResult or None.

    Removes CLAUDECODE from os.environ (also removed at CLI entry, but belt-and-suspenders)
    so the SDK subprocess doesn't reject our call as a 'nested session'.

    If *on_message* is provided it is ``await``-ed for every message yielded by the
    SDK stream (including non-result messages), enabling real-time streaming to callers.
    """
    os.environ.pop("CLAUDECODE", None)
    last_result: ResultMessage | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            if on_message is not None:
                await on_message(message)
            if isinstance(message, ResultMessage):
                last_result = message
        if last_result is None:
            return None
        return SdkResult.from_result_message(last_result)
    except Exception as e:
        if "Unknown message type" in str(e):
            logger.debug("Ignoring unknown message type from CLI: %s", e)
            if last_result is None:
                return None
            return SdkResult.from_result_message(last_result)
        # Surface the actual error details instead of opaque SDK message
        error_msg = str(e)
        if "exit code" in error_msg:
            logger.error("Claude CLI subprocess failed: %s", error_msg)
            if "CLAUDECODE" in os.environ:
                logger.error("Note: CLAUDECODE env var is set — this blocks nested sessions.")
        raise
    finally:
        pass  # Don't restore CLAUDECODE — it should stay removed for the process lifetime
