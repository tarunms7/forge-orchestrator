"""Task REST endpoints: create, get status, list, execute."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import uuid

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

logger = logging.getLogger(__name__)

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
                    graph = await daemon.plan(
                        body.description, forge_db,
                        emit_plan_ready=False, pipeline_id=pipeline_id,
                    )

                    # Remap task IDs to be globally unique, then emit
                    # plan_ready with prefixed IDs as the single source
                    # of truth (we suppressed the event in plan()).
                    prefix = pipeline_id[:8]
                    id_map = {t.id: f"{prefix}-{t.id}" for t in graph.tasks}
                    for t in graph.tasks:
                        t.depends_on = [id_map.get(d, d) for d in t.depends_on]
                        t.id = id_map[t.id]

                    # Re-emit plan_ready with remapped IDs for the frontend
                    await ws_manager.broadcast(pipeline_id, {
                        "type": "pipeline:plan_ready",
                        "tasks": [
                            {
                                "id": t.id, "title": t.title,
                                "description": t.description,
                                "files": t.files, "depends_on": t.depends_on,
                                "complexity": t.complexity.value,
                            }
                            for t in graph.tasks
                        ],
                    })

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
                    logger.exception("Planning failed for pipeline %s", pipeline_id)
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
            await daemon.execute(graph, forge_db, pipeline_id=pipeline_id)
            await forge_db.update_pipeline_status(pipeline_id, "complete")

            # Auto-create PR after successful completion
            await ws_manager.broadcast(pipeline_id, {
                "type": "pipeline:pr_creating",
            })
            try:
                pr_url = await _auto_create_pr(forge_db, pipeline_id)
                await ws_manager.broadcast(pipeline_id, {
                    "type": "pipeline:pr_created", "pr_url": pr_url,
                })
            except Exception as pr_exc:
                logger.warning("Auto-PR failed for %s: %s", pipeline_id, pr_exc)
                await ws_manager.broadcast(pipeline_id, {
                    "type": "pipeline:pr_failed", "error": str(pr_exc),
                })
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
        # Detect remote name — fail early if no remote configured
        remote_result = subprocess.run(
            ["git", "remote"],
            cwd=project_dir, capture_output=True, text=True,
        )
        remotes = remote_result.stdout.strip()
        if not remotes:
            raise HTTPException(
                status_code=400,
                detail="No git remote configured. Add a remote first: git remote add origin <repo-url>",
            )
        remote_name = remotes.split("\n")[0]

        # Detect the current/main branch so we can set --base correctly
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_dir, capture_output=True, text=True,
        ).stdout.strip() or "main"

        # Create or reset branch from current state (use -B to handle existing branch)
        subprocess.run(
            ["git", "checkout", "-B", branch_name],
            cwd=project_dir, check=True, capture_output=True, text=True,
        )

        # Push to remote (force push to handle branch reset)
        push_result = subprocess.run(
            ["git", "push", "-u", "--force-with-lease", remote_name, branch_name],
            cwd=project_dir, capture_output=True, text=True,
        )
        if push_result.returncode != 0:
            subprocess.run(["git", "checkout", current_branch], cwd=project_dir, capture_output=True)
            error_msg = push_result.stderr.strip() or push_result.stdout.strip() or "Unknown push error"
            raise HTTPException(
                status_code=500,
                detail=f"git push failed: {error_msg}. Check that remote '{remote_name}' is accessible.",
            )

        # Check if a PR already exists for this branch
        existing_pr = subprocess.run(
            ["gh", "pr", "view", branch_name, "--json", "url", "-q", ".url"],
            cwd=project_dir, capture_output=True, text=True,
        )
        if existing_pr.returncode == 0 and existing_pr.stdout.strip():
            pr_url = existing_pr.stdout.strip()
            await forge_db.set_pipeline_pr_url(pipeline_id, pr_url)
            # Switch back to the main branch
            subprocess.run(
                ["git", "checkout", current_branch],
                cwd=project_dir, capture_output=True,
            )
            return {"pr_url": pr_url, "already_existed": True}

        # Build a PR body with task summary
        tasks_json = json.loads(pipeline.task_graph_json) if pipeline.task_graph_json else {"tasks": []}
        task_list = tasks_json.get("tasks", [])
        task_summary = "\n".join(f"- {t.get('title', t.get('id', ''))}" for t in task_list)

        pr_body = (
            f"## Summary\n\n"
            f"{pipeline.description}\n\n"
            f"## Tasks Completed\n\n"
            f"{task_summary}\n\n"
            f"---\n"
            f"*Automated PR created by [Forge](https://github.com/tarunms7/forge-orchestrator) "
            f"pipeline `{pipeline_id[:8]}`*"
        )

        # Create PR using gh CLI
        pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--base", current_branch,
             "--head", branch_name,
             "--title", f"forge: {pipeline.description[:60]}",
             "--body", pr_body],
            cwd=project_dir, capture_output=True, text=True,
        )

        if pr_result.returncode != 0:
            # Switch back to the main branch before raising
            subprocess.run(
                ["git", "checkout", current_branch],
                cwd=project_dir, capture_output=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create PR: {pr_result.stderr or pr_result.stdout}",
            )

        pr_url = pr_result.stdout.strip()

        # Store PR URL on pipeline
        await forge_db.set_pipeline_pr_url(pipeline_id, pr_url)

        # Switch back to the main branch
        subprocess.run(
            ["git", "checkout", current_branch],
            cwd=project_dir, capture_output=True,
        )

        return {"pr_url": pr_url}

    except subprocess.CalledProcessError as e:
        # Try to switch back to main branch on error
        subprocess.run(
            ["git", "checkout", "-"],
            cwd=project_dir, capture_output=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Git operation failed: {e.stderr or str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        subprocess.run(
            ["git", "checkout", "-"],
            cwd=project_dir, capture_output=True,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """Resume an interrupted pipeline. Resets stuck tasks and re-enters execution loop."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(500, "Database not configured")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(404, "Pipeline not found")

    if pipeline.status == "complete":
        raise HTTPException(400, "Pipeline already complete")

    # Reconstruct TaskGraph from stored JSON
    graph_json = json.loads(pipeline.task_graph_json) if pipeline.task_graph_json else None
    if not graph_json:
        raise HTTPException(400, "No task graph stored — cannot resume")

    from forge.core.models import TaskGraph, TaskDefinition, Complexity
    task_defs = []
    for t in graph_json.get("tasks", []):
        task_defs.append(TaskDefinition(
            id=t["id"], title=t["title"], description=t["description"],
            files=t["files"], depends_on=t.get("depends_on", []),
            complexity=Complexity(t.get("complexity", "medium")),
        ))
    graph = TaskGraph(tasks=task_defs)

    # Check if task rows exist in DB (they may not if preflight failed before creation)
    tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
    needs_fresh_start = len(tasks) == 0

    if not needs_fresh_start:
        # Reset interrupted or cancelled tasks back to todo
        reset_count = 0
        for task in tasks:
            if task.state in ("in_progress", "in_review", "merging", "cancelled"):
                await forge_db.update_task_state(task.id, "todo")
                reset_count += 1

        if reset_count == 0:
            pending = [t for t in tasks if t.state == "todo"]
            if not pending:
                raise HTTPException(400, "No tasks to resume (all done or errored)")
    else:
        reset_count = 0

    # Set pipeline back to executing
    await forge_db.update_pipeline_status(pipeline_id, "executing")

    # Launch execution in background
    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon
    from forge.core.events import EventEmitter

    settings = ForgeSettings()
    emitter = getattr(request.app.state, "event_emitter", None)
    if emitter is None:
        emitter = EventEmitter()

    # Bridge events to WebSocket if ws_manager available
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager:
        _bridge_events(emitter, ws_manager, pipeline_id)

    daemon = ForgeDaemon(pipeline.project_dir, settings=settings, event_emitter=emitter)

    # If tasks were never created (preflight failed before creation),
    # run a fresh execution (resume=False) so they get created in DB.
    use_resume = not needs_fresh_start

    async def _run():
        try:
            await daemon.execute(graph, forge_db, pipeline_id=pipeline_id, resume=use_resume)
            await forge_db.update_pipeline_status(pipeline_id, "complete")
        except Exception as e:
            logger.error("Resume execution failed: %s", e)
            await forge_db.update_pipeline_status(pipeline_id, "error")

    asyncio.create_task(_run())

    return {"status": "resumed", "pipeline_id": pipeline_id, "tasks_reset": reset_count, "fresh_start": needs_fresh_start}


