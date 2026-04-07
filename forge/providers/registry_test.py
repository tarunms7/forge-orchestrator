"""Tests for ProviderRegistry."""

from __future__ import annotations

import pytest

from forge.providers.base import (
    CatalogEntry,
    ModelSpec,
    ProviderHealthStatus,
)
from forge.providers.registry import CatalogEntryNotFoundError, ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers — lightweight mock provider
# ---------------------------------------------------------------------------


def _make_entry(provider: str, alias: str, backend: str = "test-sdk", **overrides) -> CatalogEntry:
    defaults = dict(
        provider=provider,
        alias=alias,
        canonical_id=f"{provider}-{alias}-v1",
        backend=backend,
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=100_000,
        supports_structured_output=False,
        supports_reasoning=False,
        cost_key=f"{provider}:{alias}",
        validated_stages=frozenset(["agent", "planner"]),
    )
    defaults.update(overrides)
    return CatalogEntry(**defaults)


class FakeProvider:
    """Minimal ProviderProtocol impl for tests."""

    def __init__(self, name: str, entries: list[CatalogEntry], healthy: bool = True) -> None:
        self._name = name
        self._entries = entries
        self._healthy = healthy

    @property
    def name(self) -> str:
        return self._name

    def catalog_entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        if self._healthy:
            return ProviderHealthStatus(
                healthy=True,
                provider=self._name,
                details=f"{backend or 'default'} ok",
            )
        return ProviderHealthStatus(
            healthy=False,
            provider=self._name,
            details="",
            errors=[f"{backend or 'default'} unavailable"],
        )

    def start(self, *args, **kwargs):
        raise NotImplementedError

    def can_resume(self, state):
        return False

    def cleanup_session(self, state):
        pass


def _make_settings():
    """Create a minimal ForgeSettings for testing."""
    from forge.config.settings import ForgeSettings

    return ForgeSettings()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderRegistration:
    def test_register_and_get_provider(self):
        reg = ProviderRegistry(_make_settings())
        provider = FakeProvider("test", [_make_entry("test", "model-a")])
        reg.register(provider)

        assert reg.get_provider("test") is provider

    def test_get_provider_not_found_raises(self):
        reg = ProviderRegistry(_make_settings())
        with pytest.raises(KeyError, match="not registered"):
            reg.get_provider("nonexistent")

    def test_register_indexes_catalog_entries(self):
        entry = _make_entry("test", "model-a")
        provider = FakeProvider("test", [entry])
        reg = ProviderRegistry(_make_settings())
        reg.register(provider)

        result = reg.get_catalog_entry(ModelSpec("test", "model-a"))
        assert result is entry

    def test_multiple_providers(self):
        reg = ProviderRegistry(_make_settings())
        p1 = FakeProvider("alpha", [_make_entry("alpha", "m1")])
        p2 = FakeProvider("beta", [_make_entry("beta", "m2")])
        reg.register(p1)
        reg.register(p2)

        assert len(reg.all_providers()) == 2
        assert len(reg.all_catalog_entries()) == 2


class TestModelLookup:
    def test_get_for_model(self):
        reg = ProviderRegistry(_make_settings())
        provider = FakeProvider("test", [_make_entry("test", "fast")])
        reg.register(provider)

        result = reg.get_for_model(ModelSpec("test", "fast"))
        assert result is provider

    def test_get_catalog_entry_not_found(self):
        reg = ProviderRegistry(_make_settings())
        spec = ModelSpec("ghost", "phantom")
        with pytest.raises(CatalogEntryNotFoundError) as exc_info:
            reg.get_catalog_entry(spec)
        assert exc_info.value.spec == spec

    def test_validate_model_true(self):
        reg = ProviderRegistry(_make_settings())
        reg.register(FakeProvider("test", [_make_entry("test", "ok")]))
        assert reg.validate_model(ModelSpec("test", "ok")) is True

    def test_validate_model_false(self):
        reg = ProviderRegistry(_make_settings())
        assert reg.validate_model(ModelSpec("nope", "nope")) is False


class TestStageValidation:
    def test_validate_model_for_stage_valid(self):
        reg = ProviderRegistry(_make_settings())
        reg.register(
            FakeProvider("test", [_make_entry("test", "m", validated_stages=frozenset(["agent"]))])
        )

        issues = reg.validate_model_for_stage(ModelSpec("test", "m"), "agent")
        assert issues == []

    def test_validate_model_for_stage_not_in_catalog(self):
        reg = ProviderRegistry(_make_settings())
        issues = reg.validate_model_for_stage(ModelSpec("nope", "nope"), "agent")
        assert any("not found" in i for i in issues)

    def test_validate_model_for_stage_missing_validation(self):
        reg = ProviderRegistry(_make_settings())
        entry = _make_entry("test", "m", validated_stages=frozenset(["planner"]))
        reg.register(FakeProvider("test", [entry]))

        issues = reg.validate_model_for_stage(ModelSpec("test", "m"), "agent")
        assert any("WARNING" in i for i in issues)


class TestPreflight:
    def test_preflight_all_healthy(self):
        reg = ProviderRegistry(_make_settings())
        reg.register(FakeProvider("a", [_make_entry("a", "m1")], healthy=True))
        reg.register(FakeProvider("b", [_make_entry("b", "m2")], healthy=True))

        results = reg.preflight_all()
        assert results["a"].healthy is True
        assert results["b"].healthy is True

    def test_preflight_all_unhealthy(self):
        reg = ProviderRegistry(_make_settings())
        reg.register(FakeProvider("sick", [_make_entry("sick", "m")], healthy=False))

        results = reg.preflight_all()
        assert results["sick"].healthy is False
        assert len(results["sick"].errors) > 0

    def test_preflight_for_pipeline(self):
        reg = ProviderRegistry(_make_settings())
        reg.register(FakeProvider("a", [_make_entry("a", "m1", backend="sdk-a")], healthy=True))
        reg.register(FakeProvider("b", [_make_entry("b", "m2", backend="sdk-b")], healthy=True))

        # Only check provider "a"
        resolved = {"planner": ModelSpec("a", "m1")}
        results = reg.preflight_for_pipeline(resolved)
        assert "a" in results
        assert "b" not in results

    def test_preflight_for_pipeline_unregistered_provider(self):
        reg = ProviderRegistry(_make_settings())
        reg.register(FakeProvider("a", [_make_entry("a", "m1")]))

        # Reference a model whose provider is registered but model isn't in catalog
        resolved = {"agent": ModelSpec("ghost", "phantom")}
        results = reg.preflight_for_pipeline(resolved)
        # ghost provider not in catalog, so no entry found, skip silently
        assert len(results) == 0


class TestCatalogEntryNotFoundError:
    def test_exception_attributes(self):
        spec = ModelSpec("openai", "gpt-99")
        err = CatalogEntryNotFoundError(spec)
        assert err.spec == spec
        assert "gpt-99" in str(err)
        assert isinstance(err, KeyError)
