"""Tests for forge.gauntlet.models."""

from forge.gauntlet.models import (
    AssertionResult,
    GauntletResult,
    ScenarioConfig,
    ScenarioResult,
    StageResult,
)


class TestStageResult:
    def test_basic(self):
        r = StageResult(name="preflight", passed=True, duration_s=0.5, details="ok")
        assert r.name == "preflight"
        assert r.passed is True
        assert r.duration_s == 0.5
        assert r.details == "ok"

    def test_default_details(self):
        r = StageResult(name="review", passed=False, duration_s=1.0)
        assert r.details == ""


class TestAssertionResult:
    def test_basic(self):
        r = AssertionResult(name="check", passed=True, message="good")
        assert r.name == "check"
        assert r.passed is True
        assert r.message == "good"


class TestScenarioResult:
    def test_defaults(self):
        r = ScenarioResult(name="happy", passed=True, duration_s=5.0)
        assert r.stages == []
        assert r.assertions == []
        assert r.artifacts == {}
        assert r.cost_usd == 0.0
        assert r.error is None

    def test_full(self):
        stage = StageResult(name="preflight", passed=True, duration_s=0.1)
        assertion = AssertionResult(name="all_pass", passed=True, message="ok")
        r = ScenarioResult(
            name="happy",
            passed=True,
            duration_s=5.0,
            stages=[stage],
            assertions=[assertion],
            artifacts={"pipeline_id": "abc123"},
            cost_usd=0.05,
            error=None,
        )
        assert len(r.stages) == 1
        assert r.artifacts["pipeline_id"] == "abc123"


class TestGauntletResult:
    def test_passed_all(self):
        s1 = ScenarioResult(name="a", passed=True, duration_s=1.0)
        s2 = ScenarioResult(name="b", passed=True, duration_s=2.0)
        g = GauntletResult(scenarios=[s1, s2], total_duration_s=3.0)
        assert g.passed is True

    def test_passed_one_fails(self):
        s1 = ScenarioResult(name="a", passed=True, duration_s=1.0)
        s2 = ScenarioResult(name="b", passed=False, duration_s=2.0)
        g = GauntletResult(scenarios=[s1, s2], total_duration_s=3.0)
        assert g.passed is False

    def test_passed_empty(self):
        g = GauntletResult(scenarios=[], total_duration_s=0.0)
        assert g.passed is True  # vacuously true


class TestScenarioConfig:
    def test_basic(self):
        c = ScenarioConfig(
            name="happy_path",
            description="Standard success scenario",
            tags=["smoke"],
            chaos_compatible=True,
        )
        assert c.name == "happy_path"
        assert c.chaos_compatible is True

    def test_defaults(self):
        c = ScenarioConfig(name="x", description="y")
        assert c.tags == []
        assert c.chaos_compatible is False
