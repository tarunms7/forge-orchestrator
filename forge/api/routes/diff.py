"""Diff endpoint: returns combined diff text for a pipeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.routes.tasks import get_current_user

router = APIRouter(prefix="/tasks", tags=["diff"])


@router.get("/{pipeline_id}/diff")
async def get_pipeline_diff(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return combined diff text for a pipeline.

    Returns placeholder diff content until real pipeline integration is wired.
    """
    if not hasattr(request.app.state, "pipelines"):
        request.app.state.pipelines = {}

    pipelines = request.app.state.pipelines
    pipeline = pipelines.get(pipeline_id)

    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Placeholder diff until real pipeline integration
    diff_text = pipeline.get("diff", "")
    return {"pipeline_id": pipeline_id, "diff": diff_text}
