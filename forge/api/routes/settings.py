"""Settings endpoints: get and update user settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from forge.api.routes.tasks import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])

# Default settings for new users
_DEFAULT_SETTINGS = {
    "max_agents": 4,
    "timeout": 300,
    "browser_notifications": False,
    "webhook_url": "",
    "default_execution_target": "local",
}


class UpdateSettingsRequest(BaseModel):
    """Request body for updating settings."""

    max_agents: int | None = Field(None, ge=1, le=16)
    timeout: int | None = Field(None, ge=30, le=3600)
    browser_notifications: bool | None = None
    webhook_url: str | None = None
    default_execution_target: str | None = None


def _get_settings_store(request: Request) -> dict:
    """Get or initialise the in-memory settings store on app.state."""
    if not hasattr(request.app.state, "user_settings"):
        request.app.state.user_settings = {}
    return request.app.state.user_settings


@router.get("")
async def get_settings(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return user settings, creating defaults if needed."""
    store = _get_settings_store(request)

    if user_id not in store:
        store[user_id] = dict(_DEFAULT_SETTINGS)

    return store[user_id]


@router.put("")
async def update_settings(
    body: UpdateSettingsRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Update user settings. Only provided fields are updated."""
    store = _get_settings_store(request)

    if user_id not in store:
        store[user_id] = dict(_DEFAULT_SETTINGS)

    updates = body.model_dump(exclude_none=True)
    store[user_id].update(updates)

    return store[user_id]
