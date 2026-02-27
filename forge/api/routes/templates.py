"""Template REST endpoints: list, create, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from forge.api.security.dependencies import get_current_user
from forge.api.services.template_service import TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])


def _get_service() -> TemplateService:
    """Return a TemplateService using the default templates directory."""
    return TemplateService()


# ── Schemas ───────────────────────────────────────────────────────────


class CreateTemplateRequest(BaseModel):
    name: str
    description: str
    category: str


class TemplateOut(BaseModel):
    name: str
    description: str
    category: str


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("", response_model=list[TemplateOut])
async def list_templates() -> list[TemplateOut]:
    """List all available templates (no auth required)."""
    svc = _get_service()
    return [TemplateOut(**t) for t in svc.list_all()]


@router.post("", response_model=TemplateOut, status_code=201)
async def create_template(
    body: CreateTemplateRequest,
    user_id: str = Depends(get_current_user),
) -> TemplateOut:
    """Create a new template (requires auth)."""
    svc = _get_service()
    t = svc.save(name=body.name, description=body.description, category=body.category)
    return TemplateOut(**t)


@router.delete("/{name}", status_code=204)
async def delete_template(
    name: str,
    user_id: str = Depends(get_current_user),
) -> None:
    """Delete a template by name (requires auth)."""
    svc = _get_service()
    deleted = svc.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
