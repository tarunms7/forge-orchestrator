"""History endpoints: list and detail for past pipeline runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.routes.tasks import get_current_user

router = APIRouter(prefix="/history", tags=["history"])


def _get_pipelines(request: Request) -> dict:
    """Get or initialise the in-memory pipeline store on app.state."""
    if not hasattr(request.app.state, "pipelines"):
        request.app.state.pipelines = {}
    return request.app.state.pipelines


@router.get("")
async def list_history(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    """Return list of past pipeline runs for the authenticated user."""
    pipelines = _get_pipelines(request)

    return [
        {
            "pipeline_id": p["pipeline_id"],
            "description": p["description"],
            "phase": p["phase"],
            "created_at": p.get("created_at", ""),
            "duration": p.get("duration"),
            "task_count": len(p.get("tasks", [])),
        }
        for p in pipelines.values()
        if p["user_id"] == user_id
    ]


@router.get("/{pipeline_id}")
async def get_history_detail(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return full detail for a past pipeline run."""
    pipelines = _get_pipelines(request)
    pipeline = pipelines.get(pipeline_id)

    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    if pipeline["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    return {
        "pipeline_id": pipeline["pipeline_id"],
        "description": pipeline["description"],
        "project_path": pipeline["project_path"],
        "phase": pipeline["phase"],
        "tasks": pipeline.get("tasks", []),
        "created_at": pipeline.get("created_at", ""),
        "duration": pipeline.get("duration"),
    }
