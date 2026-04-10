"""Readiness endpoint: GET /api/readiness."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from forge.api.models.schemas import ReadinessResponse
from forge.api.security.dependencies import get_current_user
from forge.config.settings import ForgeSettings
from forge.providers.readiness import build_readiness_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/readiness", tags=["readiness"])


@router.get("")
async def get_readiness(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> ReadinessResponse:
    """Return a unified readiness report covering provider status and routing."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        from forge.core.provider_config import build_provider_registry

        settings = ForgeSettings()
        registry = build_provider_registry(settings)
    else:
        settings = ForgeSettings()

    report = build_readiness_report(settings, registry)

    return ReadinessResponse(**asdict(report))
