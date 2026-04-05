"""Settings endpoints: get and update user settings (persisted to DB)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from forge.api.models.schemas import CatalogEntrySummary
from forge.api.routes.providers import _catalog_entry_to_summary
from forge.api.security.dependencies import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])

# Default settings for new users
DEFAULT_SETTINGS: dict = {
    "max_agents": 2,
    "timeout": 600,
    "max_retries": 5,
    "model_strategy": "auto",
    "planner_model": "opus",
    "agent_model_low": "sonnet",
    "agent_model_medium": "opus",
    "agent_model_high": "opus",
    "reviewer_model": "sonnet",
    "contract_builder_model": "opus",
    "ci_fix_model": "sonnet",
    "autonomy": "balanced",
    "question_limit": 3,
    "question_timeout": 1800,
    "auto_pr": False,
}


class UpdateSettingsRequest(BaseModel):
    """Request body for updating settings."""

    max_agents: int | None = Field(None, ge=1, le=16)
    timeout: int | None = Field(None, ge=30, le=3600)
    max_retries: int | None = Field(None, ge=0, le=10)
    model_strategy: str | None = None
    planner_model: str | None = None
    agent_model_low: str | None = None
    agent_model_medium: str | None = None
    agent_model_high: str | None = None
    reviewer_model: str | None = None
    contract_builder_model: str | None = None
    ci_fix_model: str | None = None
    autonomy: str | None = None
    question_limit: int | None = Field(None, ge=1, le=10)
    question_timeout: int | None = Field(None, ge=60, le=7200)
    auto_pr: bool | None = None


def _get_db(request: Request):
    """Get the unified database from app state."""
    return getattr(request.app.state, "db", None)


def _get_provider_extras(request: Request) -> dict:
    """Build provider-aware fields for the settings response."""
    from forge.config.settings import ForgeSettings

    settings = ForgeSettings()
    openai_enabled = settings.openai_enabled

    registry = getattr(request.app.state, "registry", None)
    available_providers: list[str] = []
    catalog: list[CatalogEntrySummary] = []

    if registry is not None:
        available_providers = [p.name for p in registry.all_providers()]
        catalog = [_catalog_entry_to_summary(e) for e in registry.all_catalog_entries()]
    else:
        # Fallback: build from static catalog
        from forge.providers.catalog import FORGE_MODEL_CATALOG

        seen_providers: set[str] = set()
        for entry in FORGE_MODEL_CATALOG:
            if entry.provider not in seen_providers:
                available_providers.append(entry.provider)
                seen_providers.add(entry.provider)
            catalog.append(_catalog_entry_to_summary(entry))

    return {
        "openai_enabled": openai_enabled,
        "available_providers": available_providers,
        "catalog": [c.model_dump() for c in catalog],
    }


@router.get("")
async def get_settings(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return user settings, creating defaults if needed."""
    db = _get_db(request)
    result = dict(DEFAULT_SETTINGS)
    if db is not None:
        stored = await db.get_user_settings(user_id)
        if stored is not None:
            # Merge with defaults so new keys are always present
            result.update(stored)

    # Append provider-aware fields
    result.update(_get_provider_extras(request))
    return result


@router.put("")
async def update_settings(
    body: UpdateSettingsRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Update user settings. Only provided fields are updated."""
    db = _get_db(request)

    # Load existing or start from defaults
    current = dict(DEFAULT_SETTINGS)
    if db is not None:
        stored = await db.get_user_settings(user_id)
        if stored is not None:
            current.update(stored)

    # Apply updates (only non-None fields)
    updates = body.model_dump(exclude_none=True)
    current.update(updates)

    # Persist
    if db is not None:
        await db.save_user_settings(user_id, current)

    # Append provider-aware fields
    current.update(_get_provider_extras(request))
    return current
