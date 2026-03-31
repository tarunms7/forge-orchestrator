"""Forge gauntlet — end-to-end test harness for pipeline validation."""

from forge.gauntlet.models import GauntletResult, ScenarioResult
from forge.gauntlet.scenarios import SCENARIO_REGISTRY


def __getattr__(name: str):  # noqa: N807
    if name == "GauntletRunner":
        from forge.gauntlet.runner import GauntletRunner

        return GauntletRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GauntletResult",
    "GauntletRunner",
    "ScenarioResult",
    "SCENARIO_REGISTRY",
]
