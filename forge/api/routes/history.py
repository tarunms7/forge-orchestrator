"""History endpoints: list and detail for past pipeline runs."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.routes.tasks import get_current_user

router = APIRouter(prefix="/history", tags=["history"])


def _get_forge_db(request: Request):
    return getattr(request.app.state, "forge_db", None)


@router.get("")
async def list_history(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    """Return list of past pipeline runs for the authenticated user."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        return []

    pipelines = await forge_db.list_pipelines(user_id=user_id)
    results = []
    for p in pipelines:
        duration = None
        if p.created_at and p.completed_at:
            try:
                start = datetime.fromisoformat(p.created_at)
                end = datetime.fromisoformat(p.completed_at)
                duration = int((end - start).total_seconds())
            except (ValueError, TypeError):
                pass

        # Count tasks belonging to this pipeline
        tasks = await forge_db.list_tasks_by_pipeline(p.id)

        results.append({
            "pipeline_id": p.id,
            "description": p.description,
            "phase": p.status,
            "created_at": p.created_at or "",
            "duration": duration,
            "task_count": len(tasks),
        })
    return results


@router.get("/{pipeline_id}")
async def get_history_detail(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return full detail for a past pipeline run."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Parse tasks from the task_graph_json
    tasks_data = []
    if pipeline.task_graph_json:
        try:
            tasks_data = json.loads(pipeline.task_graph_json).get("tasks", [])
        except (json.JSONDecodeError, AttributeError):
            pass

    duration = None
    if pipeline.created_at and pipeline.completed_at:
        try:
            start = datetime.fromisoformat(pipeline.created_at)
            end = datetime.fromisoformat(pipeline.completed_at)
            duration = int((end - start).total_seconds())
        except (ValueError, TypeError):
            pass

    return {
        "pipeline_id": pipeline.id,
        "description": pipeline.description,
        "project_path": pipeline.project_dir,
        "phase": pipeline.status,
        "tasks": tasks_data,
        "created_at": pipeline.created_at or "",
        "duration": duration,
        "pr_url": getattr(pipeline, "pr_url", None),
    }
