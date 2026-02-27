"""3-gate review pipeline. Mandatory, no exceptions."""

from dataclasses import dataclass, field
from typing import Callable, Awaitable


@dataclass
class GateResult:
    """Outcome of a single review gate."""

    passed: bool
    gate: str
    details: str


@dataclass
class ReviewOutcome:
    """Final outcome of the full review pipeline."""

    approved: bool
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None


GateFunc = Callable[[str], Awaitable[GateResult]]


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
            result = await gate(task_id)
            results.append(result)
            if not result.passed:
                return ReviewOutcome(
                    approved=False,
                    gate_results=results,
                    failed_gate=result.gate,
                )

        return ReviewOutcome(approved=True, gate_results=results)
