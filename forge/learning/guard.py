"""RuntimeGuard -- detects and stops wasteful agent retry loops."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger("forge.learning")

# Pre-compiled regex patterns for normalize_command()
_RE_REDIRECT = re.compile(r"\s*2>&1\s*(\|.*)?$")
_RE_TAIL = re.compile(r"\s*\|\s*tail\s+-\d+$")
_RE_HEAD = re.compile(r"\s*\|\s*head\s+-\d+$")
_RE_TMP_PATH = re.compile(r"/tmp/[^\s]+")
_RE_VAR_PATH = re.compile(r"/var/folders/[^\s]+")
_RE_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_RE_TIMESTAMP = re.compile(r"\b\d{10,}\b")


@dataclass
class FailureRecord:
    """Record of a single command failure."""

    command: str
    normalized_command: str
    error_class: str  # categorized error type
    stderr_snippet: str  # first 500 chars of error output
    attempt_number: int


class GuardTriggered(Exception):
    """Raised when RuntimeGuard detects a wasteful retry loop."""

    def __init__(self, message: str, failures: list[FailureRecord]):
        super().__init__(message)
        self.failures = failures


def normalize_command(command: str) -> str:
    """Normalize a shell command by stripping variable parts.

    Strips:
    - Timestamps, PIDs, UUIDs
    - Temp directory paths (/tmp/xxx, /var/folders/xxx)
    - Trailing whitespace and output redirection variations
    - Verbose/debug flags that don't change the fundamental approach

    Keeps:
    - The base executable and action
    - Meaningful flags (--timeout, specific file paths)
    """
    cmd = command.strip()
    # Strip output redirection
    cmd = _RE_REDIRECT.sub("", cmd)
    cmd = _RE_TAIL.sub("", cmd)
    cmd = _RE_HEAD.sub("", cmd)
    # Strip temp paths
    cmd = _RE_TMP_PATH.sub("/tmp/TEMP", cmd)
    cmd = _RE_VAR_PATH.sub("/var/TEMP", cmd)
    # Strip UUIDs
    cmd = _RE_UUID.sub("UUID", cmd)
    # Strip timestamp-like numbers (but not port numbers or small integers)
    cmd = _RE_TIMESTAMP.sub("TIMESTAMP", cmd)
    return cmd


def classify_error(error_output: str) -> str:
    """Classify error output into a category.

    Returns a short string like 'module_not_found', 'command_not_found',
    'permission_denied', 'test_failure', 'syntax_error', 'timeout', 'unknown'.
    """
    err = error_output.lower()
    if "modulenotfounderror" in err or "no module named" in err:
        return "module_not_found"
    if "command not found" in err or "no such file or directory" in err:
        return "command_not_found"
    if "permission denied" in err:
        return "permission_denied"
    if "syntaxerror" in err or "syntax error" in err:
        return "syntax_error"
    if "importerror" in err:
        return "import_error"
    if "filenotfounderror" in err:
        return "file_not_found"
    if "timeout" in err or "timed out" in err:
        return "timeout"
    if "failed" in err and ("test" in err or "pytest" in err or "assert" in err):
        return "test_failure"
    if "connection refused" in err or "connection error" in err:
        return "connection_error"
    return "unknown"


def approach_signature(normalized_cmd: str, error_class: str) -> str:
    """Create a signature for a (command approach, error type) pair."""
    raw = f"{normalized_cmd}||{error_class}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class RuntimeGuard:
    """Monitors agent SDK messages for wasteful retry loops.

    Tracks Bash tool calls and their results. When the same approach
    (base command + error type) fails repeatedly:
    - 2nd failure: sets warning flag (caller should inject warning)
    - 3rd failure: raises GuardTriggered

    Usage:
        guard = RuntimeGuard()

        async def guarded_on_msg(msg):
            guard.inspect(msg)  # may raise GuardTriggered
            await original_on_msg(msg)
    """

    _MAX_PENDING = 500  # LRU eviction threshold for tracking dicts

    def __init__(self, max_attempts: int = 3):
        self._max_attempts = max_attempts
        # Track pending tool uses: tool_use_id -> (command, normalized_command)
        # Uses OrderedDict for LRU eviction at _MAX_PENDING entries.
        self._pending_bash: OrderedDict[str, tuple[str, str]] = OrderedDict()
        # Track approach attempts: approach_signature -> list[FailureRecord]
        self._approach_attempts: OrderedDict[str, list[FailureRecord]] = OrderedDict()
        # Public state
        self.warning_issued: bool = False
        self.triggered: bool = False
        self.failures: list[FailureRecord] = []

    def inspect(self, message) -> str | None:
        """Inspect an SDK message for Bash failures.

        Call this for every AssistantMessage received from the SDK.

        Returns:
            - None: no action needed
            - "warning": 2nd failure detected, caller should inject warning

        Raises:
            GuardTriggered: on 3rd failure of same approach
        """
        # Only process AssistantMessage
        content = getattr(message, "content", None)
        if content is None:
            return None

        for block in content:
            # Track Bash tool calls
            if hasattr(block, "name") and block.name == "Bash":
                tool_id = getattr(block, "id", None)
                cmd = (getattr(block, "input", None) or {}).get("command", "")
                if tool_id and cmd:
                    norm = normalize_command(cmd)
                    self._pending_bash[tool_id] = (cmd, norm)
                    if len(self._pending_bash) > self._MAX_PENDING:
                        self._pending_bash.popitem(last=False)

            # Check tool results for failures
            if hasattr(block, "tool_use_id") and hasattr(block, "is_error"):
                tool_id = block.tool_use_id
                if tool_id in self._pending_bash and block.is_error is True:
                    cmd, norm = self._pending_bash.pop(tool_id)
                    raw_content = getattr(block, "content", "") or ""
                    if isinstance(raw_content, list):
                        error_text = " ".join(
                            item.get("text", "") for item in raw_content if isinstance(item, dict)
                        )[:500]
                    else:
                        error_text = str(raw_content)[:500]
                    err_class = classify_error(error_text)
                    sig = approach_signature(norm, err_class)

                    record = FailureRecord(
                        command=cmd,
                        normalized_command=norm,
                        error_class=err_class,
                        stderr_snippet=error_text,
                        attempt_number=len(self._approach_attempts.get(sig, [])) + 1,
                    )
                    self._approach_attempts.setdefault(sig, []).append(record)
                    self._approach_attempts.move_to_end(sig)
                    if len(self._approach_attempts) > self._MAX_PENDING:
                        self._approach_attempts.popitem(last=False)
                    self.failures.append(record)

                    attempts = self._approach_attempts[sig]
                    if len(attempts) >= self._max_attempts:
                        self.triggered = True
                        raise GuardTriggered(
                            f"Agent stuck: command '{cmd}' failed {len(attempts)} times "
                            f"with error type '{err_class}'. Stopping agent.",
                            failures=attempts,
                        )
                    if len(attempts) == self._max_attempts - 1:
                        self.warning_issued = True
                        return "warning"
                elif tool_id in self._pending_bash and block.is_error is False:
                    # Success -- remove from pending, don't track
                    self._pending_bash.pop(tool_id, None)

        return None

    def get_warning_message(self) -> str:
        """Get a warning message to inject into the agent's context."""
        # Find the approach that triggered the warning
        for _sig, attempts in self._approach_attempts.items():
            if len(attempts) == self._max_attempts - 1:
                last = attempts[-1]
                return (
                    f"\n\nWARNING: The command `{last.command}` has failed "
                    f"{len(attempts)} times with the same error type ({last.error_class}). "
                    f"Your next attempt MUST use a fundamentally different approach "
                    f"or you will be stopped. Do NOT retry the same command with trivial variations.\n"
                )
        return ""

    def get_failure_summary(self) -> str:
        """Get a summary of all failures for lesson extraction."""
        if not self.failures:
            return ""
        lines = [f"Agent failed with {len(self.failures)} command failures:"]
        for f in self.failures:
            lines.append(
                f"  [{f.attempt_number}] `{f.command}` -> {f.error_class}: {f.stderr_snippet[:100]}"
            )
        return "\n".join(lines)
