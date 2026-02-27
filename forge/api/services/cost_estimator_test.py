"""Tests for the cost estimator (estimate_cost function)."""

import pytest

from forge.api.services.cost_estimator import estimate_cost


def test_low_complexity_sessions():
    """Low complexity should return 3 sessions."""
    result = estimate_cost("Fix a typo", "low")
    assert result["sessions"] == 3


def test_low_complexity_minutes():
    """Low complexity should return 5 estimated minutes."""
    result = estimate_cost("Fix a typo", "low")
    assert result["estimated_minutes"] == 5


def test_low_complexity_field():
    """Low complexity should echo the complexity back."""
    result = estimate_cost("Fix a typo", "low")
    assert result["complexity"] == "low"


def test_medium_complexity_sessions():
    """Medium complexity should return 6 sessions."""
    result = estimate_cost("Build a REST API", "medium")
    assert result["sessions"] == 6


def test_medium_complexity_minutes():
    """Medium complexity should return 15 estimated minutes."""
    result = estimate_cost("Build a REST API", "medium")
    assert result["estimated_minutes"] == 15


def test_medium_complexity_field():
    """Medium complexity should echo the complexity back."""
    result = estimate_cost("Build a REST API", "medium")
    assert result["complexity"] == "medium"


def test_high_complexity_sessions():
    """High complexity should return 12 sessions."""
    result = estimate_cost("Rewrite the entire auth system", "high")
    assert result["sessions"] == 12


def test_high_complexity_minutes():
    """High complexity should return 30 estimated minutes."""
    result = estimate_cost("Rewrite the entire auth system", "high")
    assert result["estimated_minutes"] == 30


def test_high_complexity_field():
    """High complexity should echo the complexity back."""
    result = estimate_cost("Rewrite the entire auth system", "high")
    assert result["complexity"] == "high"


def test_result_has_all_keys():
    """The result dict should always contain sessions, estimated_minutes, complexity."""
    result = estimate_cost("Any task", "medium")
    assert "sessions" in result
    assert "estimated_minutes" in result
    assert "complexity" in result


def test_sessions_is_planner_plus_agents_plus_reviewers():
    """Sessions should be base (1 planner) + N agents + N reviewers.

    For medium: N=6 total implies 1 planner + 2-3 agents + 2-3 reviewers.
    We just verify the total matches the expected heuristic.
    """
    result = estimate_cost("Build something", "medium")
    # Medium = 6 sessions: 1 planner + N agents + N reviewers
    assert result["sessions"] == 6


def test_invalid_complexity_raises():
    """An invalid complexity level should raise a ValueError."""
    with pytest.raises(ValueError, match="Invalid complexity"):
        estimate_cost("task", "extreme")
