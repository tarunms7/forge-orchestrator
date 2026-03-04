"""Task REST endpoints: create, get status, list, execute."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge.api.models.schemas import (
    CreateTaskRequest,
    PipelineResponse,
    RestartPipelineRequest,
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


# ── PR title generation helpers ──────────────────────────────────────


def _sanitize_pr_title(description: str) -> str:
    """Generate a clean PR title from raw user description (heuristic fallback).

    Extracts the first meaningful sentence, strips list markers and numbering,
    lowercases, and truncates to fit within the ``forge: `` prefix budget.
    """
    # Take the first line / first sentence
    text = description.strip()
    # Split on common sentence boundaries and list starters
    first_sentence = re.split(r'[.!?\n]|(?:\d+[.)]\s)', text)[0].strip()
    # Strip leading list markers like "- ", "* ", "1. ", etc.
    first_sentence = re.sub(r'^[\-\*•]\s*', '', first_sentence).strip()
    # Remove question marks and trailing punctuation
    first_sentence = first_sentence.rstrip('?!.:;,')
    # Lowercase the first character for conventional commit style
    if first_sentence:
        first_sentence = first_sentence[0].lower() + first_sentence[1:]
    # Truncate to ~50 chars to keep total title (with "forge: " prefix) under ~60
    if len(first_sentence) > 50:
        # Cut at last word boundary
        truncated = first_sentence[:50].rsplit(' ', 1)[0]
        first_sentence = truncated
    return first_sentence or description[:50]


async def _generate_pr_title(description: str, task_summaries: str) -> str:
    """Generate a concise PR title using an LLM call, with heuristic fallback.

    Uses ``sdk_query()`` with haiku model for fast, cheap title generation.
    Falls back to ``_sanitize_pr_title()`` if the LLM call fails.

    Returns:
        A short title string (without the ``forge: `` prefix).
    """
    from claude_code_sdk import ClaudeCodeOptions
    from forge.core.sdk_helpers import sdk_query

    prompt = (
        "Generate a short, concise PR title for the following changes. "
        "The title should be in conventional commit style (e.g., 'fix: button alignment', "
        "'feat: add dark mode toggle', 'refactor: simplify auth flow'). "
        "Reply with ONLY the title text, nothing else. No quotes, no explanation. "
        "Keep it under 50 characters.\n\n"
        f"Pipeline description: {description}\n\n"
    )
    if task_summaries.strip():
        prompt += f"Tasks completed:\n{task_summaries}\n"

    try:
        result = await sdk_query(
            prompt=prompt,
            options=ClaudeCodeOptions(
                max_turns=1,
                model="haiku",
            ),
        )
        if result and result.result:
            title = result.result.strip().strip('"\'').strip()
            # Remove any "forge: " prefix the LLM might add (we add it ourselves)
            if title.lower().startswith("forge:"):
                title = title[6:].strip()
            # Validate: non-empty, reasonable length
            if title and len(title) <= 80:
                return title
    except Exception as e:
        logger.warning("LLM PR title generation failed, using fallback: %s", e)

    return _sanitize_pr_title(description)


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

    # Save uploaded images to disk so planner & agents can read them.
    description = body.description
    image_paths: list[str] = []
    if body.images:
        project_dir = body.project_path or os.getcwd()
        images_dir = os.path.join(project_dir, ".forge", "images", pipeline_id)
        os.makedirs(images_dir, exist_ok=True)
        for idx, data_uri in enumerate(body.images):
            # data_uri format: "data:image/png;base64,iVBOR..."
            try:
                header, b64data = data_uri.split(",", 1)
                ext = "png"
                if "image/jpeg" in header or "image/jpg" in header:
                    ext = "jpg"
                elif "image/gif" in header:
                    ext = "gif"
                elif "image/webp" in header:
                    ext = "webp"
                file_path = os.path.join(images_dir, f"image_{idx + 1}.{ext}")
                with open(file_path, "wb") as f:
                    f.write(base64.b64decode(b64data))
                image_paths.append(file_path)
            except Exception:
                logger.warning("Failed to decode image %d for pipeline %s", idx, pipeline_id)

        if image_paths:
            description += "\n\n## Attached Images\n"
            description += "The user has attached the following image files. Use the Read tool to view them:\n"
            for path in image_paths:
                description += f"- {path}\n"

    if forge_db is not None:
        await forge_db.create_pipeline(
            id=pipeline_id,
            description=description,
            project_dir=body.project_path,
            model_strategy=body.model_strategy,
            user_id=user_id,
            branch_name=body.branch_name,
            build_cmd=body.build_cmd,
            test_cmd=body.test_cmd,
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
                        description, forge_db,
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
                    # Update DB status so REST hydration returns correct phase
                    await forge_db.update_pipeline_status(pipeline_id, "planned")
                    # Store graph for later execution (lock protects concurrent access)
                    lock = getattr(request.app.state, "pending_graphs_lock", None)
                    if lock:
                        async with lock:
                            request.app.state.pending_graphs[pipeline_id] = (graph, daemon)
                    else:
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
            "description": description,
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
        return {"total_runs": 0, "active": 0, "completed": 0, "failed": 0, "avg_duration_secs": None, "total_spend_usd": None}

    pipelines = await forge_db.list_pipelines(user_id=user_id)
    total = len(pipelines)
    active = sum(1 for p in pipelines if p.status in ("planning", "planned", "executing"))
    completed = sum(1 for p in pipelines if p.status == "complete")
    failed = sum(1 for p in pipelines if p.status == "error")

    # Compute average duration (seconds) across completed pipelines with timestamps.
    durations: list[float] = []
    for p in pipelines:
        if p.status == "complete" and p.created_at and p.completed_at:
            try:
                start = datetime.fromisoformat(p.created_at)
                end = datetime.fromisoformat(p.completed_at)
                durations.append((end - start).total_seconds())
            except (ValueError, TypeError):
                pass
    avg_duration_secs: float | None = round(sum(durations) / len(durations), 1) if durations else None

    # Compute total spend by aggregating task:cost_update events across all pipelines.
    total_spend: float = 0.0
    has_cost_data = False
    for p in pipelines:
        events = await forge_db.list_events(p.id, event_type="task:cost_update")
        for ev in events:
            cost = ev.payload.get("cost_usd", 0) if ev.payload else 0
            if cost:
                has_cost_data = True
                total_spend += cost
    total_spend_usd: float | None = round(total_spend, 4) if has_cost_data else None

    return {
        "total_runs": total,
        "active": active,
        "completed": completed,
        "failed": failed,
        "avg_duration_secs": avg_duration_secs,
        "total_spend_usd": total_spend_usd,
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

    lock = getattr(request.app.state, "pending_graphs_lock", None)
    pending_graphs = getattr(request.app.state, "pending_graphs", {})

    if lock:
        async with lock:
            entry = pending_graphs.get(pipeline_id)
    else:
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

            # Only auto-create PR if ALL tasks succeeded (no errors)
            tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
            errored = [t for t in tasks if t.state == "error"]
            if errored:
                logger.info(
                    "Skipping auto-PR for %s: %d task(s) in error state",
                    pipeline_id, len(errored),
                )
                await ws_manager.broadcast(pipeline_id, {
                    "type": "pipeline:pr_failed",
                    "error": f"{len(errored)} task(s) failed — fix before creating PR",
                })
            else:
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

    # Remove from pending_graphs under lock (use app.state directly, not local var)
    if lock:
        async with lock:
            request.app.state.pending_graphs.pop(pipeline_id, None)
    else:
        request.app.state.pending_graphs.pop(pipeline_id, None)

    return {"status": "executing", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/pr")
async def create_pr(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Create a GitHub PR for a completed pipeline.

    The pipeline branch (forge/pipeline-{id}) already contains all merged
    task code (advanced by update-ref during execution).  We just push it
    and open a PR — NO git checkout, NO working-directory mutation.
    """
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
    # Use the actual branch name stored by the daemon (supports custom names from UI)
    branch_name = getattr(pipeline, "branch_name", None)
    if not branch_name:
        branch_name = f"forge/pipeline-{pipeline_id[:8]}"
        logger.warning("Pipeline %s missing branch_name in DB, using fallback: %s", pipeline_id, branch_name)

    # Use base_branch from DB (stored by daemon at pipeline start)
    base_branch = getattr(pipeline, "base_branch", None) or "main"

    try:
        # Verify gh CLI is authenticated
        gh_check = subprocess.run(
            ["gh", "auth", "status"],
            cwd=project_dir, capture_output=True, text=True,
        )
        if gh_check.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail="GitHub CLI not authenticated. Run: gh auth login",
            )

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

        # Push the pipeline branch directly — NO checkout needed.
        # The branch was created by the daemon and advanced via update-ref.
        push_result = subprocess.run(
            ["git", "push", "-u", "--force-with-lease", remote_name, branch_name],
            cwd=project_dir, capture_output=True, text=True,
        )
        if push_result.returncode != 0:
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

        # Generate a proper PR title via LLM (with heuristic fallback)
        pr_title_body = await _generate_pr_title(pipeline.description, task_summary)
        pr_title = f"forge: {pr_title_body}"

        # Create PR — base_branch from DB, head is the pipeline branch
        pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--base", base_branch,
             "--head", branch_name,
             "--title", pr_title,
             "--body", pr_body],
            cwd=project_dir, capture_output=True, text=True,
        )

        if pr_result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create PR: {pr_result.stderr or pr_result.stdout}",
            )

        pr_url = pr_result.stdout.strip()
        await forge_db.set_pipeline_pr_url(pipeline_id, pr_url)
        return {"pr_url": pr_url}

    except HTTPException:
        raise
    except Exception as e:
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
    """Cancel a running pipeline.

    - Sets pipeline status to 'cancelled' (acts as a flag for the daemon loop)
    - Marks all non-terminal tasks as CANCELLED
    - Handles pipelines stuck in 'planning' phase
    - Emits pipeline:cancelled WebSocket event
    - Returns list of cancelled task IDs
    """
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(500, "Database not configured")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(404, "Pipeline not found")

    if pipeline.status == "cancelled":
        return {"status": "already_cancelled", "tasks_cancelled": [], "pipeline_id": pipeline_id}

    # Use cancel_pipeline_hard for atomicity and timestamp
    await forge_db.cancel_pipeline_hard(pipeline_id)

    # Collect the IDs of tasks that were cancelled
    tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
    cancelled_task_ids = [t.id for t in tasks if t.state == "cancelled"]

    # If pipeline was in planning phase, remove from pending_graphs
    if pipeline.status == "planning":
        lock = getattr(request.app.state, "pending_graphs_lock", None)
        if lock:
            async with lock:
                request.app.state.pending_graphs.pop(pipeline_id, None)
        else:
            pending = getattr(request.app.state, "pending_graphs", {})
            pending.pop(pipeline_id, None)

    # Emit WebSocket event so frontend updates immediately
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager:
        await ws_manager.broadcast(pipeline_id, {
            "type": "pipeline:cancelled",
            "pipeline_id": pipeline_id,
            "tasks_cancelled": cancelled_task_ids,
        })

    return {
        "status": "cancelled",
        "pipeline_id": pipeline_id,
        "tasks_cancelled": cancelled_task_ids,
    }