@router.post("/{pipeline_id}/cancel")
async def cancel_pipeline(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """Cancel a running pipeline. Sets all non-terminal tasks to cancelled."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(500, "Database not configured")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(404, "Pipeline not found")

    tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
    cancelled_count = 0
    for task in tasks:
        if task.state not in ("done", "error", "cancelled"):
            await forge_db.update_task_state(task.id, "cancelled")
            cancelled_count += 1

    await forge_db.update_pipeline_status(pipeline_id, "cancelled")
    return {"status": "cancelled", "tasks_cancelled": cancelled_count}


@router.post("/{pipeline_id}/{task_id}/retry")
async def retry_task(
    pipeline_id: str,
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """Retry a single failed task."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(500, "Database not configured")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(404, "Pipeline not found")

    task = await forge_db.get_task(task_id)
    if task is None or task.pipeline_id != pipeline_id:
        raise HTTPException(404, "Task not found")

    if task.state != "error":
        raise HTTPException(400, f"Task is in state '{task.state}', can only retry errored tasks")

    await forge_db.retry_task(task_id)  # Resets to todo, increments retry_count

    # If pipeline was complete/cancelled, reactivate it
    if pipeline.status in ("complete", "cancelled", "error"):
        await forge_db.update_pipeline_status(pipeline_id, "executing")

    return {"status": "retrying", "task_id": task_id}


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
        tasks_list = tasks.get("tasks", [])

        # Query persisted events for this pipeline
        events = await forge_db.list_events(pipeline_id)

        # Build per-task event data
        task_events: dict[str, dict] = {}
        timeline = []
        for ev in events:
            timeline.append({
                "type": ev.event_type,
                "task_id": ev.task_id,
                "payload": ev.payload,
                "timestamp": ev.created_at,
            })
            if ev.task_id:
                te = task_events.setdefault(ev.task_id, {
                    "output": [], "reviewGates": [], "mergeResult": None, "cost_usd": 0,
                })
                if ev.event_type == "task:agent_output":
                    te["output"].append(ev.payload.get("line", ""))
                elif ev.event_type == "task:review_update":
                    te["reviewGates"].append({
                        "gate": ev.payload.get("gate"),
                        "result": "pass" if ev.payload.get("passed") else "fail",
                        "details": ev.payload.get("details"),
                    })
                elif ev.event_type == "task:merge_result":
                    te["mergeResult"] = ev.payload
                elif ev.event_type == "task:cost_update":
                    te["cost_usd"] = ev.payload.get("cumulative_cost_usd", 0)
                elif ev.event_type == "task:state_changed":
                    te["state"] = ev.payload.get("state")

        # Also get live task states from DB
        db_tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
        task_state_map = {t.id: t.state for t in db_tasks}

        # Merge event data into task list
        enriched_tasks = []
        for t in tasks_list:
            tid = t.get("id", "")
            te = task_events.get(tid, {})
            enriched = {**t, **te}
            # Use live DB state if available (more accurate than last event)
            if tid in task_state_map:
                enriched["state"] = task_state_map[tid]
            enriched_tasks.append(enriched)

        return TaskStatusResponse(
            pipeline_id=pipeline.id,
            phase=pipeline.status,
            tasks=enriched_tasks,
            timeline=timeline,
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


async def _auto_create_pr(forge_db, pipeline_id: str) -> str:
    """Create a GitHub PR for a completed pipeline. Returns the PR URL."""
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if not pipeline:
        raise ValueError("Pipeline not found")

    # Check if PR already exists
    if getattr(pipeline, "pr_url", None):
        return pipeline.pr_url

    project_dir = pipeline.project_dir
    branch_name = f"forge/pipeline-{pipeline_id[:8]}"

    # Detect remote name — fail early if no remote configured
    remote_result = subprocess.run(
        ["git", "remote"],
        cwd=project_dir, capture_output=True, text=True,
    )
    remotes = remote_result.stdout.strip()
    if not remotes:
        raise RuntimeError(
            "No git remote configured. Add a remote first: "
            "git remote add origin <repo-url>"
        )
    remote_name = remotes.split("\n")[0]

    # Check if gh CLI is available and authenticated
    gh_check = subprocess.run(
        ["gh", "auth", "status"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if gh_check.returncode != 0:
        raise RuntimeError(
            "GitHub CLI not authenticated. Run: gh auth login"
        )

    # Detect the current/main branch
    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_dir, capture_output=True, text=True,
    ).stdout.strip() or "main"

    # Create or reset branch from current state
    subprocess.run(
        ["git", "checkout", "-B", branch_name],
        cwd=project_dir, check=True, capture_output=True, text=True,
    )

    # Push to remote
    push_result = subprocess.run(
        ["git", "push", "-u", "--force-with-lease", remote_name, branch_name],
        cwd=project_dir, capture_output=True, text=True,
    )
    if push_result.returncode != 0:
        # Switch back before raising
        subprocess.run(["git", "checkout", current_branch], cwd=project_dir, capture_output=True)
        error_msg = push_result.stderr.strip() or push_result.stdout.strip() or "Unknown push error"
        raise RuntimeError(
            f"git push failed: {error_msg}. "
            f"Check that remote '{remote_name}' is accessible and you have push permissions."
        )

    # Check if a PR already exists for this branch
    existing_pr = subprocess.run(
        ["gh", "pr", "view", branch_name, "--json", "url", "-q", ".url"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if existing_pr.returncode == 0 and existing_pr.stdout.strip():
        pr_url = existing_pr.stdout.strip()
        await forge_db.set_pipeline_pr_url(pipeline_id, pr_url)
        subprocess.run(["git", "checkout", current_branch], cwd=project_dir, capture_output=True)
        return pr_url

    # Build PR body
    tasks_json = json.loads(pipeline.task_graph_json) if pipeline.task_graph_json else {"tasks": []}
    task_list = tasks_json.get("tasks", [])
    task_summary = "\n".join(f"- {t.get('title', t.get('id', ''))}" for t in task_list)

    pr_body = (
        f"## Summary\n\n"
        f"{pipeline.description}\n\n"
        f"## Tasks Completed\n\n"
        f"{task_summary}\n\n"
        f"---\n"
        f"*Automated PR created by [Forge](https://github.com/tarunms7/forge-orchestrator) "
        f"pipeline `{pipeline_id[:8]}`*"
    )

    pr_result = subprocess.run(
        ["gh", "pr", "create",
         "--base", current_branch,
         "--head", branch_name,
         "--title", f"forge: {pipeline.description[:60]}",
         "--body", pr_body],
        cwd=project_dir, capture_output=True, text=True,
    )

    # Switch back to main branch
    subprocess.run(["git", "checkout", current_branch], cwd=project_dir, capture_output=True)

    if pr_result.returncode != 0:
        raise RuntimeError(f"Failed to create PR: {pr_result.stderr or pr_result.stdout}")

    pr_url = pr_result.stdout.strip()
    await forge_db.set_pipeline_pr_url(pipeline_id, pr_url)
    return pr_url


def _bridge_events(emitter, ws_manager, pipeline_id: str) -> None:
    """Register event handlers to broadcast daemon events over WebSocket."""
    event_types = [
        "pipeline:phase_changed",
        "pipeline:plan_ready",
        "pipeline:preflight_failed",
        "task:state_changed",
        "task:agent_output",
        "task:files_changed",
        "task:review_update",
        "task:merge_result",
        "task:cost_update",
        "planner:output",
    ]
    for event_type in event_types:

        async def _handler(data, _type=event_type):
            await ws_manager.broadcast(pipeline_id, {"type": _type, **(data or {})})

        emitter.on(event_type, _handler)
