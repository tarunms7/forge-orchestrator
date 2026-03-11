"""Settings endpoints: get and update user settings (persisted to DB)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from forge.api.routes.tasks import get_current_user

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
    autonomy: str | None = None
    question_limit: int | None = Field(None, ge=1, le=10)
    question_timeout: int | None = Field(None, ge=60, le=7200)
    auto_pr: bool | None = None


def _get_db(request: Request):
    """Get the unified database from app state."""
    return getattr(request.app.state, "db", None)


@router.get("")
async def get_settings(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return user settings, creating defaults if needed."""
    db = _get_db(request)
    if db is not None:
        stored = await db.get_user_settings(user_id)
        if stored is not None:
            # Merge with defaults so new keys are always present
            merged = dict(DEFAULT_SETTINGS)
            merged.update(stored)
            return merged

    return dict(DEFAULT_SETTINGS)


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

    return current
