"""GitHub webhook endpoint: receive events and trigger pipelines."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response

from forge.core.async_utils import safe_create_task
from forge.core.provider_config import (
    build_provider_config_snapshot,
    build_provider_registry,
    build_settings_for_project,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Helper: HMAC-SHA256 signature verification
# ---------------------------------------------------------------------------


def _verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature.

    Args:
        payload_body: Raw request body bytes.
        signature_header: Value of X-Hub-Signature-256 header (``sha256=abc...``).
        secret: The webhook secret (FORGE_GITHUB_WEBHOOK_SECRET).

    Returns:
        True if the signature is valid.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            payload_body,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Helper: collaborator check via author_association
# ---------------------------------------------------------------------------

_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _is_collaborator(payload: dict) -> bool:
    """Check if the comment author is a repository collaborator.

    Uses the ``author_association`` field from the webhook payload.
    Trusted associations: OWNER, MEMBER, COLLABORATOR.
    """
    association = payload.get("comment", {}).get("author_association", "").upper()
    return association in _TRUSTED_ASSOCIATIONS


# ---------------------------------------------------------------------------
# Helper: parse /forge command from comment body
# ---------------------------------------------------------------------------


def _extract_forge_command(comment_body: str) -> str | None:
    """Extract the task description from a ``/forge`` comment.

    Returns ``None`` if the comment doesn't start with ``/forge``.
    Returns the text after ``/forge`` (stripped), or empty string if just
    ``/forge``.
    """
    body = comment_body.strip()
    if not body.startswith("/forge"):
        return None
    return body[len("/forge") :].strip()


# ---------------------------------------------------------------------------
# Helper: build task description from issue + comment
# ---------------------------------------------------------------------------


def _build_task_description(payload: dict, extra_instruction: str) -> str:
    """Combine issue title + body + comment text into planner input."""
    issue = payload.get("issue", {})
    title = issue.get("title", "")
    body = issue.get("body", "") or ""

    parts = [f"# {title}"]
    if body:
        parts.append(f"\n{body}")
    if extra_instruction:
        parts.append(f"\n## Additional Instructions\n{extra_instruction}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# In-memory rate limiter
# ---------------------------------------------------------------------------

_webhook_rate_limit: dict[str, float] = {}
WEBHOOK_RATE_LIMIT_SECONDS = 300  # 5 minutes
_RATE_LIMIT_MAX_ENTRIES = 10_000


def _check_rate_limit(repo: str, issue_number: int) -> bool:
    """Return True if a pipeline can be triggered for this issue.

    False if rate-limited (< 5 min since last trigger for the same issue).
    Also enforces a max-entries cap to prevent unbounded memory growth:
    when the dict exceeds ``_RATE_LIMIT_MAX_ENTRIES``, the oldest entries
    are evicted.
    """
    key = f"{repo}#{issue_number}"
    now = datetime.now(UTC).timestamp()

    # Evict expired entries first
    expired_cutoff = now - WEBHOOK_RATE_LIMIT_SECONDS
    expired_keys = [k for k, v in _webhook_rate_limit.items() if v < expired_cutoff]
    for k in expired_keys:
        del _webhook_rate_limit[k]

    # If still over capacity, evict oldest entries
    if len(_webhook_rate_limit) >= _RATE_LIMIT_MAX_ENTRIES:
        sorted_keys = sorted(_webhook_rate_limit, key=_webhook_rate_limit.get)  # type: ignore[arg-type]
        excess = len(_webhook_rate_limit) - _RATE_LIMIT_MAX_ENTRIES + 1
        for k in sorted_keys[:excess]:
            del _webhook_rate_limit[k]

    last = _webhook_rate_limit.get(key, 0)
    if now - last < WEBHOOK_RATE_LIMIT_SECONDS:
        return False
    _webhook_rate_limit[key] = now
    return True


# ---------------------------------------------------------------------------
# POST /webhooks/github
# ---------------------------------------------------------------------------


@router.post("/github")
async def github_webhook(request: Request) -> Response:
    """Receive GitHub webhook events and trigger pipelines.

    Returns:
        202 Accepted  -- pipeline triggered
        200 OK        -- event acknowledged but not actionable
        401           -- invalid / missing signature
        403           -- not a collaborator, or repo not allowed
        429           -- rate-limited
    """
    # 1. Webhook secret
    webhook_secret: str | None = getattr(request.app.state, "github_webhook_secret", None)
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # 2. Signature verification
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(raw_body, signature, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # 3. Event type
    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type == "ping":
        return Response(content=json.dumps({"status": "pong"}), status_code=200)
    if event_type != "issue_comment":
        return Response(
            content=json.dumps(
                {"status": "ignored", "reason": f"Event type '{event_type}' not handled"}
            ),
            status_code=200,
        )

    # 4. Parse payload
    payload = json.loads(raw_body)

    if payload.get("action") != "created":
        return Response(
            content=json.dumps(
                {"status": "ignored", "reason": "Only 'created' actions are processed"}
            ),
            status_code=200,
        )

    # 5. /forge command
    comment_body = payload.get("comment", {}).get("body", "")
    extra_instruction = _extract_forge_command(comment_body)
    if extra_instruction is None:
        return Response(
            content=json.dumps(
                {"status": "ignored", "reason": "Comment does not start with /forge"}
            ),
            status_code=200,
        )

    # 6. Collaborator check
    if not _is_collaborator(payload):
        raise HTTPException(
            status_code=403,
            detail="Only repository collaborators can trigger Forge pipelines",
        )

    # 7. Repo allow-list
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    allowed_repos: list[str] = getattr(request.app.state, "github_allowed_repos", [])
    if allowed_repos and repo_full_name not in allowed_repos:
        raise HTTPException(
            status_code=403,
            detail=f"Repository '{repo_full_name}' is not in the allowed list",
        )

    # 8. Rate limit
    issue_number: int = payload.get("issue", {}).get("number", 0)
    if not _check_rate_limit(repo_full_name, issue_number):
        raise HTTPException(
            status_code=429,
            detail="Rate limited: max 1 pipeline per issue per 5 minutes",
        )

    # 9. Build task description
    task_description = _build_task_description(payload, extra_instruction)
    issue_url = payload.get("issue", {}).get("html_url", "")

    # 10. Create pipeline in DB
    forge_db = getattr(request.app.state, "db", None) or getattr(
        request.app.state, "forge_db", None
    )
    if forge_db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    pipeline_id = str(uuid.uuid4())

    project_dir: str = getattr(request.app.state, "webhook_project_dir", None) or os.getcwd()
    settings, project_config = build_settings_for_project(project_dir, model_strategy="auto")
    registry = build_provider_registry(settings, project_config)
    provider_config_json = json.dumps(
        build_provider_config_snapshot(settings, registry, strategy="auto")
    )

    await forge_db.create_pipeline(
        id=pipeline_id,
        description=task_description,
        project_dir=project_dir,
        model_strategy="auto",
        github_issue_url=issue_url,
        github_issue_number=issue_number,
        provider_config=provider_config_json,
    )

    # 11. Launch background pipeline
    daemon_factory = getattr(request.app.state, "daemon_factory", None)

    safe_create_task(
        _run_webhook_pipeline(
            forge_db=forge_db,
            daemon_factory=daemon_factory,
            pipeline_id=pipeline_id,
            project_dir=project_dir,
            task_description=task_description,
            issue_url=issue_url,
            issue_number=issue_number,
            repo_full_name=repo_full_name,
        ),
        logger=logger,
        name="webhook-pipeline",
    )

    return Response(
        content=json.dumps(
            {
                "status": "accepted",
                "pipeline_id": pipeline_id,
                "issue_number": issue_number,
            }
        ),
        status_code=202,
    )


# ---------------------------------------------------------------------------
# Helper: post issue comment via gh CLI
# ---------------------------------------------------------------------------


async def _post_issue_comment(
    repo_full_name: str,
    issue_number: int,
    body: str,
    cwd: str,
) -> None:
    """Post a comment on a GitHub issue using ``gh issue comment``."""
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "issue",
        "comment",
        str(issue_number),
        "--repo",
        repo_full_name,
        "--body",
        body,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "Failed to post issue comment on %s#%d: %s",
            repo_full_name,
            issue_number,
            stderr.decode().strip(),
        )


# ---------------------------------------------------------------------------
# Background pipeline orchestration
# ---------------------------------------------------------------------------


async def _run_webhook_pipeline(
    *,
    forge_db,
    daemon_factory,
    pipeline_id: str,
    project_dir: str,
    task_description: str,
    issue_url: str,
    issue_number: int,
    repo_full_name: str,
) -> None:
    """Execute the full pipeline lifecycle for a webhook-triggered pipeline.

    Phases: plan -> execute -> PR, with issue comments at each stage.
    """
    try:
        pipeline = await forge_db.get_pipeline(pipeline_id)
        daemon, emitter = daemon_factory(
            project_dir,
            "auto",
            provider_config=getattr(pipeline, "provider_config", None) if pipeline else None,
        )

        # Phase 1: Planning
        await _post_issue_comment(
            repo_full_name,
            issue_number,
            f"🔧 **Forge pipeline started** (`{pipeline_id[:8]}`)\n\nPlanning task decomposition...",
            project_dir,
        )

        graph = await daemon.plan(task_description, forge_db, pipeline_id=pipeline_id)

        task_list_md = "\n".join(
            f"- **{t.title}** ({t.complexity.value if hasattr(t.complexity, 'value') else t.complexity})"
            for t in graph.tasks
        )
        await _post_issue_comment(
            repo_full_name,
            issue_number,
            f"📋 **Plan ready: {len(graph.tasks)} tasks**\n\n{task_list_md}",
            project_dir,
        )

        # Phase 2: Execution
        await forge_db.update_pipeline_status(pipeline_id, "executing")
        await daemon.execute(graph, forge_db, pipeline_id=pipeline_id)
        await forge_db.update_pipeline_status(pipeline_id, "complete")

        # Phase 3: Auto-create PR with "Closes #N"
        from forge.api.routes.tasks import _auto_create_pr

        try:
            pr_url = await _auto_create_pr(forge_db, pipeline_id, issue_number=issue_number)
            await _post_issue_comment(
                repo_full_name,
                issue_number,
                f"✅ **Pipeline complete!** PR: {pr_url}\n\n"
                f"This PR will automatically close this issue when merged.",
                project_dir,
            )
        except Exception as pr_exc:
            logger.warning("Auto-PR failed for webhook pipeline %s: %s", pipeline_id, pr_exc)
            await _post_issue_comment(
                repo_full_name,
                issue_number,
                "✅ **Pipeline complete!** (PR creation failed. Check server logs for details.)",
                project_dir,
            )

    except Exception as exc:
        logger.exception("Webhook pipeline %s failed", pipeline_id)
        try:
            await forge_db.update_pipeline_status(pipeline_id, "error")
        except Exception:
            pass
        await _post_issue_comment(
            repo_full_name,
            issue_number,
            f"❌ **Pipeline failed** (`{type(exc).__name__}`). Check server logs for details.",
            project_dir,
        )
