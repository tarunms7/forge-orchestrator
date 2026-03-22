"""Review gate data classes."""

from dataclasses import dataclass, field


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


@dataclass
class ReviewOutcome:
    """Final outcome of the full review pipeline."""

    approved: bool
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None
