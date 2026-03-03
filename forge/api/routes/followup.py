"""Follow-up question REST endpoints: submit and check status."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from forge.api.security.jwt import decode_token
from forge.core.events import EventEmitter
from forge.core.followup import (
    FollowUpExecution,
    FollowUpQuestion,
    FollowUpStatus,
    classify_questions,
    execute_followups,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["followup"])

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Extract and verify the JWT token. Returns user_id."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        payload = decode_token(credentials.credentials, secret=request.app.state.jwt_secret)
        return payload["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _get_forge_db(request: Request):
    """Get the forge Database instance from app.state."""
    return getattr(request.app.state, "forge_db", None)


def _get_followup_store(request: Request) -> dict[str, FollowUpExecution]:
    """Get or create the in-memory follow-up execution store."""
    if not hasattr(request.app.state, "followup_store"):
        request.app.state.followup_store = {}
    return request.app.state.followup_store


# ── Request/Response schemas ──────────────────────────────────────────


class FollowUpQuestionInput(BaseModel):
    """A single follow-up question in the request body."""
    text: str = Field(min_length=1, description="The follow-up question or request")
    context: str | None = Field(default=None, description="Optional context for the question")


class FollowUpRequest(BaseModel):
    """Request body for submitting follow-up questions."""
    questions: list[FollowUpQuestionInput] = Field(
        min_length=1,
        description="List of follow-up questions",
    )


class FollowUpResultResponse(BaseModel):
    """Result for a single task's follow-up execution."""
    task_id: str
    task_title: str
    success: bool
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    error: str | None = None
    cost_usd: float = 0.0


class FollowUpResponse(BaseModel):
    """Response for follow-up submission."""
    followup_id: str
    pipeline_id: str
    status: str
    message: str


class FollowUpStatusResponse(BaseModel):
    """Response for follow-up status query."""
    followup_id: str
    pipeline_id: str
    status: str
    questions: list[dict] = Field(default_factory=list)
    classification: dict[str, str] = Field(default_factory=dict)
    results: list[FollowUpResultResponse] = Field(default_factory=list)
    error: str | None = None
    created_at: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/{pipeline_id}/followup", response_model=FollowUpResponse, status_code=202)
