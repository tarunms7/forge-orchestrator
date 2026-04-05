"""Provider registry — central lookup for providers and catalog entries.

Thread-safe registry that indexes all providers and their catalog entries,
supports model validation, stage validation, and health-check preflight.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from forge.providers.base import (
    CatalogEntry,
    ModelSpec,
    ProviderHealthStatus,
)
from forge.providers.catalog import validate_model_for_stage

if TYPE_CHECKING:
    from forge.config.settings import ForgeSettings
    from forge.providers.base import ProviderProtocol

logger = logging.getLogger("forge.providers.registry")


# ---------------------------------------------------------------------------
# CatalogEntryNotFoundError
# ---------------------------------------------------------------------------


class CatalogEntryNotFoundError(KeyError):
    """Raised when a ModelSpec has no matching CatalogEntry in the registry."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self.message = f"No catalog entry found for model spec '{spec}'"
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Central registry of providers and their catalog entries.

    Thread-safe. Injected into daemon, planner, executor, and API routes.
    """

    def __init__(self, settings: ForgeSettings) -> None:
        self._settings = settings
        self._providers: dict[str, ProviderProtocol] = {}
        self._catalog: dict[str, CatalogEntry] = {}

    def register(self, provider: ProviderProtocol) -> None:
        """Register a provider and index all its catalog entries by str(entry.spec)."""
        self._providers[provider.name] = provider
        for entry in provider.catalog_entries():
            key = str(entry.spec)
            self._catalog[key] = entry

    def get_provider(self, name: str) -> ProviderProtocol:
        """Get provider by name. Raises KeyError if not found."""
        try:
            return self._providers[name]
        except KeyError:
            raise KeyError(f"Provider '{name}' is not registered")

    def get_for_model(self, spec: ModelSpec) -> ProviderProtocol:
        """Get the provider that owns the given model spec."""
        return self.get_provider(spec.provider)

    def get_catalog_entry(self, spec: ModelSpec) -> CatalogEntry:
        """Get catalog entry for a model spec. Raises CatalogEntryNotFoundError if unknown."""
        key = str(spec)
        entry = self._catalog.get(key)
        if entry is None:
            raise CatalogEntryNotFoundError(spec)
        return entry

    def all_providers(self) -> list[ProviderProtocol]:
        """Return all registered providers."""
        return list(self._providers.values())

    def all_catalog_entries(self) -> list[CatalogEntry]:
        """Return all catalog entries across all providers."""
        return list(self._catalog.values())

    def validate_model(self, spec: ModelSpec) -> bool:
        """Return True if spec is in the catalog."""
        return str(spec) in self._catalog

    def validate_model_for_stage(self, spec: ModelSpec, stage: str) -> list[str]:
        """Return list of validation warnings/errors. Empty list = valid."""
        entry = self._catalog.get(str(spec))
        if entry is None:
            return [f"BLOCKED: model '{spec}' not found in catalog"]
        return validate_model_for_stage(entry, stage)

    def preflight_all(self) -> dict[str, ProviderHealthStatus]:
        """Health-check all registered providers.

        Checks each provider once per unique backend it supports.
        Keys are provider names.
        """
        results: dict[str, ProviderHealthStatus] = {}
        for name, provider in self._providers.items():
            # Collect unique backends for this provider
            backends = {e.backend for e in provider.catalog_entries()}
            errors: list[str] = []
            details_parts: list[str] = []

            for backend in sorted(backends):
                status = provider.health_check(backend=backend)
                if not status.healthy:
                    errors.extend(status.errors)
                details_parts.append(status.details)

            results[name] = ProviderHealthStatus(
                healthy=len(errors) == 0,
                provider=name,
                details="; ".join(details_parts),
                errors=errors,
            )
        return results

    def preflight_for_pipeline(
        self, resolved_models: dict[str, ModelSpec]
    ) -> dict[str, ProviderHealthStatus]:
        """Health-check only providers/backends needed for a specific pipeline.

        resolved_models maps stage names to ModelSpec instances.
        Keys in result are provider names.
        """
        # Collect unique (provider, backend) pairs needed
        needed: dict[str, set[str]] = {}  # provider_name -> set of backends
        for spec in resolved_models.values():
            entry = self._catalog.get(str(spec))
            if entry is None:
                continue
            needed.setdefault(spec.provider, set()).add(entry.backend)

        results: dict[str, ProviderHealthStatus] = {}
        for provider_name, backends in needed.items():
            provider = self._providers.get(provider_name)
            if provider is None:
                results[provider_name] = ProviderHealthStatus(
                    healthy=False,
                    provider=provider_name,
                    details="",
                    errors=[f"Provider '{provider_name}' not registered"],
                )
                continue

            errors: list[str] = []
            details_parts: list[str] = []
            for backend in sorted(backends):
                status = provider.health_check(backend=backend)
                if not status.healthy:
                    errors.extend(status.errors)
                details_parts.append(status.details)

            results[provider_name] = ProviderHealthStatus(
                healthy=len(errors) == 0,
                provider=provider_name,
                details="; ".join(details_parts),
                errors=errors,
            )
        return results
