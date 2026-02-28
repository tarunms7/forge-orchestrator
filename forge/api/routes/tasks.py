"""Task REST endpoints: create, get status, list, execute."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge.api.models.schemas import (
    CreateTaskRequest,
    ExecuteRequest,
    PipelineResponse,
    TaskListItem,
    TaskStatusResponse,
)
from forge.api.security.jwt import decode_token

router = APIRouter(tags=["tasks"])

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


def _get_forge_db(request: Request):
    """Get the forge Database instance from app.state."""
    return getattr(request.app.state, "forge_db", None)


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> PipelineResponse:
    """Create a new pipeline task.

    Creates the pipeline in DB and optionally starts planning in background.
    """
    pipeline_id = str(uuid.uuid4())
    forge_db = _get_forge_db(request)

    if forge_db is not None:
        await forge_db.create_pipeline(
            id=pipeline_id,
            description=body.description,
            project_dir=body.project_path,
            model_strategy=body.model_strategy,
            user_id=user_id,
        )

        # Start planning in background if daemon factory is available
        daemon_factory = getattr(request.app.state, "daemon_factory", None)
        if daemon_factory:
            daemon, emitter = daemon_factory(body.project_path, body.model_strategy)
            ws_manager = request.app.state.ws_manager
            _bridge_events(emitter, ws_manager, pipeline_id)

            async def _run_plan():
                try:
                    graph = await daemon.plan(body.description, forge_db)
                    await forge_db.set_pipeline_plan(
                        pipeline_id,
                        json.dumps({
                            "tasks": [
                                {
                                    "id": t.id, "title": t.title,
                                    "description": t.description,
                                    "files": t.files, "depends_on": t.depends_on,
                                    "complexity": t.complexity.value,
                                }
                                for t in graph.tasks
                            ]
                        }),
                    )
                    # Store graph for later execution
                    if not hasattr(request.app.state, "pending_graphs"):
                        request.app.state.pending_graphs = {}
                    request.app.state.pending_graphs[pipeline_id] = (graph, daemon)
                except Exception as exc:
                    await forge_db.update_pipeline_status(pipeline_id, "error")
                    await ws_manager.broadcast(pipeline_id, {
                        "type": "pipeline:error", "error": str(exc),
                    })

            asyncio.create_task(_run_plan())
    else:
        # Fallback: in-memory storage for testing without forge DB
        if not hasattr(request.app.state, "pipelines"):
            request.app.state.pipelines = {}
        request.app.state.pipelines[pipeline_id] = {
            "pipeline_id": pipeline_id,
            "user_id": user_id,
            "description": body.description,
            "project_path": body.project_path,
            "extra_dirs": body.extra_dirs,
            "model_strategy": body.model_strategy,
            "phase": "pending",
            "tasks": [],
        }

    return PipelineResponse(pipeline_id=pipeline_id)


@router.get("/stats")
async def get_stats(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return dashboard statistics for the authenticated user."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        return {"total_runs": 0, "active": 0, "completed": 0, "failed": 0}

    pipelines = await forge_db.list_pipelines(user_id=user_id)
    total = len(pipelines)
    active = sum(1 for p in pipelines if p.status in ("planning", "planned", "executing"))
    completed = sum(1 for p in pipelines if p.status == "complete")
    failed = sum(1 for p in pipelines if p.status == "error")

    return {
        "total_runs": total,
        "active": active,
        "completed": completed,
        "failed": failed,
    }


@router.post("/{pipeline_id}/execute", status_code=202)
async def execute_pipeline(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Start execution of a previously planned pipeline."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    # IDOR check: verify the pipeline belongs to the requesting user
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pending_graphs = getattr(request.app.state, "pending_graphs", {})
    entry = pending_graphs.get(pipeline_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No pending plan found for this pipeline")

    graph, daemon = entry
    ws_manager = request.app.state.ws_manager

    async def _run_execute():
        try:
            await forge_db.update_pipeline_status(pipeline_id, "executing")
            await daemon.execute(graph, forge_db)
            await forge_db.update_pipeline_status(pipeline_id, "complete")
        except Exception as exc:
            logger.exception("Pipeline %s execution failed", pipeline_id)
            await forge_db.update_pipeline_status(pipeline_id, "error")
            await ws_manager.broadcast(pipeline_id, {
                "type": "pipeline:error", "error": str(exc),
            })

    asyncio.create_task(_run_execute())
    del pending_graphs[pipeline_id]

    return {"status": "executing", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/pr")
async def create_pr(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Create a GitHub PR for a completed pipeline."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Check if PR already exists
    if getattr(pipeline, "pr_url", None):
        return {"pr_url": pipeline.pr_url, "already_existed": True}

    project_dir = pipeline.project_dir
    branch_name = f"forge/pipeline-{pipeline_id[:8]}"

    try:
        # Create branch from current state
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=project_dir, check=True, capture_output=True, text=True,
        )

        # Push to remote
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=project_dir, check=True, capture_output=True, text=True,
        )

        # Create PR using gh CLI
        pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--base", "main",
             "--head", branch_name,
             "--title", f"Forge: {pipeline.description[:60]}",
             "--body", f"Automated PR created by Forge pipeline `{pipeline_id}`.\n\nDescription: {pipeline.description}"],
            cwd=project_dir, capture_output=True, text=True,
        )

        if pr_result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create PR: {pr_result.stderr or pr_result.stdout}",
            )

        pr_url = pr_result.stdout.strip()

        # Store PR URL on pipeline
        await forge_db.set_pipeline_pr_url(pipeline_id, pr_url)

        return {"pr_url": pr_url}

    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Git operation failed: {e.stderr or str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{pipeline_id}", response_model=TaskStatusResponse)
async def get_task_status(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> TaskStatusResponse:
    """Get the status of a pipeline by ID."""
    forge_db = _get_forge_db(request)

    if forge_db is not None:
        pipeline = await forge_db.get_pipeline(pipeline_id)
        if pipeline is None or pipeline.user_id != user_id:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        tasks = json.loads(pipeline.task_graph_json) if pipeline.task_graph_json else {"tasks": []}
        return TaskStatusResponse(
            pipeline_id=pipeline.id,
            phase=pipeline.status,
            tasks=tasks.get("tasks", []),
        )

    # Fallback: in-memory
    pipelines = getattr(request.app.state, "pipelines", {})
    pipeline = pipelines.get(pipeline_id)
    if pipeline is None or pipeline["user_id"] != user_id:
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
    forge_db = _get_forge_db(request)

    if forge_db is not None:
        pipelines = await forge_db.list_pipelines(user_id=user_id)
        return [
            TaskListItem(
                pipeline_id=p.id,
                description=p.description,
                project_path=p.project_dir,
                phase=p.status,
            )
            for p in pipelines
        ]

    # Fallback: in-memory
    pipelines = getattr(request.app.state, "pipelines", {})
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


def _bridge_events(emitter, ws_manager, pipeline_id: str) -> None:
    """Register event handlers to broadcast daemon events over WebSocket."""
    event_types = [
        "pipeline:phase_changed",
        "pipeline:plan_ready",
        "task:state_changed",
        "task:agent_output",
        "task:review_update",
        "task:merge_result",
        "planner:output",
    ]
    for event_type in event_types:

        async def _handler(data, _type=event_type):
            await ws_manager.broadcast(pipeline_id, {"type": _type, **(data or {})})

        emitter.on(event_type, _handler)
