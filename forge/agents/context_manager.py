"""Tiered context compaction for agent sessions.

Inspired by Claude Code's 4-tier context management hierarchy:
  Tier 0: Token budget tracking & early warning
  Tier 1: Microcompaction — prune old tool results from event stream
  Tier 2: Summary injection — generate a summary prefix for resumed sessions
  Tier 3: Session restart with compacted context (last resort)

Forge agents run via the provider protocol (Claude SDK, OpenAI Agents SDK, etc.)
which owns the actual conversation history. We can't mutate that history directly.
What we CAN do:

1. Track cumulative token usage via ProviderEvent callbacks
2. Emit warnings when approaching context limits so the daemon can act
3. Build compacted summaries that get injected into resume prompts
4. Signal the runtime to abort + restart with a summary when necessary

This module is the "context intelligence" layer that was completely missing
from Forge — it previously had zero awareness of context window pressure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("forge.agents.context_manager")


class ContextPressure(str, Enum):
    """Current context window pressure level."""

    NORMAL = "normal"  # < 60% of context window
    ELEVATED = "elevated"  # 60-75% — start being cautious
    HIGH = "high"  # 75-85% — microcompact tool results
    CRITICAL = "critical"  # 85-95% — inject summary, prepare restart
    EXCEEDED = "exceeded"  # > 95% — must restart with compacted context


# Thresholds as fractions of max_context_tokens
_PRESSURE_THRESHOLDS = {
    ContextPressure.ELEVATED: 0.60,
    ContextPressure.HIGH: 0.75,
    ContextPressure.CRITICAL: 0.85,
    ContextPressure.EXCEEDED: 0.95,
}

# How many tokens to reserve for model output at each pressure level.
# Higher pressure → more conservative reservation.
_OUTPUT_RESERVE = {
    ContextPressure.NORMAL: 8_000,
    ContextPressure.ELEVATED: 6_000,
    ContextPressure.HIGH: 4_000,
    ContextPressure.CRITICAL: 3_000,
    ContextPressure.EXCEEDED: 2_000,
}

# Maximum tool result tokens to keep at each pressure level.
# At HIGH pressure, we aggressively prune old tool results.
_MAX_TOOL_RESULT_TOKENS = {
    ContextPressure.NORMAL: None,  # No limit
    ContextPressure.ELEVATED: 50_000,
    ContextPressure.HIGH: 25_000,
    ContextPressure.CRITICAL: 10_000,
    ContextPressure.EXCEEDED: 5_000,
}


@dataclass
class ContextSnapshot:
    """Point-in-time view of an agent's context usage."""

    timestamp: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    max_context_tokens: int
    pressure: ContextPressure
    utilization_pct: float  # 0.0 - 1.0
    estimated_turns_remaining: int
    tool_results_tracked: int
    tool_results_prunable: int

    def to_payload(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "max_context_tokens": self.max_context_tokens,
            "pressure": self.pressure.value,
            "utilization_pct": round(self.utilization_pct, 3),
            "estimated_turns_remaining": self.estimated_turns_remaining,
            "tool_results_tracked": self.tool_results_tracked,
            "tool_results_prunable": self.tool_results_prunable,
        }


@dataclass
class ToolResultEntry:
    """Tracked tool result for potential pruning."""

    tool_call_id: str
    tool_name: str
    timestamp: float
    estimated_tokens: int
    is_read_only: bool  # Read-only results are safest to prune
    pruned: bool = False


@dataclass
class CompactionDecision:
    """What the context manager recommends doing."""

    action: str  # "none", "warn", "prune_tools", "inject_summary", "restart"
    reason: str
    tool_ids_to_prune: list[str] = field(default_factory=list)
    summary_text: str | None = None
    estimated_tokens_freed: int = 0


# Read-only tools whose results are safe to prune (won't lose state)
_READ_ONLY_TOOLS = frozenset({
    "Read", "file_read", "FileRead",
    "Glob", "glob", "ListFiles",
    "Grep", "grep", "Search", "search",
    "Bash",  # Bash is classified at record time based on output
    "WebFetch", "WebSearch",
    "cat", "ls", "find", "git_log", "git_diff", "git_status",
})

# Tools whose results should NEVER be pruned (carry forward state)
_STATEFUL_TOOLS = frozenset({
    "Edit", "file_edit", "FileEdit", "FileWrite", "Write",
    "Bash_write",  # destructive bash commands
    "TodoWrite",
})


