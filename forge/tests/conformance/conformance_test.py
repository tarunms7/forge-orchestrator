"""Unit tests for the conformance test framework itself.

Validates that ConformanceTest/ConformanceResult can be created and used
correctly, and that each test module exposes its registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from forge.providers.base import (
    CatalogEntry,
    ExecutionHandle,
    ExecutionMode,
    MCPServerConfig,
    OutputContract,
    ProviderEvent,
    ProviderHealthStatus,
    ProviderResult,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.tests.conformance import ConformanceResult, ConformanceTest
from forge.tests.conformance.agent_tests import AGENT_CONFORMANCE_TESTS
from forge.tests.conformance.planner_tests import PLANNER_CONFORMANCE_TESTS
from forge.tests.conformance.reviewer_tests import REVIEWER_CONFORMANCE_TESTS

if TYPE_CHECKING:
    from collections.abc import Callable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_catalog_entry(**overrides) -> CatalogEntry:
    defaults = dict(
        provider="test",
        alias="test-model",
        canonical_id="test-model-v1",
        backend="test-sdk",
        tier="supported",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=100_000,
        supports_structured_output=True,
        supports_reasoning=False,
        cost_key="test",
        validated_stages=frozenset({"agent", "planner", "reviewer"}),
    )
    defaults.update(overrides)
    return CatalogEntry(**defaults)


class _StubHandle(ExecutionHandle):
    """Execution handle that returns a canned result."""

    def __init__(self, result: ProviderResult) -> None:
        self._result = result
        self._running = True

    @property
    def is_running(self) -> bool:
        return self._running

    async def abort(self) -> None:
        self._running = False

    async def result(self) -> ProviderResult:
        self._running = False
        return self._result


class StubProvider:
    """Minimal provider that returns canned results."""

    def __init__(
        self,
        name: str = "test",
        entries: list[CatalogEntry] | None = None,
        result: ProviderResult | None = None,
    ) -> None:
        self._name = name
        self._entries = entries or [_make_catalog_entry()]
        self._result = result or ProviderResult(
            text="stub output",
            is_error=False,
            input_tokens=10,
            output_tokens=20,
            resume_state=None,
            duration_ms=100,
            provider_reported_cost_usd=0.001,
            model_canonical_id="test-model-v1",
        )

    @property
    def name(self) -> str:
        return self._name

    def catalog_entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        return ProviderHealthStatus(healthy=True, provider=self._name, details="ok")

    def start(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        execution_mode: ExecutionMode,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        workspace: WorkspaceRoots,
        max_turns: int,
        mcp_servers: list[MCPServerConfig] | None = None,
        resume_state: ResumeState | None = None,
        on_event: Callable[[ProviderEvent], None] | None = None,
    ) -> ExecutionHandle:
        return _StubHandle(self._result)

    def can_resume(self, state: ResumeState) -> bool:
        return False

    def cleanup_session(self, state: ResumeState) -> None:
        pass


# ---------------------------------------------------------------------------
# ConformanceResult tests
# ---------------------------------------------------------------------------


class TestConformanceResult:
    def test_create_passing(self):
        r = ConformanceResult(
            passed=True,
            stage="agent",
            model="sonnet",
            details="OK",
            duration_ms=42,
        )
        assert r.passed is True
        assert r.stage == "agent"
        assert r.model == "sonnet"
        assert r.duration_ms == 42
        assert r.events == []

    def test_create_failing(self):
        r = ConformanceResult(
            passed=False,
            stage="planner",
            model="opus",
            details="Missing tasks key",
            duration_ms=100,
            events=[{"kind": "text"}],
        )
        assert r.passed is False
        assert r.details == "Missing tasks key"
        assert len(r.events) == 1

    def test_default_events_list(self):
        r = ConformanceResult(passed=True, stage="reviewer", model="haiku", details="ok", duration_ms=1)
        assert r.events == []
        # Mutating one instance's events should not affect others
        r.events.append({"test": True})
        r2 = ConformanceResult(passed=True, stage="reviewer", model="haiku", details="ok", duration_ms=1)
        assert r2.events == []


# ---------------------------------------------------------------------------
# ConformanceTest ABC tests
# ---------------------------------------------------------------------------


class _ConcreteTest(ConformanceTest):
    """Concrete subclass for testing the ABC."""

    provider = "test"
    model = "test-model"
    stage = "agent"

    async def run(self, registry):
        start = self._timer()
        return self._pass(start, "test passed")


class _FailingTest(ConformanceTest):
    provider = "test"
    model = "test-model"
    stage = "agent"

    async def run(self, registry):
        start = self._timer()
        return self._fail(start, "intentional failure")


class TestConformanceTestABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ConformanceTest()

    def test_concrete_subclass_fields(self):
        t = _ConcreteTest()
        assert t.provider == "test"
        assert t.model == "test-model"
        assert t.stage == "agent"

    def test_timer_returns_positive(self):
        ms = ConformanceTest._timer()
        assert ms > 0

    def test_elapsed_non_negative(self):
        start = ConformanceTest._timer()
        elapsed = ConformanceTest._elapsed(start)
        assert elapsed >= 0

    @pytest.mark.asyncio
    async def test_pass_helper(self):
        t = _ConcreteTest()
        from unittest.mock import MagicMock

        result = await t.run(MagicMock())
        assert result.passed is True
        assert result.details == "test passed"
        assert result.stage == "agent"
        assert result.model == "test-model"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_fail_helper(self):
        t = _FailingTest()
        from unittest.mock import MagicMock

        result = await t.run(MagicMock())
        assert result.passed is False
        assert result.details == "intentional failure"


# ---------------------------------------------------------------------------
# Test registries are populated
# ---------------------------------------------------------------------------


class TestRegistries:
    def test_agent_tests_registered(self):
        assert len(AGENT_CONFORMANCE_TESTS) == 6
        for cls in AGENT_CONFORMANCE_TESTS:
            assert issubclass(cls, ConformanceTest)
            assert cls.stage == "agent"

    def test_planner_tests_registered(self):
        assert len(PLANNER_CONFORMANCE_TESTS) == 3
        for cls in PLANNER_CONFORMANCE_TESTS:
            assert issubclass(cls, ConformanceTest)
            assert cls.stage == "planner"

    def test_reviewer_tests_registered(self):
        assert len(REVIEWER_CONFORMANCE_TESTS) == 2
        for cls in REVIEWER_CONFORMANCE_TESTS:
            assert issubclass(cls, ConformanceTest)
            assert cls.stage == "reviewer"

    def test_all_tests_have_unique_names(self):
        all_tests = AGENT_CONFORMANCE_TESTS + PLANNER_CONFORMANCE_TESTS + REVIEWER_CONFORMANCE_TESTS
        names = [cls.__name__ for cls in all_tests]
        assert len(names) == len(set(names)), f"Duplicate test names: {names}"


# ---------------------------------------------------------------------------
# Test that test classes can be instantiated and have required fields
# ---------------------------------------------------------------------------


class TestAllTestsInstantiable:
    def test_agent_tests_instantiate(self):
        for cls in AGENT_CONFORMANCE_TESTS:
            instance = cls()
            assert instance.stage == "agent"
            assert hasattr(instance, "run")

    def test_planner_tests_instantiate(self):
        for cls in PLANNER_CONFORMANCE_TESTS:
            instance = cls()
            assert instance.stage == "planner"
            assert hasattr(instance, "run")

    def test_reviewer_tests_instantiate(self):
        for cls in REVIEWER_CONFORMANCE_TESTS:
            instance = cls()
            assert instance.stage == "reviewer"
            assert hasattr(instance, "run")
