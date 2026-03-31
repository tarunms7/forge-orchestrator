"""Tests for forge.gauntlet.models — Pydantic model serialization and logic."""

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

    def test_serialize_round_trip(self):
        sr = StageResult(name="preflight", passed=True, duration_s=1.23, details="ok")
        data = sr.model_dump()
        restored = StageResult.model_validate(data)
        assert restored == sr

    def test_json_round_trip(self):
        sr = StageResult(name="review", passed=False, duration_s=0.5, details="fail")
        json_str = sr.model_dump_json()
        restored = StageResult.model_validate_json(json_str)
        assert restored == sr

    def test_empty_details_explicit(self):
        sr = StageResult(name="contracts", passed=True, duration_s=0.1, details="")
        assert sr.details == ""

    def test_zero_duration(self):
        sr = StageResult(name="planning", passed=True, duration_s=0.0)
        assert sr.duration_s == 0.0


class TestAssertionResult:
    def test_basic(self):
        r = AssertionResult(name="check", passed=True, message="good")
        assert r.name == "check"
        assert r.passed is True
        assert r.message == "good"

    def test_serialize_round_trip(self):
        ar = AssertionResult(name="check_1", passed=True, message="all good")
        data = ar.model_dump()
        restored = AssertionResult.model_validate(data)
        assert restored == ar

    def test_empty_message(self):
        ar = AssertionResult(name="check_2", passed=False, message="")
        assert ar.message == ""

    def test_json_round_trip(self):
        ar = AssertionResult(name="x", passed=False, message="failed")
        json_str = ar.model_dump_json()
        restored = AssertionResult.model_validate_json(json_str)
        assert restored == ar


class TestScenarioResult:
    def test_defaults(self):
        r = ScenarioResult(name="happy", passed=True, duration_s=5.0)
        assert r.stages == []
        assert r.assertions == []
        assert r.artifacts == {}
        assert r.cost_usd == 0.0
        assert r.error is None

    def test_all_fields_populated(self):
        sr = ScenarioResult(
            name="happy_path",
            passed=True,
            duration_s=5.0,
            stages=[
                StageResult(name="preflight", passed=True, duration_s=0.1),
                StageResult(name="planning", passed=True, duration_s=0.2),
            ],
            assertions=[
                AssertionResult(name="a1", passed=True, message="ok"),
            ],
            artifacts={"workspace_dir": "/tmp/test", "stage_count": "2"},
            cost_usd=0.05,
            error=None,
        )
        data = sr.model_dump()
        restored = ScenarioResult.model_validate(data)
        assert restored == sr
        assert restored.cost_usd == 0.05
        assert restored.error is None
        assert len(restored.stages) == 2
        assert len(restored.assertions) == 1
        assert restored.artifacts["workspace_dir"] == "/tmp/test"

    def test_with_error(self):
        sr = ScenarioResult(
            name="broken",
            passed=False,
            duration_s=1.0,
            error="RuntimeError: something broke",
        )
        assert sr.error == "RuntimeError: something broke"

    def test_json_round_trip(self):
        sr = ScenarioResult(
            name="test",
            passed=True,
            duration_s=2.5,
            stages=[StageResult(name="preflight", passed=True, duration_s=0.1)],
            assertions=[AssertionResult(name="a", passed=True, message="m")],
            artifacts={"key": "value"},
            cost_usd=0.01,
        )
        json_str = sr.model_dump_json()
        restored = ScenarioResult.model_validate_json(json_str)
        assert restored == sr


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

    def test_passed_single_failing(self):
        g = GauntletResult(
            scenarios=[ScenarioResult(name="x", passed=False, duration_s=0.5)],
            total_duration_s=0.5,
        )
        assert g.passed is False

    def test_serialize_round_trip(self):
        gr = GauntletResult(
            scenarios=[
                ScenarioResult(name="s1", passed=True, duration_s=1.0),
            ],
            total_duration_s=1.0,
        )
        data = gr.model_dump()
        restored = GauntletResult.model_validate(data)
        assert restored.scenarios == gr.scenarios
        assert restored.total_duration_s == gr.total_duration_s

    def test_json_round_trip(self):
        gr = GauntletResult(
            scenarios=[
                ScenarioResult(name="s1", passed=True, duration_s=1.0),
            ],
            total_duration_s=1.0,
        )
        json_str = gr.model_dump_json()
        restored = GauntletResult.model_validate_json(json_str)
        assert restored.scenarios == gr.scenarios

    def test_passed_property_not_in_dump(self):
        """passed is a @property, not a stored field — verify model_dump omits it."""
        gr = GauntletResult(
            scenarios=[ScenarioResult(name="a", passed=True, duration_s=1.0)],
            total_duration_s=1.0,
        )
        data = gr.model_dump()
        assert "passed" not in data


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

    def test_serialize_round_trip(self):
        cfg = ScenarioConfig(
            name="happy_path",
            description="Full pipeline success",
            tags=["smoke"],
            chaos_compatible=False,
        )
        data = cfg.model_dump()
        restored = ScenarioConfig.model_validate(data)
        assert restored == cfg
