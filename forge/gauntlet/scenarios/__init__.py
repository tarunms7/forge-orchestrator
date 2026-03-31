"""Scenario registry for the Forge gauntlet test harness."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from forge.gauntlet.models import ScenarioConfig, ScenarioResult

from .happy_path import run_happy_path
from .integration_failure import run_integration_failure
from .multi_repo_contracts import run_multi_repo_contracts
from .resume_after_interrupt import run_resume_after_interrupt
from .review_gate_failure import run_review_gate_failure

SCENARIO_REGISTRY: dict[str, ScenarioConfig] = {
    "happy_path": ScenarioConfig(
        name="happy_path",
        description="Full pipeline success with no injected failures",
        tags=["smoke"],
        chaos_compatible=False,
    ),
    "multi_repo_contracts": ScenarioConfig(
        name="multi_repo_contracts",
        description="Cross-repo contract generation and validation",
        tags=["contracts"],
        chaos_compatible=False,
    ),
    "resume_after_interrupt": ScenarioConfig(
        name="resume_after_interrupt",
        description="Pipeline interrupt and resume with state preservation",
        tags=["resilience"],
        chaos_compatible=True,
    ),
    "review_gate_failure": ScenarioConfig(
        name="review_gate_failure",
        description="Review stage failure detection and error state transitions",
        tags=["resilience"],
        chaos_compatible=True,
    ),
    "integration_failure": ScenarioConfig(
        name="integration_failure",
        description="Post-merge integration check failure detection",
        tags=["resilience"],
        chaos_compatible=False,
    ),
}

SCENARIO_FUNCTIONS: dict[str, Callable[..., Awaitable[ScenarioResult]]] = {
    "happy_path": run_happy_path,
    "multi_repo_contracts": run_multi_repo_contracts,
    "resume_after_interrupt": run_resume_after_interrupt,
    "review_gate_failure": run_review_gate_failure,
    "integration_failure": run_integration_failure,
}

__all__ = [
    "SCENARIO_FUNCTIONS",
    "SCENARIO_REGISTRY",
]
