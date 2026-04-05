"""Provider listing endpoint: GET /api/providers."""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, Request

from forge.api.models.schemas import (
    CatalogCapabilities,
    CatalogEntrySummary,
    ObservedHealthEntry,
    ProviderListResponse,
    ProviderSummary,
)
from forge.api.security.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])


def _catalog_entry_to_summary(entry) -> CatalogEntrySummary:
    """Convert a CatalogEntry dataclass to an API summary."""
    return CatalogEntrySummary(
        alias=entry.alias,
        canonical_id=entry.canonical_id,
        backend=entry.backend,
        tier=entry.tier,
        capabilities=CatalogCapabilities(
            can_use_tools=entry.can_use_tools,
            can_stream=entry.can_stream,
            can_resume_session=entry.can_resume_session,
            can_run_shell=entry.can_run_shell,
            can_edit_files=entry.can_edit_files,
            supports_mcp_servers=entry.supports_mcp_servers,
            max_context_tokens=entry.max_context_tokens,
            supports_structured_output=entry.supports_structured_output,
            supports_reasoning=entry.supports_reasoning,
        ),
        validated_stages=sorted(entry.validated_stages),
    )


def _load_observed_health() -> list[ObservedHealthEntry]:
    """Load observed health from health_state.json if it exists."""
    health_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "providers", "health_state.json"
    )
    health_path = os.path.normpath(health_path)
    if not os.path.isfile(health_path):
        return []
    try:
        with open(health_path, encoding="utf-8") as f:
            data = json.load(f)
        entries = []
        for item in data if isinstance(data, list) else []:
            entries.append(
                ObservedHealthEntry(
                    spec=item.get("spec", ""),
                    last_checked=item.get("last_checked", ""),
                    stages_passing=item.get("stages_passing", []),
                    stages_failing=item.get("stages_failing", []),
                )
            )
        return entries
    except Exception:
        logger.warning("Failed to load health_state.json", exc_info=True)
        return []


@router.get("")
async def list_providers(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> ProviderListResponse:
    """List registered providers with models, capabilities, and observed health."""
    registry = getattr(request.app.state, "registry", None)

    providers: list[ProviderSummary] = []
    if registry is not None:
        for provider in registry.all_providers():
            models = [
                _catalog_entry_to_summary(entry)
                for entry in provider.catalog_entries()
            ]
            providers.append(ProviderSummary(name=provider.name, models=models))
    else:
        # Fallback: build from static catalog when no registry is wired
        from forge.providers.catalog import FORGE_MODEL_CATALOG

        by_provider: dict[str, list[CatalogEntrySummary]] = {}
        for entry in FORGE_MODEL_CATALOG:
            summary = _catalog_entry_to_summary(entry)
            by_provider.setdefault(entry.provider, []).append(summary)
        for name, models in sorted(by_provider.items()):
            providers.append(ProviderSummary(name=name, models=models))

    observed_health = _load_observed_health()

    return ProviderListResponse(providers=providers, observed_health=observed_health)
