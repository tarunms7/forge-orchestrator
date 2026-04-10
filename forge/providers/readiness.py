"""Unified readiness report combining provider status and stage routing."""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.config.settings import ForgeSettings
from forge.core.provider_config import (
    _ROUTING_PLAN,
    _SNAPSHOT_TO_REASONING_SETTING,
    resolve_pipeline_models,
)
from forge.providers.base import ModelSpec
from forge.providers.registry import ProviderRegistry
from forge.providers.status import (
    ProviderConnectionStatus,
    collect_provider_connection_statuses,
)

# ---------------------------------------------------------------------------
# Stage labels
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    "planner": "Planner",
    "agent_low": "Agent Low",
    "agent_medium": "Agent Medium",
    "agent_high": "Agent High",
    "reviewer": "Reviewer",
    "contract_builder": "Contract Builder",
    "ci_fix": "CI Fix",
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderReadinessEntry:
    """Per-provider connection and install status for the readiness report."""

    ui_key: str
    provider_key: str
    display_name: str
    installed: bool
    connected: bool
    status: str
    detail: str
    auth_source: str | None = None
    blocking_issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StageRoutingEntry:
    """Per-stage model routing entry with resolved model and validation warnings."""

    stage: str
    label: str
    provider: str
    model: str
    spec: str
    backend: str
    reasoning_effort: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReadinessReport:
    """Aggregated readiness report for providers and routing."""

    providers: list[ProviderReadinessEntry]
    routing: list[StageRoutingEntry]
    blocking_issues: list[str]
    warnings: list[str]
    ready: bool


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_readiness_report(
    settings: ForgeSettings,
    registry: ProviderRegistry,
) -> ReadinessReport:
    """Build a unified readiness report from provider status and routing state.

    Parameters
    ----------
    settings:
        Fully configured ForgeSettings (ensure_routing_defaults +
        normalize_routing_settings already called).
    registry:
        Built provider registry from build_provider_registry(settings).

    Returns
    -------
    ReadinessReport with providers, routing, blocking_issues, warnings, ready.
    """
    # -- 1. Provider connection statuses ------------------------------------
    conn_statuses = collect_provider_connection_statuses()
    provider_entries = _build_provider_entries(conn_statuses)

    # -- 2. Routing entries -------------------------------------------------
    resolved = resolve_pipeline_models(settings, registry)
    routing_entries = _build_routing_entries(settings, registry, resolved)

    # -- 3. Aggregate issues ------------------------------------------------
    all_blocking: list[str] = []
    all_warnings: list[str] = []

    for pe in provider_entries:
        all_blocking.extend(pe.blocking_issues)

    # Check which providers are actually used by routing
    used_providers: set[str] = set()
    for snapshot_key in resolved:
        used_providers.add(resolved[snapshot_key].provider)

    # Provider not connected but used by a stage
    provider_by_key: dict[str, ProviderConnectionStatus] = {}
    for cs in conn_statuses.values():
        provider_by_key[cs.provider_key] = cs

    for provider_key in used_providers:
        cs = provider_by_key.get(provider_key)
        if cs is None:
            continue
        if not cs.installed:
            issue = f"Provider {provider_key} is not installed but used by stage " + ", ".join(
                sk for sk, spec in resolved.items() if spec.provider == provider_key
            )
            if issue not in all_blocking:
                all_blocking.append(issue)
        elif not cs.connected:
            issue = f"Provider {provider_key} is not connected but used by stage " + ", ".join(
                sk for sk, spec in resolved.items() if spec.provider == provider_key
            )
            if issue not in all_blocking:
                all_blocking.append(issue)

    for re in routing_entries:
        for w in re.warnings:
            if w.startswith("BLOCKED:"):
                if w not in all_blocking:
                    all_blocking.append(w)
            else:
                if w not in all_warnings:
                    all_warnings.append(w)

    return ReadinessReport(
        providers=provider_entries,
        routing=routing_entries,
        blocking_issues=all_blocking,
        warnings=all_warnings,
        ready=len(all_blocking) == 0,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_provider_entries(
    conn_statuses: dict[str, ProviderConnectionStatus],
) -> list[ProviderReadinessEntry]:
    """Convert raw connection statuses into readiness entries."""
    entries: list[ProviderReadinessEntry] = []
    for cs in conn_statuses.values():
        blocking: list[str] = []
        if not cs.installed:
            blocking.append(f"Provider {cs.provider_key} is not installed")
        entries.append(
            ProviderReadinessEntry(
                ui_key=cs.ui_key,
                provider_key=cs.provider_key,
                display_name=cs.display_name,
                installed=cs.installed,
                connected=cs.connected,
                auth_source=cs.auth_source,
                status=cs.status,
                detail=cs.detail,
                blocking_issues=blocking,
            )
        )
    return entries


def _build_routing_entries(
    settings: ForgeSettings,
    registry: ProviderRegistry,
    resolved: dict[str, ModelSpec],
) -> list[StageRoutingEntry]:
    """Build per-stage routing entries with validation warnings."""
    entries: list[StageRoutingEntry] = []
    for snapshot_key, stage, complexity in _ROUTING_PLAN:
        spec = resolved[snapshot_key]
        try:
            cat_entry = registry.get_catalog_entry(spec)
            backend = cat_entry.backend
        except Exception:
            backend = "unknown"

        warnings = registry.validate_model_for_stage(spec, stage)

        reasoning_attr = _SNAPSHOT_TO_REASONING_SETTING.get(snapshot_key)
        reasoning_effort: str | None = None
        if reasoning_attr:
            reasoning_effort = getattr(settings, reasoning_attr, None)

        entries.append(
            StageRoutingEntry(
                stage=snapshot_key,
                label=_STAGE_LABELS.get(snapshot_key, snapshot_key),
                provider=spec.provider,
                model=spec.model,
                spec=str(spec),
                backend=backend,
                reasoning_effort=reasoning_effort,
                warnings=warnings,
            )
        )
    return entries
