"""Diff endpoint: returns combined diff text for a pipeline."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.routes.tasks import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["diff"])


@router.get("/{pipeline_id}/diff")
async def get_pipeline_diff(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return combined diff text for a pipeline from DB events."""
    db = getattr(request.app.state, "db", None) or getattr(
        request.app.state, "forge_db", None
    )
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    pipeline = await db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    events = await db.list_events(pipeline_id, event_type="task:merge_result")
    diff_parts = []
    for evt in events:
        if evt.payload and evt.payload.get("success"):
            diff_text = evt.payload.get("diff", "")
            if diff_text:
                repo_id = evt.payload.get("repo_id", "default")
                diff_parts.append(f"# repo: {repo_id}\n{diff_text}")
    return {"pipeline_id": pipeline_id, "diff": "\n".join(diff_parts)}