class AgentContextManager:
    """Tracks token usage for a single agent session and recommends compaction.

    Wire this into the ProviderEvent callback in run_with_retry(). Each event
    with token counts gets recorded. The manager tracks tool results and can
    recommend pruning old read-only results when context pressure rises.

    This is a MONITORING + ADVISORY module — it does not mutate the provider's
    conversation history directly. It signals the runtime what to do.
    """

    def __init__(
        self,
        max_context_tokens: int,
        *,
        agent_id: str = "unknown",
        task_id: str = "unknown",
    ) -> None:
        self._max_context = max_context_tokens
        self._agent_id = agent_id
        self._task_id = task_id

        # Token tracking
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._turn_count: int = 0
        self._tokens_per_turn: list[int] = []

        # Tool result tracking for microcompaction
        self._tool_results: list[ToolResultEntry] = []

        # State
        self._last_pressure = ContextPressure.NORMAL
        self._pressure_transitions: list[tuple[float, ContextPressure]] = []
        self._compaction_count: int = 0
        self._created_at: float = time.monotonic()

    def record_usage(self, input_tokens: int, output_tokens: int) -> ContextPressure:
        """Record token usage from a USAGE event. Returns current pressure."""
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._turn_count += 1

        total = input_tokens + output_tokens
        self._tokens_per_turn.append(total)

        new_pressure = self._compute_pressure(input_tokens)
        if new_pressure != self._last_pressure:
            self._pressure_transitions.append((time.monotonic(), new_pressure))
            if new_pressure.value > self._last_pressure.value:
                logger.info(
                    "Context pressure %s → %s for agent %s (task %s): %d/%d tokens (%.0f%%)",
                    self._last_pressure.value,
                    new_pressure.value,
                    self._agent_id,
                    self._task_id,
                    input_tokens,
                    self._max_context,
                    (input_tokens / self._max_context) * 100,
                )
            self._last_pressure = new_pressure

        return new_pressure

    def record_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result_text: str | None = None,
        *,
        is_read_only: bool | None = None,
    ) -> None:
        """Track a tool result for potential future pruning."""
        if is_read_only is None:
            is_read_only = tool_name in _READ_ONLY_TOOLS

        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(result_text) // 4 if result_text else 100

        self._tool_results.append(
            ToolResultEntry(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                timestamp=time.monotonic(),
                estimated_tokens=estimated_tokens,
                is_read_only=is_read_only,
            )
        )

    def recommend(self) -> CompactionDecision:
        """Analyze current state and recommend a compaction action.

        Called by the runtime after each turn to decide what to do.
        """
        pressure = self._last_pressure

        if pressure == ContextPressure.NORMAL:
            return CompactionDecision(action="none", reason="Context usage normal")

        if pressure == ContextPressure.ELEVATED:
            return CompactionDecision(
                action="warn",
                reason=f"Context at {self._utilization_pct:.0%} — approaching limit",
            )

        # HIGH: Microcompact — identify old read-only tool results to prune
        if pressure == ContextPressure.HIGH:
            prunable = self._get_prunable_tool_ids()
            if prunable:
                est_freed = sum(
                    tr.estimated_tokens
                    for tr in self._tool_results
                    if tr.tool_call_id in set(prunable) and not tr.pruned
                )
                return CompactionDecision(
                    action="prune_tools",
                    reason=f"Context at {self._utilization_pct:.0%} — pruning {len(prunable)} old tool results",
                    tool_ids_to_prune=prunable,
                    estimated_tokens_freed=est_freed,
                )
            return CompactionDecision(
                action="warn",
                reason=f"Context at {self._utilization_pct:.0%} — no prunable tool results",
            )

        # CRITICAL: Need summary injection for potential restart
        if pressure == ContextPressure.CRITICAL:
            return CompactionDecision(
                action="inject_summary",
                reason=f"Context at {self._utilization_pct:.0%} — prepare restart with summary",
            )

        # EXCEEDED: Must restart
        return CompactionDecision(
            action="restart",
            reason=f"Context at {self._utilization_pct:.0%} — must restart with compacted context",
        )

    def snapshot(self) -> ContextSnapshot:
        """Return current context usage snapshot."""
        total = self._input_tokens + self._output_tokens
        utilization = self._input_tokens / self._max_context if self._max_context > 0 else 0
        prunable = [tr for tr in self._tool_results if tr.is_read_only and not tr.pruned]

        return ContextSnapshot(
            timestamp=time.monotonic(),
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=total,
            max_context_tokens=self._max_context,
            pressure=self._last_pressure,
            utilization_pct=utilization,
            estimated_turns_remaining=self._estimate_remaining_turns(),
            tool_results_tracked=len(self._tool_results),
            tool_results_prunable=len(prunable),
        )

    def mark_pruned(self, tool_call_ids: list[str]) -> None:
        """Mark tool results as pruned (already handled by provider/runtime)."""
        id_set = set(tool_call_ids)
        for tr in self._tool_results:
            if tr.tool_call_id in id_set:
                tr.pruned = True
        self._compaction_count += 1

    def build_compaction_summary(self, agent_summary_so_far: str = "") -> str:
        """Build a compact context summary for session restart.

        This is injected as the resume prompt when the agent needs to restart
        with a fresh context window. Modeled after Claude Code's 9-section
        compactConversation() format but adapted for Forge's agent tasks.
        """
        parts = [
            "## Context Compaction Summary",
            "",
            "This is a RESUMED session. The previous session's context was compacted",
            "because the context window was filling up. Key information preserved below.",
            "",
        ]

        if agent_summary_so_far:
            parts.extend([
                "### Work Completed So Far",
                agent_summary_so_far,
                "",
            ])

        # Tool usage stats
        parts.extend([
            "### Session Stats",
            f"- Turns completed: {self._turn_count}",
            f"- Input tokens used: {self._input_tokens:,}",
            f"- Output tokens used: {self._output_tokens:,}",
            f"- Tool calls tracked: {len(self._tool_results)}",
            f"- Compactions performed: {self._compaction_count}",
            "",
        ])

        # Recent tool results that were NOT pruned (still relevant)
        recent_unpruned = [
            tr for tr in self._tool_results[-10:]
            if not tr.pruned
        ]
        if recent_unpruned:
            parts.append("### Recent Tool Activity")
            for tr in recent_unpruned:
                parts.append(f"- {tr.tool_name} (call {tr.tool_call_id})")
            parts.append("")

        parts.extend([
            "### Instructions",
            "Continue the task from where the previous session left off.",
            "Do NOT repeat work already done. Focus on remaining objectives.",
            "",
        ])

        return "\n".join(parts)

    # ── Internal ──────────────────────────────────────────────────────

    @property
    def _utilization_pct(self) -> float:
        return self._input_tokens / self._max_context if self._max_context > 0 else 0

    def _compute_pressure(self, input_tokens: int) -> ContextPressure:
        """Compute pressure level from input token count."""
        if self._max_context <= 0:
            return ContextPressure.NORMAL

        ratio = input_tokens / self._max_context
        for level in reversed(list(ContextPressure)):
            threshold = _PRESSURE_THRESHOLDS.get(level)
            if threshold is not None and ratio >= threshold:
                return level

        return ContextPressure.NORMAL

    def _estimate_remaining_turns(self) -> int:
        """Estimate how many turns remain before context exhaustion."""
        if not self._tokens_per_turn or self._max_context <= 0:
            return 999  # Unknown

        # Use average token growth per turn (input grows each turn)
        if len(self._tokens_per_turn) < 2:
            return 999

        # Input token growth rate
        recent = self._tokens_per_turn[-5:]
        if len(recent) < 2:
            return 999

        avg_growth = sum(
            recent[i] - recent[i - 1] for i in range(1, len(recent))
        ) / (len(recent) - 1)

        if avg_growth <= 0:
            return 999

        remaining_budget = self._max_context - self._input_tokens
        reserve = _OUTPUT_RESERVE.get(self._last_pressure, 8_000)
        usable = max(0, remaining_budget - reserve)

        return max(0, int(usable / avg_growth))

    def _get_prunable_tool_ids(self) -> list[str]:
        """Get tool call IDs that are safe to prune, oldest first."""
        prunable = [
            tr for tr in self._tool_results
            if tr.is_read_only and not tr.pruned
        ]
        # Keep the most recent N results, prune the rest
        keep_recent = 5
        if len(prunable) <= keep_recent:
            return []

        to_prune = prunable[:-keep_recent]
        return [tr.tool_call_id for tr in to_prune]
