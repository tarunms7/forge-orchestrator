"""Shared helpers for claude-code-sdk interaction."""

import logging
import os

from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query
from claude_code_sdk._internal import client as _sdk_client
from claude_code_sdk._internal import message_parser as _sdk_parser

logger = logging.getLogger("forge.sdk")

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


async def sdk_query(prompt: str, options: ClaudeCodeOptions) -> ResultMessage | None:
    """Run a claude-code-sdk query with clean environment. Returns the ResultMessage or None.

    Removes CLAUDECODE from os.environ (also removed at CLI entry, but belt-and-suspenders)
    so the SDK subprocess doesn't reject our call as a 'nested session'.
    """
    os.environ.pop("CLAUDECODE", None)
    last_result: ResultMessage | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                last_result = message
        return last_result
    except Exception as e:
        if "Unknown message type" in str(e):
            logger.debug("Ignoring unknown message type from CLI: %s", e)
            return last_result
        # Surface the actual error details instead of opaque SDK message
        error_msg = str(e)
        if "exit code" in error_msg:
            logger.error("Claude CLI failed: %s", error_msg)
            print(f"Claude CLI subprocess failed: {error_msg}")
            if "CLAUDECODE" in os.environ:
                print("Note: CLAUDECODE env var is set — this blocks nested sessions.")
        raise
    finally:
        pass  # Don't restore CLAUDECODE — it should stay removed for the process lifetime
