"""Planning module for Forge.

The unified planner replaces the former 4-stage pipeline
(Scout → Architect → Detailer → Validator) with a single agent
that has full codebase read access.
"""

from forge.core.planning.unified_planner import UnifiedPlanner, UnifiedPlannerResult
from forge.core.planning.models import (
    KeyModule,
    RelevantInterface,
    CodebaseMap,
    ValidationIssue,
    MinorFix,
    ValidationResult,
    PlanFeedback,
    CodebaseMapMeta,
)
from forge.core.planning.validator import validate_plan

__all__ = [
    "UnifiedPlanner",
    "UnifiedPlannerResult",
    "KeyModule",
    "RelevantInterface",
    "CodebaseMap",
    "ValidationIssue",
    "MinorFix",
    "ValidationResult",
    "PlanFeedback",
    "CodebaseMapMeta",
    "validate_plan",
]
