"""Task REST endpoints: create, get status, list."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge.api.models.schemas import (
    CreateTaskRequest,
    PipelineResponse,
    TaskListItem,
    TaskStatusResponse,
)
from forge.api.security.jwt import decode_token

router = APIRouter(prefix="/tasks", tags=["tasks"])

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Extract and verify the JWT token from the Authorization header.

    Returns the ``user_id`` (``sub`` claim) from the decoded token.

    Raises:
        HTTPException: 401 if the token is missing or invalid.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    try:
        payload = decode_token(credentials.credentials, secret=request.app.state.jwt_secret)
        user_id: str = payload["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user_id


def _get_pipelines(request: Request) -> dict:
    """Get or initialise the in-memory pipeline store on app.state."""
    if not hasattr(request.app.state, "pipelines"):
        request.app.state.pipelines = {}
    return request.app.state.pipelines


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> PipelineResponse:
    """Create a new pipeline task.

    Stores pipeline metadata in-memory (will be wired to DB later).
    """
    pipeline_id = str(uuid.uuid4())
    pipelines = _get_pipelines(request)

    pipelines[pipeline_id] = {
        "pipeline_id": pipeline_id,
        "user_id": user_id,
        "description": body.description,
        "project_path": body.project_path,
        "extra_dirs": body.extra_dirs,
        "phase": "pending",
        "tasks": [],
    }

    return PipelineResponse(pipeline_id=pipeline_id)


@router.get("/{pipeline_id}", response_model=TaskStatusResponse)
async def get_task_status(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> TaskStatusResponse:
    """Get the status of a pipeline by ID."""
    pipelines = _get_pipelines(request)

    pipeline = pipelines.get(pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    return TaskStatusResponse(
        pipeline_id=pipeline["pipeline_id"],
        phase=pipeline["phase"],
        tasks=pipeline["tasks"],
    )


@router.get("", response_model=list[TaskListItem])
async def list_tasks(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[TaskListItem]:
    """List all pipelines belonging to the authenticated user."""
    pipelines = _get_pipelines(request)

    return [
        TaskListItem(
            pipeline_id=p["pipeline_id"],
            description=p["description"],
            project_path=p["project_path"],
            phase=p["phase"],
        )
        for p in pipelines.values()
        if p["user_id"] == user_id
    ]
