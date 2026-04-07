"""Conformance test framework for multi-provider validation.

Provides the base ConformanceTest ABC and ConformanceResult dataclass
used by agent, planner, and reviewer conformance test suites.
Run via ``forge providers test``.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry


@dataclass
class ConformanceResult:
    """Outcome of a single conformance test run."""

    passed: bool
    stage: str
    model: str
    details: str
    duration_ms: int
    events: list[dict] = field(default_factory=list)


class ConformanceTest(abc.ABC):
    """Base class for all provider conformance tests.

    Subclasses must set ``provider``, ``model``, and ``stage``
    class-level fields and implement :meth:`run`.
    """

    provider: str = ""
    model: str = ""
    stage: str = ""

    @abc.abstractmethod
    async def run(self, registry: ProviderRegistry) -> ConformanceResult:
        """Execute the conformance test and return a result."""
        ...

    # Convenience helpers for subclasses ------------------------------------

    @staticmethod
    def _timer() -> float:
        """Return a monotonic timestamp in milliseconds."""
        return time.monotonic() * 1000

    @classmethod
    def _elapsed(cls, start_ms: float) -> int:
        """Milliseconds elapsed since *start_ms*."""
        return int(cls._timer() - start_ms)

    def _pass(self, start_ms: float, details: str = "OK", **kwargs) -> ConformanceResult:
        return ConformanceResult(
            passed=True,
            stage=self.stage,
            model=self.model,
            details=details,
            duration_ms=self._elapsed(start_ms),
            **kwargs,
        )

    def _fail(self, start_ms: float, details: str, **kwargs) -> ConformanceResult:
        return ConformanceResult(
            passed=False,
            stage=self.stage,
            model=self.model,
            details=details,
            duration_ms=self._elapsed(start_ms),
            **kwargs,
        )
