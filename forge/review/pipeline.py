"""Review gate data classes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReviewCostInfo:
    """Accumulated cost from one or more LLM review calls."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: ReviewCostInfo) -> None:
        """Accumulate cost from another ReviewCostInfo in-place."""
        self.cost_usd += other.cost_usd
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class GateResult:
    """Outcome of a single review gate."""

    passed: bool
    gate: str
    details: str
    retriable: bool = (
        False  # True = transient failure (empty response, SDK error) — re-review, don't re-agent
    )
    infra_error: bool = (
        False  # True = environment/infra failure (missing module, wrong Python, cmd not found)
    )
    # — skip this gate instead of consuming a retry
    needs_human: bool = False  # True = escalate to awaiting_input for human decision
    # Adaptive review metadata (all optional, backward-compatible)
    review_strategy: str | None = None  # "tier1", "tier2", "tier3"
    chunk_count: int | None = None  # Tier 3 only: total number of chunks
    chunk_verdicts: list[str] | None = None  # Tier 3 only: e.g. ["PASS","FAIL","PASS"]


@dataclass
class ReviewOutcome:
    """Final outcome of the full review pipeline."""

    approved: bool
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None
