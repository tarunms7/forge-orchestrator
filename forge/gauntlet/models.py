"""Pydantic models for the Forge gauntlet test harness."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StageResult(BaseModel):
    """Result of a single pipeline stage."""

    name: str
    passed: bool
    duration_s: float
    details: str = ""


class AssertionResult(BaseModel):
    """Result of a single scenario assertion check."""

    name: str
    passed: bool
    message: str


class ScenarioResult(BaseModel):
    """Full result of running one gauntlet scenario."""

    name: str
    passed: bool
    duration_s: float
    stages: list[StageResult] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    cost_usd: float = 0.0
    error: str | None = None


class GauntletResult(BaseModel):
    """Aggregate result of a full gauntlet run across all selected scenarios."""

    scenarios: list[ScenarioResult] = Field(default_factory=list)
    total_duration_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios)


class ScenarioConfig(BaseModel):
    """Static metadata for a registered gauntlet scenario."""

    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    chaos_compatible: bool = False
