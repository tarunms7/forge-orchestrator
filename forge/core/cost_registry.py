"""Cost tracking and estimation for multi-provider models.

Provides rate lookups, cost calculations, and pipeline cost estimation
with support for legacy settings migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from forge.providers.base import ModelSpec, ProviderResult

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# ModelRates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRates:
    """Per-1k-token cost rates for a model."""

    input_per_1k: float
    output_per_1k: float


# ---------------------------------------------------------------------------
# UnknownCostBehavior / UnknownModelCostError
# ---------------------------------------------------------------------------


class UnknownCostBehavior(str, Enum):
    """Controls behavior when a model has no known cost rates."""

    BLOCK = "block"
    ESTIMATE_HIGH = "estimate_high"
    ALLOW = "allow"


class UnknownModelCostError(Exception):
    """Raised when cost lookup fails with BLOCK behavior."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        super().__init__(f"No cost rates found for model {spec} and behavior is BLOCK")


# ---------------------------------------------------------------------------
# Default rates
# ---------------------------------------------------------------------------

_DEFAULT_RATES: dict[str, ModelRates] = {
    # Claude models
    "claude:sonnet": ModelRates(input_per_1k=0.003, output_per_1k=0.015),
    "claude:opus": ModelRates(input_per_1k=0.015, output_per_1k=0.075),
    "claude:haiku": ModelRates(input_per_1k=0.00025, output_per_1k=0.00125),
    # OpenAI models
    "openai:gpt-5.4": ModelRates(input_per_1k=0.005, output_per_1k=0.015),
    "openai:gpt-5.4-mini": ModelRates(input_per_1k=0.0004, output_per_1k=0.0016),
    "openai:gpt-5.4-nano": ModelRates(input_per_1k=0.0001, output_per_1k=0.0004),
    "openai:o3": ModelRates(input_per_1k=0.010, output_per_1k=0.040),
    # Provider defaults (fallback within a provider)
    "claude:default": ModelRates(input_per_1k=0.003, output_per_1k=0.015),
    "openai:default": ModelRates(input_per_1k=0.005, output_per_1k=0.015),
}


# ---------------------------------------------------------------------------
# StageCostEstimate / PipelineCostEstimate
# ---------------------------------------------------------------------------


@dataclass
class StageCostEstimate:
    """Cost estimate for a single pipeline stage."""

    stage: str
    model_spec: ModelSpec
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float


@dataclass
class PipelineCostEstimate:
    """Aggregate cost estimate for an entire pipeline."""

    stages: list[StageCostEstimate] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.estimated_cost_usd for s in self.stages)


# ---------------------------------------------------------------------------
# CostRegistry
# ---------------------------------------------------------------------------


class CostRegistry:
    """Registry for model cost rates with fallback chain."""

    def __init__(
        self,
        overrides: dict[str, ModelRates] | None = None,
        unknown_behavior: UnknownCostBehavior = UnknownCostBehavior.ESTIMATE_HIGH,
    ) -> None:
        self._rates: dict[str, ModelRates] = dict(_DEFAULT_RATES)
        if overrides:
            self._rates.update(overrides)
        self._unknown_behavior = unknown_behavior

    def get_rates(self, spec: ModelSpec) -> ModelRates:
        """Look up rates by ModelSpec.

        Fallback chain: exact cost_key -> provider:default -> unknown_behavior.
        """
        # Try exact key
        key = str(spec)
        if key in self._rates:
            return self._rates[key]

        # Try provider default
        default_key = f"{spec.provider}:default"
        if default_key in self._rates:
            return self._rates[default_key]

        # Unknown model behavior
        if self._unknown_behavior == UnknownCostBehavior.BLOCK:
            raise UnknownModelCostError(spec)

        if self._unknown_behavior == UnknownCostBehavior.ALLOW:
            return ModelRates(input_per_1k=0.0, output_per_1k=0.0)

        # ESTIMATE_HIGH — use highest known rates
        return self._highest_rates()

    def calculate_cost(self, spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD."""
        rates = self.get_rates(spec)
        return (input_tokens / 1000) * rates.input_per_1k + (
            output_tokens / 1000
        ) * rates.output_per_1k

    def _highest_rates(self) -> ModelRates:
        """Return the highest known rates across all models."""
        max_input = max(r.input_per_1k for r in self._rates.values())
        max_output = max(r.output_per_1k for r in self._rates.values())
        return ModelRates(input_per_1k=max_input, output_per_1k=max_output)


# ---------------------------------------------------------------------------
# resolve_cost helper
# ---------------------------------------------------------------------------


def resolve_cost(
    result: ProviderResult,
    spec: ModelSpec,
    cost_registry: CostRegistry,
) -> float:
    """Resolve the cost of a provider execution.

    Uses provider_reported_cost first if available, else calculates
    from token counts using the cost registry.
    """
    if result.provider_reported_cost_usd is not None:
        return result.provider_reported_cost_usd

    return cost_registry.calculate_cost(spec, result.input_tokens, result.output_tokens)


# ---------------------------------------------------------------------------
# Legacy settings migration
# ---------------------------------------------------------------------------


def migrate_legacy_cost_settings(
    sonnet_input: float,
    sonnet_output: float,
    haiku_input: float,
    haiku_output: float,
    opus_input: float,
    opus_output: float,
) -> dict[str, ModelRates]:
    """Convert old per-family cost_rate_* settings to CostRegistry overrides.

    Maps the legacy ForgeSettings fields (cost_rate_sonnet_input, etc.)
    to the new provider:model keyed rate format.
    """
    return {
        "claude:sonnet": ModelRates(input_per_1k=sonnet_input, output_per_1k=sonnet_output),
        "claude:haiku": ModelRates(input_per_1k=haiku_input, output_per_1k=haiku_output),
        "claude:opus": ModelRates(input_per_1k=opus_input, output_per_1k=opus_output),
    }
