"""Unified readiness report combining provider status and stage routing."""

from __future__ import annotations

from collections import Counter
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


@dataclass(frozen=True)
class RoutingAuditEntry:
    """Resolved routing audit entry for one pipeline stage."""

    stage: str
    label: str
    expected_provider: str | None
    actual_provider: str
    actual_model: str
    actual_spec: str
    backend: str
    mismatch: bool
    mismatch_detail: str | None


@dataclass(frozen=True)
class RoutingAudit:
    """Aggregated routing audit with dominant-provider mismatch detection."""

    entries: list[RoutingAuditEntry]
    has_mismatches: bool
    summary: str
    mismatch_count: int


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


def build_routing_audit(
    resolved: dict[str, ModelSpec],
    registry: ProviderRegistry,
) -> RoutingAudit:
    """Build a routing audit from resolved stage models."""
    dominant_provider = _dominant_provider(resolved)
    entries: list[RoutingAuditEntry] = []

    for snapshot_key, _, _ in _ROUTING_PLAN:
        spec = resolved[snapshot_key]
        try:
            backend = registry.get_catalog_entry(spec).backend
        except Exception:
            backend = "unknown"

        mismatch = dominant_provider is not None and spec.provider != dominant_provider
        mismatch_detail = None
        if mismatch:
            mismatch_detail = (
                f"Expected {dominant_provider} (dominant), got {spec.provider}"
            )

        entries.append(
            RoutingAuditEntry(
                stage=snapshot_key,
                label=_STAGE_LABELS.get(snapshot_key, snapshot_key),
                expected_provider=dominant_provider,
                actual_provider=spec.provider,
                actual_model=spec.model,
                actual_spec=str(spec),
                backend=backend,
                mismatch=mismatch,
                mismatch_detail=mismatch_detail,
            )
        )

    mismatch_count = sum(1 for entry in entries if entry.mismatch)
    return RoutingAudit(
        entries=entries,
        has_mismatches=mismatch_count > 0,
        summary=_build_routing_summary(resolved),
        mismatch_count=mismatch_count,
    )


def format_routing_audit_rich(audit: RoutingAudit) -> str:
    """Format a routing audit as compact Rich markup for the TUI."""
    entry_by_stage = {entry.stage: entry for entry in audit.entries}
    segments = [
        _format_rich_summary_entry("planner", "planner", entry_by_stage),
        _format_rich_summary_entry("contract_builder", "contracts", entry_by_stage),
        _format_rich_summary_entry("agent_low", "agent-low", entry_by_stage),
        _format_rich_summary_entry(("agent_medium", "agent_high"), "agent-med/high", entry_by_stage),
        _format_rich_summary_entry("reviewer", "reviewer", entry_by_stage),
    ]
    return " · ".join(segment for segment in segments if segment)


def routing_audit_to_dict(audit: RoutingAudit) -> dict[str, object]:
    """Serialize a routing audit to JSON-safe primitive types."""
    return {
        "entries": [
            {
                "stage": entry.stage,
                "label": entry.label,
                "expected_provider": entry.expected_provider,
                "actual_provider": entry.actual_provider,
                "actual_model": entry.actual_model,
                "actual_spec": entry.actual_spec,
                "backend": entry.backend,
                "mismatch": entry.mismatch,
                "mismatch_detail": entry.mismatch_detail,
            }
            for entry in audit.entries
        ],
        "has_mismatches": audit.has_mismatches,
        "summary": audit.summary,
        "mismatch_count": audit.mismatch_count,
    }


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


def _dominant_provider(resolved: dict[str, ModelSpec]) -> str | None:
    """Return the dominant provider by stage count, or None on a tie/empty input."""
    if not resolved:
        return None

    counts = Counter(spec.provider for spec in resolved.values())
    if not counts:
        return None

    [(provider, count), *rest] = counts.most_common()
    if rest and rest[0][1] == count:
        return None
    return provider


def _provider_display_name(provider: str) -> str:
    """Map provider keys to user-facing names."""
    if provider == "openai":
        return "Codex"
    if provider == "claude":
        return "Claude"
    return provider.title()


def _build_routing_summary(resolved: dict[str, ModelSpec]) -> str:
    """Build the compact one-line summary used across TUI and API surfaces."""
    planner = _provider_display_name(resolved["planner"].provider)
    contracts = _provider_display_name(resolved["contract_builder"].provider)
    agent_low = _provider_display_name(resolved["agent_low"].provider)
    agent_med_high = _provider_display_name(resolved["agent_medium"].provider)
    reviewer = _provider_display_name(resolved["reviewer"].provider)
    return (
        f"Planner: {planner} | "
        f"Contracts: {contracts} | "
        f"Agent L: {agent_low} | "
        f"Agent M/H: {agent_med_high} | "
        f"Reviewer: {reviewer}"
    )


def _provider_rich(provider: str) -> str:
    """Render a provider name with the canonical color for the TUI."""
    color = "#22c55e" if provider == "claude" else "#58a6ff" if provider == "openai" else "#c9d1d9"
    return f"[{color}]{_provider_display_name(provider)}[/]"


def _format_rich_summary_entry(
    stage_or_stages: str | tuple[str, ...],
    label: str,
    entry_by_stage: dict[str, RoutingAuditEntry],
) -> str:
    """Render a summary segment, adding a warning marker for mismatches."""
    if isinstance(stage_or_stages, tuple):
        entries = [entry_by_stage[stage] for stage in stage_or_stages]
        provider = entries[0].actual_provider
        mismatch = any(entry.mismatch for entry in entries)
    else:
        entry = entry_by_stage[stage_or_stages]
        provider = entry.actual_provider
        mismatch = entry.mismatch

    marker = " [yellow]![/]" if mismatch else ""
    return f"{_provider_rich(provider)} {label}{marker}"


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