@router.post("/{pipeline_id}/restart")
async def restart_pipeline(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    body: RestartPipelineRequest | None = None,
):
    """Restart a pipeline from scratch.

    - Resets all tasks and clears pipeline state via db.restart_pipeline()
    - Optionally cleans up leftover worktrees
    - Re-invokes daemon factory for fresh planning
    - Emits pipeline:restarted WebSocket event
    - Returns new pipeline status
    """
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(500, "Database not configured")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(404, "Pipeline not found")

    # Save original description and config before reset
    original_description = pipeline.description
    project_dir = pipeline.project_dir
    model_strategy = pipeline.model_strategy

    # Reset all state in DB
    reset_result = await forge_db.restart_pipeline(pipeline_id)

    # Remove from pending_graphs if present
    lock = getattr(request.app.state, "pending_graphs_lock", None)
    if lock:
        async with lock:
            request.app.state.pending_graphs.pop(pipeline_id, None)
    else:
        pending = getattr(request.app.state, "pending_graphs", {})
        pending.pop(pipeline_id, None)

    # Clean up leftover worktrees if requested
    clean_worktrees = body.clean_worktrees if body else True
    if clean_worktrees:
        try:
            from forge.merge.worktree import WorktreeManager
            wt_manager = WorktreeManager(project_dir)
            tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
            for task in tasks:
                if task.worktree_path:
                    try:
                        wt_manager.remove(task.worktree_path)
                    except Exception:
                        logger.debug("Failed to clean worktree %s", task.worktree_path)
        except ImportError:
            logger.debug("WorktreeManager not available for worktree cleanup")
        except Exception:
            logger.debug("Worktree cleanup skipped: %s", pipeline_id)

    # Set pipeline back to planning status
    await forge_db.update_pipeline_status(pipeline_id, "planning")

    # Emit WebSocket event
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager:
        await ws_manager.broadcast(pipeline_id, {
            "type": "pipeline:restarted",
            "pipeline_id": pipeline_id,
        })

    # Re-invoke daemon factory to start fresh planning
    daemon_factory = getattr(request.app.state, "daemon_factory", None)
    if daemon_factory:
        daemon, emitter = daemon_factory(project_dir, model_strategy)
        if ws_manager:
            _bridge_events(emitter, ws_manager, pipeline_id)

        async def _run_restart_plan():
            try:
                graph = await daemon.plan(
                    original_description, forge_db,
                    emit_plan_ready=False, pipeline_id=pipeline_id,
                )

                # Remap task IDs to be globally unique
                prefix = pipeline_id[:8]
                id_map = {t.id: f"{prefix}-{t.id}" for t in graph.tasks}
                for t in graph.tasks:
                    t.depends_on = [id_map.get(d, d) for d in t.depends_on]
                    t.id = id_map[t.id]

                # Re-emit plan_ready with remapped IDs
                if ws_manager:
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
                await forge_db.update_pipeline_status(pipeline_id, "planned")

                # Store graph for later execution
                graph_lock = getattr(request.app.state, "pending_graphs_lock", None)
                if graph_lock:
                    async with graph_lock:
                        request.app.state.pending_graphs[pipeline_id] = (graph, daemon)
                else:
                    request.app.state.pending_graphs[pipeline_id] = (graph, daemon)
            except Exception as exc:
                logger.exception("Restart planning failed for pipeline %s", pipeline_id)
                await forge_db.update_pipeline_status(pipeline_id, "error")
                if ws_manager:
                    await ws_manager.broadcast(pipeline_id, {
                        "type": "pipeline:error", "error": str(exc),
                    })

        asyncio.create_task(_run_restart_plan())

    return {
        "status": "restarting",
        "pipeline_id": pipeline_id,
        "tasks_reset": reset_result["tasks_reset"],
        "events_deleted": reset_result["events_deleted"],
    }


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

    # If pipeline was complete/cancelled/error, we need to reactivate it
    # AND re-launch the execution loop (otherwise the task stays in 'todo'
    # with nothing to pick it up).
    if pipeline.status in ("complete", "cancelled", "error"):
        await forge_db.update_pipeline_status(pipeline_id, "executing")

        # Reconstruct graph and daemon to run execution loop
        graph_json = json.loads(pipeline.task_graph_json) if pipeline.task_graph_json else None
        if graph_json:
            from forge.config.settings import ForgeSettings
            from forge.core.daemon import ForgeDaemon
            from forge.core.events import EventEmitter
            from forge.core.models import Complexity, TaskDefinition, TaskGraph

            task_defs = []
            for t in graph_json.get("tasks", []):
                task_defs.append(TaskDefinition(
                    id=t["id"], title=t["title"], description=t["description"],
                    files=t["files"], depends_on=t.get("depends_on", []),
                    complexity=Complexity(t.get("complexity", "medium")),
                ))
            graph = TaskGraph(tasks=task_defs)

            settings = ForgeSettings()
            emitter = getattr(request.app.state, "event_emitter", None)
            if emitter is None:
                emitter = EventEmitter()

            ws_manager = getattr(request.app.state, "ws_manager", None)
            if ws_manager:
                _bridge_events(emitter, ws_manager, pipeline_id)

            daemon = ForgeDaemon(pipeline.project_dir, settings=settings, event_emitter=emitter)

            async def _run_retry():
                try:
                    await daemon.execute(graph, forge_db, pipeline_id=pipeline_id, resume=True)
                    await forge_db.update_pipeline_status(pipeline_id, "complete")
                except Exception as e:
                    logger.error("Retry execution failed for %s: %s", task_id, e)
                    await forge_db.update_pipeline_status(pipeline_id, "error")

            asyncio.create_task(_run_retry())

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

        # Build per-task event data + extract planner output
        task_events: dict[str, dict] = {}
        planner_output_lines: list[str] = []
        timeline = []
        for ev in events:
            timeline.append({
                "type": ev.event_type,
                "task_id": ev.task_id,
                "payload": ev.payload,
                "timestamp": ev.created_at,
            })
            # Collect planner output lines (pipeline-level, no task_id)
            if ev.event_type == "planner:output":
                line = ev.payload.get("line", "")
                if line:
                    planner_output_lines.append(line)

            if ev.task_id:
                te = task_events.setdefault(ev.task_id, {
                    "output": [], "reviewGates": [], "mergeResult": None, "cost_usd": 0, "files_changed": [],
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
                    te["cost_usd"] = (te["cost_usd"] or 0) + (ev.payload.get("cost_usd", 0))
                elif ev.event_type == "task:files_changed":
                    te["files_changed"] = ev.payload.get("files", [])
                elif ev.event_type == "task:state_changed":
                    te["state"] = ev.payload.get("state")
                    # Clear review gates when a retry starts so REST hydration
                    # shows only the current attempt's gates, not all retries.
                    if ev.payload.get("state") == "in_progress":
                        te["reviewGates"] = []
                        te["mergeResult"] = None

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
            pr_url=pipeline.pr_url,
            planner_output=planner_output_lines,
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
    # Use the actual branch name stored by the daemon (supports custom names from UI)
    branch_name = getattr(pipeline, "branch_name", None)
    if not branch_name:
        branch_name = f"forge/pipeline-{pipeline_id[:8]}"
        logger.warning("Pipeline %s missing branch_name in DB, using fallback: %s", pipeline_id, branch_name)

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

    # Use stored base_branch from pipeline record (set by daemon at pipeline start).
    # Falls back to detecting current branch for backward compatibility.
    base_branch = getattr(pipeline, "base_branch", None)
    if not base_branch:
        base_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_dir, capture_output=True, text=True,
        ).stdout.strip() or "main"

    # Pipeline branch already exists (created by daemon at start) with all
    # merged task code.  Just push it — no checkout needed.
    push_result = subprocess.run(
        ["git", "push", "-u", "--force-with-lease", remote_name, branch_name],
        cwd=project_dir, capture_output=True, text=True,
    )
    if push_result.returncode != 0:
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
         "--base", base_branch,
         "--head", branch_name,
         "--title", f"forge: {pipeline.description[:60]}",
         "--body", pr_body],
        cwd=project_dir, capture_output=True, text=True,
    )

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
        "pipeline:cancelled",
        "pipeline:restarted",
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