async def submit_followup(
    pipeline_id: str,
    body: FollowUpRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> FollowUpResponse:
    """Submit follow-up questions for a completed pipeline.

    Classifies each question to the most relevant original task,
    then spawns agents to address them. Returns immediately with a
    followup_id that can be polled for status.
    """
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    # Verify pipeline exists and belongs to user
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Verify pipeline is complete
    if pipeline.status != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline is in '{pipeline.status}' state. Follow-ups require a completed pipeline.",
        )

    # Parse the task graph
    tasks_json = json.loads(pipeline.task_graph_json) if pipeline.task_graph_json else {"tasks": []}
    pipeline_tasks = tasks_json.get("tasks", [])
    if not pipeline_tasks:
        raise HTTPException(status_code=400, detail="Pipeline has no tasks to follow up on")

    # Convert request questions to domain objects
    questions = [
        FollowUpQuestion(text=q.text, context=q.context)
        for q in body.questions
    ]

    # Create the follow-up execution record
    followup_id = str(uuid.uuid4())
    followup = FollowUpExecution(
        id=followup_id,
        pipeline_id=pipeline_id,
        status=FollowUpStatus.PENDING,
        questions=questions,
    )

    # Store it
    store = _get_followup_store(request)
    store[followup_id] = followup

    # Get DB tasks for context gathering
    pipeline_db_tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)

    # Set up event emitter and bridge to WebSocket if available
    emitter = EventEmitter()
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager:
        _bridge_followup_events(emitter, ws_manager, pipeline_id)

    # Launch classification and execution in background
    async def _run_followup():
        try:
            # Phase 1: Classify questions
            followup.status = FollowUpStatus.CLASSIFYING

            if ws_manager:
                await ws_manager.broadcast(pipeline_id, {
                    "type": "followup:classifying",
                    "followup_id": followup_id,
                    "question_count": len(questions),
                })

            classification = await classify_questions(questions, pipeline_tasks)
            followup.classification = classification

            if ws_manager:
                await ws_manager.broadcast(pipeline_id, {
                    "type": "followup:classified",
                    "followup_id": followup_id,
                    "classification": {str(k): v for k, v in classification.items()},
                })

            # Phase 2: Execute follow-ups
            await execute_followups(
                followup=followup,
                pipeline_tasks=pipeline_tasks,
                pipeline_db_tasks=pipeline_db_tasks,
                pipeline=pipeline,
                db=forge_db,
                emitter=emitter,
            )

            # Log completion event
            await forge_db.log_event(
                pipeline_id=pipeline_id,
                task_id=None,
                event_type="followup:complete",
                payload={
                    "followup_id": followup_id,
                    "status": followup.status.value,
                    "result_count": len(followup.results),
                    "success_count": sum(1 for r in followup.results if r.success),
                },
            )

            if ws_manager:
                await ws_manager.broadcast(pipeline_id, {
                    "type": "followup:complete",
                    "followup_id": followup_id,
                    "status": followup.status.value,
                    "results": [
                        {
                            "task_id": r.task_id,
                            "task_title": r.task_title,
                            "success": r.success,
                            "summary": r.summary,
                            "files_changed": r.files_changed,
                        }
                        for r in followup.results
                    ],
                })

        except Exception as exc:
            logger.exception("Follow-up execution failed for pipeline %s", pipeline_id)
            followup.status = FollowUpStatus.ERROR
            followup.error = str(exc)

            if ws_manager:
                await ws_manager.broadcast(pipeline_id, {
                    "type": "followup:error",
                    "followup_id": followup_id,
                    "error": str(exc),
                })

    asyncio.create_task(_run_followup())

    return FollowUpResponse(
        followup_id=followup_id,
        pipeline_id=pipeline_id,
        status=followup.status.value,
        message=f"Processing {len(questions)} follow-up question(s)",
    )


@router.get(
    "/{pipeline_id}/followup/{followup_id}",
    response_model=FollowUpStatusResponse,
)
async def get_followup_status(
    pipeline_id: str,
    followup_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> FollowUpStatusResponse:
    """Get the status and results of a follow-up execution."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    # Verify pipeline belongs to user (IDOR protection)
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Look up follow-up execution
    store = _get_followup_store(request)
    followup = store.get(followup_id)
    if followup is None or followup.pipeline_id != pipeline_id:
        raise HTTPException(status_code=404, detail="Follow-up not found")

    return FollowUpStatusResponse(
        followup_id=followup.id,
        pipeline_id=followup.pipeline_id,
        status=followup.status.value,
        questions=[
            {"text": q.text, "context": q.context}
            for q in followup.questions
        ],
        classification={str(k): v for k, v in followup.classification.items()},
        results=[
            FollowUpResultResponse(
                task_id=r.task_id,
                task_title=r.task_title,
                success=r.success,
                summary=r.summary,
                files_changed=r.files_changed,
                error=r.error,
                cost_usd=r.cost_usd,
            )
            for r in followup.results
        ],
        error=followup.error,
        created_at=followup.created_at,
    )


def _bridge_followup_events(emitter: EventEmitter, ws_manager, pipeline_id: str) -> None:
    """Register event handlers to broadcast follow-up events over WebSocket."""
    event_types = [
        "followup:task_started",
        "followup:task_completed",
        "followup:task_error",
        "followup:agent_output",
    ]
    for event_type in event_types:

        async def _handler(data, _type=event_type):
            await ws_manager.broadcast(pipeline_id, {"type": _type, **(data or {})})

        emitter.on(event_type, _handler)
