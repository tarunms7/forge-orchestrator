"""Tests for forge.gauntlet.report."""

from __future__ import annotations

import json

from forge.gauntlet.models import AssertionResult, GauntletResult, ScenarioResult, StageResult
from forge.gauntlet.report import format_report_json, format_report_rich, format_report_summary


def _make_result(passed: bool = True) -> GauntletResult:
    """Create a sample GauntletResult for testing."""
    return GauntletResult(
        scenarios=[
            ScenarioResult(
                name="happy_path",
                passed=True,
                duration_s=2.5,
                stages=[
                    StageResult(name="preflight", passed=True, duration_s=0.1, details="ok"),
                    StageResult(name="planning", passed=True, duration_s=1.0, details=""),
                ],
                assertions=[
                    AssertionResult(name="all_stages_pass", passed=True, message="All 6 stages passed"),
                ],
                artifacts={"pipeline_id": "abc123"},
                cost_usd=0.05,
            ),
            ScenarioResult(
                name="review_gate_failure",
                passed=passed,
                duration_s=1.2,
                stages=[
                    StageResult(name="review", passed=False, duration_s=0.5, details="quality too low"),
                ],
                assertions=[
                    AssertionResult(name="review_fails", passed=passed, message="Review correctly failed"),
                ],
                cost_usd=0.01,
            ),
        ],
        total_duration_s=3.7,
    )


def test_format_report_summary_all_pass():
    result = _make_result(passed=True)
    summary = format_report_summary(result)
    assert summary == "Gauntlet: 2/2 passed in 3.7s"


def test_format_report_summary_with_failure():
    result = _make_result(passed=False)
    summary = format_report_summary(result)
    assert summary == "Gauntlet: 1/2 passed in 3.7s"


def test_format_report_json_roundtrip():
    result = _make_result()
    json_str = format_report_json(result)
    data = json.loads(json_str)
    assert data["total_duration_s"] == 3.7
    assert len(data["scenarios"]) == 2
    assert data["scenarios"][0]["name"] == "happy_path"


def test_format_report_rich_no_crash():
    """Smoke test: format_report_rich should not raise."""
    result = _make_result()
    format_report_rich(result, verbose=False)


def test_format_report_rich_verbose_no_crash():
    """Smoke test: verbose mode should not raise."""
    result = _make_result()
    format_report_rich(result, verbose=True)


def test_format_report_rich_with_error():
    """Scenario with an error should not crash."""
    result = GauntletResult(
        scenarios=[
            ScenarioResult(
                name="broken",
                passed=False,
                duration_s=0.1,
                error="RuntimeError: something broke\n  traceback here",
            ),
        ],
        total_duration_s=0.1,
    )
    format_report_rich(result, verbose=True)


def test_format_report_summary_empty():
    result = GauntletResult(scenarios=[], total_duration_s=0.0)
    summary = format_report_summary(result)
    assert summary == "Gauntlet: 0/0 passed in 0.0s"
