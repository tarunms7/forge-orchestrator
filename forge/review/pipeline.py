"""3-gate review pipeline. Mandatory, no exceptions."""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Union


@dataclass
class GateResult:
    """Outcome of a single review gate."""

    passed: bool
    gate: str
    details: str
    retriable: bool = False  # True = transient failure (empty response, SDK error) — re-review, don't re-agent


@dataclass
class ReviewOutcome:
    """Final outcome of the full review pipeline."""

    approved: bool
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None


# Gate functions may return a plain GateResult or a tuple of
# (GateResult, cost_info) — e.g. gate2_llm_review returns
# tuple[GateResult, ReviewCostInfo].  The pipeline unpacks both.
GateFunc = Callable[[str], Awaitable[Union[GateResult, tuple[GateResult, Any]]]]


def _unpack_gate_result(raw: GateResult | tuple[GateResult, Any]) -> GateResult:
    """Extract a GateResult from a plain value or a (GateResult, extra) tuple."""
    if isinstance(raw, tuple):
        return raw[0]
    return raw


class ReviewPipeline:
    """Runs Gate 1 -> Gate 2 -> Gate 3 sequentially. Stops on first failure."""

    def __init__(
        self,
        gate1: GateFunc,
        gate2: GateFunc,
        gate3: GateFunc,
        max_retries: int = 3,
    ) -> None:
        self._gates = [gate1, gate2, gate3]
        self._max_retries = max_retries

    async def review(self, task_id: str) -> ReviewOutcome:
        results: list[GateResult] = []

        for gate in self._gates:
            raw = await gate(task_id)
            result = _unpack_gate_result(raw)
            results.append(result)
            if not result.passed:
                return ReviewOutcome(
                    approved=False,
                    gate_results=results,
                    failed_gate=result.gate,
                )

        return ReviewOutcome(approved=True, gate_results=results)
