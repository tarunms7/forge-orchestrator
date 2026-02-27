"""Heuristic cost estimator for Forge pipeline tasks.

Estimates the number of Claude sessions and time needed based on
task complexity. The model is:

    total sessions = 1 planner + N agents + N reviewers

Where N varies by complexity level.
"""

from __future__ import annotations


# Heuristic lookup: complexity -> (total_sessions, estimated_minutes)
# Sessions breakdown:
#   low:    1 planner + 1 agent + 1 reviewer = 3 sessions, ~5 min
#   medium: 1 planner + 2-3 agents + 2-3 reviewers = 6 sessions, ~15 min
#   high:   1 planner + 5-6 agents + 5-6 reviewers = 12 sessions, ~30 min
_HEURISTICS: dict[str, tuple[int, int]] = {
    "low": (3, 5),
    "medium": (6, 15),
    "high": (12, 30),
}


def estimate_cost(description: str, complexity: str) -> dict:
    """Estimate the cost of running a task.

    Args:
        description: The task description (currently unused in heuristics
            but available for future NLP-based estimation).
        complexity: One of ``"low"``, ``"medium"``, or ``"high"``.

    Returns:
        A dict with keys ``sessions``, ``estimated_minutes``, and
        ``complexity``.

    Raises:
        ValueError: If *complexity* is not a recognised level.
    """
    if complexity not in _HEURISTICS:
        raise ValueError(
            f"Invalid complexity '{complexity}'. Must be one of: {', '.join(_HEURISTICS)}"
        )

    sessions, minutes = _HEURISTICS[complexity]

    return {
        "sessions": sessions,
        "estimated_minutes": minutes,
        "complexity": complexity,
    }
