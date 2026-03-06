"""Template REST endpoints: CRUD for built-in and user templates."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.models.schemas import (
    CreateUserTemplateRequest,
    ReviewConfigSchema,
    TemplateListResponse,
    TemplateResponse,
    UpdateUserTemplateRequest,
)
from forge.api.security.dependencies import get_current_user
from forge.core.templates import BUILTIN_TEMPLATES, get_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


def _get_db(request: Request):
    """Get the Database instance from app state."""
    return getattr(request.app.state, "forge_db", None)


def _builtin_to_response(tmpl) -> TemplateResponse:
    """Convert a built-in PipelineTemplate dataclass to a TemplateResponse."""
    return TemplateResponse(
        id=tmpl.id,
        name=tmpl.name,
        description=tmpl.description,
        icon=tmpl.icon,
        model_strategy=tmpl.model_strategy,
        planner_prompt_modifier=tmpl.planner_prompt_modifier,
        agent_prompt_modifier=tmpl.agent_prompt_modifier,
        review_config=ReviewConfigSchema(
            skip_l2=tmpl.review_config.skip_l2,
            extra_review_pass=tmpl.review_config.extra_review_pass,
            custom_review_focus=tmpl.review_config.custom_review_focus,
        ),
        build_cmd=tmpl.build_cmd,
        test_cmd=tmpl.test_cmd,
        max_tasks=tmpl.max_tasks,
        default_complexity=tmpl.default_complexity,
        is_builtin=True,
        user_id=None,
        created_at=None,
    )


def _user_row_to_response(row) -> TemplateResponse:
    """Convert a UserTemplateRow to a TemplateResponse."""
    config = json.loads(row.config_json) if row.config_json else {}
    review_raw = config.get("review_config", {})
    return TemplateResponse(
        id=row.id,
        name=row.name,
        description=config.get("description", ""),
        icon=config.get("icon", "📋"),
        model_strategy=config.get("model_strategy", "auto"),
        planner_prompt_modifier=config.get("planner_prompt_modifier", ""),
        agent_prompt_modifier=config.get("agent_prompt_modifier", ""),
        review_config=ReviewConfigSchema(
            skip_l2=review_raw.get("skip_l2", False),
            extra_review_pass=review_raw.get("extra_review_pass", False),
            custom_review_focus=review_raw.get("custom_review_focus", ""),
        ),
        build_cmd=config.get("build_cmd"),
        test_cmd=config.get("test_cmd"),
        max_tasks=config.get("max_tasks"),
        default_complexity=config.get("default_complexity"),
        is_builtin=False,
        user_id=row.user_id,
        created_at=row.created_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    request: Request,
    user_id: str | None = Depends(get_current_user),
) -> TemplateListResponse:
    """List all templates: built-in + user-owned.

    Built-in templates are always returned.  User templates require auth
    and are scoped to the authenticated user.
    """
    builtin = [_builtin_to_response(t) for t in BUILTIN_TEMPLATES.values()]

    user_templates: list[TemplateResponse] = []
    if user_id:
        db = _get_db(request)
        if db is not None:
            rows = await db.list_user_templates(user_id)
            user_templates = [_user_row_to_response(r) for r in rows]

    return TemplateListResponse(builtin=builtin, user=user_templates)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template_by_id(
    template_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> TemplateResponse:
    """Get a single template by ID (built-in or user-owned)."""
    # Check built-in first
    builtin = get_template(template_id)
    if builtin is not None:
        return _builtin_to_response(builtin)

    # Check user templates in DB
    db = _get_db(request)
    if db is not None:
        row = await db.get_user_template(template_id)
        if row is not None and row.user_id == user_id:
            return _user_row_to_response(row)

    raise HTTPException(status_code=404, detail="Template not found")


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: CreateUserTemplateRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> TemplateResponse:
    """Create a new user-owned template (auth required)."""
    db = _get_db(request)
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    config = {
        "description": body.description,
        "icon": body.icon,
        "model_strategy": body.model_strategy,
        "planner_prompt_modifier": body.planner_prompt_modifier,
        "agent_prompt_modifier": body.agent_prompt_modifier,
        "review_config": body.review_config.model_dump(),
        "build_cmd": body.build_cmd,
        "test_cmd": body.test_cmd,
        "max_tasks": body.max_tasks,
        "default_complexity": body.default_complexity,
    }

    row = await db.create_user_template(
        user_id=user_id,
        name=body.name,
        config_json=json.dumps(config),
    )
    return _user_row_to_response(row)


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    body: UpdateUserTemplateRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> TemplateResponse:
    """Update a user-owned template (auth required, owner only).

    Cannot update built-in templates.
    """
    # Block updates to built-in templates
    if get_template(template_id) is not None:
        raise HTTPException(status_code=403, detail="Cannot update built-in templates")

    db = _get_db(request)
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    row = await db.get_user_template(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not the template owner")

    # Merge updates into existing config
    existing_config = json.loads(row.config_json) if row.config_json else {}
    updates = body.model_dump(exclude_none=True)

    new_name = updates.pop("name", None)
    if "review_config" in updates:
        updates["review_config"] = body.review_config.model_dump()
    existing_config.update(updates)

    updated_row = await db.update_user_template(
        template_id,
        name=new_name,
        config_json=json.dumps(existing_config),
    )
    if updated_row is None:
        raise HTTPException(status_code=404, detail="Template not found")

    return _user_row_to_response(updated_row)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> None:
    """Delete a user-owned template (auth required, owner only).

    Cannot delete built-in templates.
    """
    # Block deletion of built-in templates
    if get_template(template_id) is not None:
        raise HTTPException(status_code=403, detail="Cannot delete built-in templates")

    db = _get_db(request)
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    row = await db.get_user_template(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not the template owner")

    await db.delete_user_template(template_id)
