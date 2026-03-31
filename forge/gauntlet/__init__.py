"""Forge gauntlet — end-to-end test harness for pipeline validation."""

from forge.gauntlet.models import GauntletResult, ScenarioResult

# These are defined by sibling tasks; use lazy imports to avoid circular errors
# at import time while still exposing them in the public API.
SCENARIO_REGISTRY: dict[str, object] = {}


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
