"""Tool concurrency partitioning for parallel execution of read-only operations.

Inspired by Claude Code's toolOrchestration.ts which partitions tool calls into
serial vs concurrent batches based on whether each tool is "concurrency-safe".

In Forge, agents execute tools via the provider SDK (Claude Code SDK, OpenAI
Agents SDK, etc.). The SDK handles tool dispatch internally. However, Forge
can influence this by:

1. Classifying tools as read-only vs write (for safety auditing & context mgr)
2. Providing concurrency hints to providers that support batch tool execution
3. Pre-validating tool call batches before they hit the provider

This module provides the classification layer. Providers can use it to decide
whether to execute tool calls concurrently or serialize them.

Claude Code's pattern:
  - Read tools (Glob, Grep, FileRead, read-only Bash) → concurrent-safe
  - Write tools (FileEdit, FileWrite, destructive Bash) → exclusive access
  - Max concurrency cap (default: 10)

Forge adaptation:
  - Same read/write classification
  - Expose via ToolConcurrencyPolicy for providers
  - Track per-session tool execution stats for the context manager
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("forge.agents.tool_concurrency")

# Maximum number of concurrent tool calls (matches Claude Code's default)
DEFAULT_MAX_CONCURRENT = 10


# ── Tool Classification ──────────────────────────────────────────────

# Tools that only read state — safe to run in parallel
_READ_ONLY_TOOLS = frozenset(
    {
        # Claude SDK tools
        "Read",
        "FileRead",
        "file_read",
        "Glob",
        "glob",
        "Grep",
        "grep",
        "Search",
        "search",
        "ListFiles",
        "list_files",
        "WebFetch",
        "WebSearch",
        # Git read operations
        "git_log",
        "git_diff",
        "git_status",
        "git_show",
        "git_blame",
        # Generic
        "cat",
        "head",
        "tail",
        "ls",
        "find",
        "wc",
        "du",
    }
)

# Tools that modify state — must have exclusive access
_WRITE_TOOLS = frozenset(
    {
        "Edit",
        "FileEdit",
        "file_edit",
        "Write",
        "FileWrite",
        "file_write",
        "NotebookEdit",
        # Git write operations
        "git_add",
        "git_commit",
        "git_checkout",
        "git_merge",
    }
)

# Bash commands that are read-only (grep on command string)
_BASH_READ_PATTERNS = [
    re.compile(r"^\s*(cat|head|tail|less|more|wc|du|df|ls|find|tree)\s"),
    re.compile(r"^\s*(grep|rg|ag|ack|fgrep|egrep)\s"),
    re.compile(r"^\s*(git\s+(log|diff|show|status|branch|tag|remote|stash\s+list))\s"),
    re.compile(r"^\s*(python|node|ruby)\s+.*--version"),
    re.compile(r"^\s*(echo|printf|date|whoami|hostname|uname|env|printenv)\s"),
    re.compile(r"^\s*(which|type|command\s+-v)\s"),
    re.compile(r"^\s*(npm\s+(list|ls|view|info|outdated))\s"),
    re.compile(r"^\s*(pip\s+(list|show|freeze))\s"),
]


def is_tool_read_only(tool_name: str, tool_input: str | None = None) -> bool:
    """Determine if a tool call is read-only (safe for concurrent execution).

    Args:
        tool_name: The tool name (e.g., "Read", "Bash", "Edit")
        tool_input: The tool input/command string (for Bash classification)

    Returns:
        True if the tool only reads state and can safely run in parallel.
    """
    if tool_name in _READ_ONLY_TOOLS:
        return True
    if tool_name in _WRITE_TOOLS:
        return False

    # Bash/shell tools need command-level classification
    if tool_name.lower() in ("bash", "shell", "command", "execute"):
        if tool_input:
            return _is_bash_read_only(tool_input)
        return False  # Unknown bash command — assume write

    # Unknown tool — default to non-concurrent (safe)
    return False


def _is_bash_read_only(command: str) -> bool:
    """Check if a bash command is read-only by pattern matching."""
    cmd = command.strip()
    # Pipe chains: check if ALL commands in the chain are read-only
    if "|" in cmd:
        parts = cmd.split("|")
        return all(_is_single_bash_read_only(p.strip()) for p in parts)
    # Command chains with && or ;
    if "&&" in cmd or ";" in cmd:
        parts = re.split(r"[;&]+", cmd)
        return all(_is_single_bash_read_only(p.strip()) for p in parts if p.strip())
    return _is_single_bash_read_only(cmd)


def _is_single_bash_read_only(cmd: str) -> bool:
    """Check a single bash command (no pipes/chains)."""
    for pattern in _BASH_READ_PATTERNS:
        if pattern.match(cmd):
            return True
    # Redirects to file → write
    if re.search(r"[>]", cmd):
        return False
    return False


# ── Batch Partitioning ────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCall:
    """Normalized tool call for partitioning."""

    tool_call_id: str
    tool_name: str
    tool_input: str | None = None

    @property
    def is_read_only(self) -> bool:
        return is_tool_read_only(self.tool_name, self.tool_input)


@dataclass
class ToolBatch:
    """A batch of tool calls to execute together."""

    calls: list[ToolCall]
    concurrent: bool  # True if all calls are read-only and can run in parallel
    max_concurrent: int = DEFAULT_MAX_CONCURRENT

    @property
    def size(self) -> int:
        return len(self.calls)


def partition_tool_calls(
    calls: list[ToolCall],
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> list[ToolBatch]:
    """Partition tool calls into serial/concurrent batches.

    Follows Claude Code's partitioning strategy:
    - Consecutive read-only calls → single concurrent batch
    - Each write call → standalone serial batch
    - Preserves ordering between batches

    Example:
        [Read, Grep, Glob, Edit, Read, Read] →
        [Batch(Read,Grep,Glob, concurrent=True), Batch(Edit, concurrent=False), Batch(Read,Read, concurrent=True)]
    """
    if not calls:
        return []

    batches: list[ToolBatch] = []
    current_reads: list[ToolCall] = []

    for call in calls:
        if call.is_read_only:
            current_reads.append(call)
        else:
            # Flush any accumulated reads as a concurrent batch
            if current_reads:
                batches.append(
                    ToolBatch(calls=current_reads, concurrent=True, max_concurrent=max_concurrent)
                )
                current_reads = []
            # Write call gets its own serial batch
            batches.append(ToolBatch(calls=[call], concurrent=False))

    # Flush remaining reads
    if current_reads:
        batches.append(
            ToolBatch(calls=current_reads, concurrent=True, max_concurrent=max_concurrent)
        )

    return batches


# ── Execution Stats ───────────────────────────────────────────────────


@dataclass
class ToolExecutionStats:
    """Aggregated tool execution statistics for a session."""

    total_calls: int = 0
    read_only_calls: int = 0
    write_calls: int = 0
    concurrent_batches: int = 0
    serial_batches: int = 0
    total_wall_time_ms: float = 0
    saved_time_ms: float = 0  # Estimated time saved by concurrent execution
    _call_durations: list[float] = field(default_factory=list)

    def record_batch(
        self,
        batch: ToolBatch,
        wall_time_ms: float,
        individual_times_ms: list[float] | None = None,
    ) -> None:
        """Record execution stats for a completed batch."""
        self.total_calls += batch.size
        self.total_wall_time_ms += wall_time_ms

        if batch.concurrent:
            self.concurrent_batches += 1
            self.read_only_calls += batch.size
            # Estimate time saved: sum of individual times minus wall time
            if individual_times_ms:
                serial_time = sum(individual_times_ms)
                self.saved_time_ms += max(0, serial_time - wall_time_ms)
        else:
            self.serial_batches += 1
            self.write_calls += batch.size

        self._call_durations.append(wall_time_ms)

    def to_payload(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "read_only_calls": self.read_only_calls,
            "write_calls": self.write_calls,
            "concurrent_batches": self.concurrent_batches,
            "serial_batches": self.serial_batches,
            "total_wall_time_ms": round(self.total_wall_time_ms, 1),
            "estimated_time_saved_ms": round(self.saved_time_ms, 1),
            "concurrency_ratio": (
                round(self.read_only_calls / self.total_calls, 2) if self.total_calls else 0
            ),
        }
