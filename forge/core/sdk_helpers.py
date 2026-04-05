"""Shared helpers for claude-code-sdk interaction.

.. deprecated::
    Direct SDK logic has moved to :mod:`forge.providers.claude`.
    This module is a thin backward-compatibility shim. New code should use
    :class:`forge.providers.claude.ClaudeProvider` via the ProviderRegistry.
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query

# Import the monkey-patch from claude provider so it's applied regardless
# of which module is imported first.
from forge.providers.claude import _SkipMessage  # noqa: F401

logger = logging.getLogger("forge.sdk")


@dataclass
class SdkResult:
    """Structured result from an SDK query with cost and token tracking.

    .. deprecated::
        Use :class:`forge.providers.base.ProviderResult` instead.
        This class is kept for backward compatibility during the migration.
    """

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


async def sdk_query(
    prompt: str,
    options: ClaudeCodeOptions,
    on_message: Callable[[Any], Any] | None = None,
) -> SdkResult | None:
    """Run a claude-code-sdk query with clean environment. Returns an SdkResult or None.

    .. deprecated::
        Use :class:`forge.providers.claude.ClaudeProvider` via the
        ProviderRegistry instead. This function is kept for backward
        compatibility during the migration.

    Removes CLAUDECODE from os.environ so the SDK subprocess doesn't reject
    our call as a 'nested session'.

    If *on_message* is provided it is ``await``-ed for every message yielded
    by the SDK stream, enabling real-time streaming to callers.
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
