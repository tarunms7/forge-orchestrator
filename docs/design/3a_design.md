# Design 3A: GitHub Webhook Integration — Issue-Triggered Pipelines

**Feature:** GitHub webhook endpoint, issue-linked pipelines, progress comments, collaborator gating
**Status:** Draft
**Date:** 2026-03-05

---

## 1. Overview

Currently, Forge pipelines are only triggered manually — via `forge run` (CLI) or `POST /api/tasks` (web UI). This design adds a **GitHub webhook integration** that automatically triggers pipelines from GitHub issue comments.

When a collaborator comments `/forge` on a GitHub issue, Forge:

1. Verifies the webhook signature (HMAC-SHA256).
2. Checks the commenter is a repository collaborator.
3. Extracts the issue title, body, and comment text as the task description.
4. Creates a pipeline linked to the issue.
5. Posts progress updates as issue comments (planning started, plan ready, pipeline complete/failed).
6. Includes `Closes #N` in the auto-created PR body.

---

## 2. Sequence Diagram

```
GitHub                          Forge API                       ForgeDaemon
  │                                │                                │
  │  POST /api/webhooks/github     │                                │
  │  (issue_comment event)         │                                │
  │──────────────────────────────►│                                │
  │                                │                                │
  │                                │ 1. Verify X-Hub-Signature-256  │
  │                                │    (HMAC-SHA256 of raw body)   │
  │                                │                                │
  │                                │ 2. Parse payload:              │
  │                                │    - action == "created"       │
  │                                │    - comment.body starts "/forge"
  │                                │    - extract issue #, title,   │
  │                                │      body, comment text        │
  │                                │                                │
  │                                │ 3. Check collaborator perms    │
  │                                │    via webhook payload         │
  │                                │    (author_association field)  │
  │                                │                                │
  │                                │ 4. Rate-limit check            │
  │                                │    (1 pipeline/issue/5 min)    │
  │                                │                                │
  │                                │ 5. Repo allow-list check       │
  │                                │                                │
  │                                │ 6. Create pipeline in DB       │
  │                                │    (with github_issue_url,     │
  │                                │     github_issue_number)       │
  │                                │                                │
  │  ◄── 202 Accepted ────────────│                                │
  │                                │                                │
  │                                │ 7. Post issue comment:         │
  │  ◄── "Planning started..." ───│    "🔧 Planning started..."    │
  │                                │                                │
  │                                │ 8. daemon.plan()               │
  │                                │──────────────────────────────►│
  │                                │                                │ plan()
  │                                │  ◄──── TaskGraph ─────────────│
  │                                │                                │
  │  ◄── "Plan: N tasks" ─────────│ 9. Post issue comment:         │
  │                                │    "📋 Plan ready: N tasks"    │
  │                                │                                │
  │                                │ 10. daemon.execute()           │
  │                                │──────────────────────────────►│
  │                                │                                │ execute()
  │                                │  ◄──── complete ──────────────│
  │                                │                                │
  │                                │ 11. Auto-create PR             │
  │                                │     (with "Closes #N")         │
  │                                │                                │
  │  ◄── "Pipeline complete.      │ 12. Post issue comment:        │
  │       PR: #M" ────────────────│    "✅ Pipeline complete.      │
  │                                │     PR: #M"                    │
  │                                │                                │
  │         OR on failure:         │                                │
  │                                │                                │
  │  ◄── "Pipeline failed:        │ 13. Post issue comment:        │
  │       {error}" ───────────────│    "❌ Pipeline failed: ..."   │
```

---

## 3. Webhook Endpoint Implementation

### 3.1 New file: `forge/api/routes/webhooks.py`

```python
"""GitHub webhook endpoint: receive events and trigger pipelines."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature.

    Args:
        payload_body: Raw request body bytes.
        signature_header: Value of X-Hub-Signature-256 header (e.g. "sha256=abc123...").
        secret: The webhook secret (FORGE_GITHUB_WEBHOOK_SECRET).

    Returns:
        True if the signature is valid.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _is_collaborator(payload: dict) -> bool:
    """Check if the comment author is a repository collaborator.

    Uses the author_association field from the webhook payload.
    Trusted associations: OWNER, MEMBER, COLLABORATOR.
    """
    comment = payload.get("comment", {})
    association = comment.get("author_association", "").upper()
    return association in ("OWNER", "MEMBER", "COLLABORATOR")


def _extract_forge_command(comment_body: str) -> str | None:
    """Extract the task description from a /forge comment.

    Returns None if the comment doesn't start with /forge.
    Returns the text after /forge (stripped), or empty string if just "/forge".
    """
    body = comment_body.strip()
    if not body.startswith("/forge"):
        return None
    # Everything after "/forge" is the optional extra instruction
    return body[len("/forge"):].strip()


def _build_task_description(payload: dict, extra_instruction: str) -> str:
    """Build the pipeline task description from the GitHub issue + comment.

    Combines issue title, issue body, and the /forge comment into a single
    task description for the planner.
    """
    issue = payload.get("issue", {})
    title = issue.get("title", "")
    body = issue.get("body", "") or ""

    parts = [f"# {title}"]
    if body:
        parts.append(f"\n{body}")
    if extra_instruction:
        parts.append(f"\n## Additional Instructions\n{extra_instruction}")

    return "\n".join(parts)


# ── In-memory rate limiter for webhook-triggered pipelines ──────────
# Key: "{repo_full_name}#{issue_number}" -> last trigger timestamp
_webhook_rate_limit: dict[str, float] = {}
WEBHOOK_RATE_LIMIT_SECONDS = 300  # 5 minutes


def _check_rate_limit(repo: str, issue_number: int) -> bool:
    """Check if a pipeline can be triggered for this issue.

    Returns True if allowed, False if rate-limited.
    """
    key = f"{repo}#{issue_number}"
    now = datetime.now(timezone.utc).timestamp()
    last = _webhook_rate_limit.get(key, 0)
    if now - last < WEBHOOK_RATE_LIMIT_SECONDS:
        return False
    _webhook_rate_limit[key] = now
    return True


@router.post("/github")
async def github_webhook(request: Request) -> Response:
    """Receive GitHub webhook events and trigger pipelines.

    Expects:
        - X-Hub-Signature-256 header for HMAC verification
        - X-GitHub-Event header (only "issue_comment" is processed)
        - JSON payload with issue + comment data

    Returns:
        - 202 Accepted: Pipeline triggered successfully
        - 200 OK: Event acknowledged but not actionable (wrong event type, etc.)
        - 401 Unauthorized: Invalid or missing signature
        - 403 Forbidden: Comment author is not a collaborator
        - 429 Too Many Requests: Rate limited
    """
    # 1. Get webhook secret from app settings
    webhook_secret = getattr(request.app.state, "github_webhook_secret", None)
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # 2. Verify signature
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(raw_body, signature, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # 3. Check event type
    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type == "ping":
        return Response(content=json.dumps({"status": "pong"}), status_code=200)
    if event_type != "issue_comment":
        return Response(
            content=json.dumps({"status": "ignored", "reason": f"Event type '{event_type}' not handled"}),
            status_code=200,
        )

    # 4. Parse payload
    payload = json.loads(raw_body)

    # Only trigger on new comments (not edits/deletes)
    if payload.get("action") != "created":
        return Response(
            content=json.dumps({"status": "ignored", "reason": "Only 'created' actions are processed"}),
            status_code=200,
        )

    # 5. Check for /forge command
    comment_body = payload.get("comment", {}).get("body", "")
    extra_instruction = _extract_forge_command(comment_body)
    if extra_instruction is None:
        return Response(
            content=json.dumps({"status": "ignored", "reason": "Comment does not start with /forge"}),
            status_code=200,
        )

    # 6. Collaborator check
    if not _is_collaborator(payload):
        raise HTTPException(
            status_code=403,
            detail="Only repository collaborators can trigger Forge pipelines",
        )

    # 7. Repository allow-list check
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    allowed_repos = getattr(request.app.state, "github_allowed_repos", [])
    if allowed_repos and repo_full_name not in allowed_repos:
        raise HTTPException(
            status_code=403,
            detail=f"Repository '{repo_full_name}' is not in the allowed list",
        )

    # 8. Rate limit check
    issue_number = payload.get("issue", {}).get("number", 0)
    if not _check_rate_limit(repo_full_name, issue_number):
        raise HTTPException(
            status_code=429,
            detail="Rate limited: max 1 pipeline per issue per 5 minutes",
        )

    # 9. Build task description
    task_description = _build_task_description(payload, extra_instruction)
    issue_url = payload.get("issue", {}).get("html_url", "")
    repo_clone_url = payload.get("repository", {}).get("clone_url", "")
    comment_user = payload.get("comment", {}).get("user", {}).get("login", "unknown")

    # 10. Create pipeline
    forge_db = getattr(request.app.state, "db", None) or getattr(request.app.state, "forge_db", None)
    if forge_db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    import uuid
    pipeline_id = str(uuid.uuid4())

    # Determine project directory from settings or app state
    project_dir = getattr(request.app.state, "webhook_project_dir", None)
    if not project_dir:
        project_dir = os.getcwd()

    await forge_db.create_pipeline(
        id=pipeline_id,
        description=task_description[:200],
        project_dir=project_dir,
        model_strategy="auto",
        github_issue_url=issue_url,
        github_issue_number=issue_number,
    )

    # 11. Launch pipeline in background
    ws_manager = getattr(request.app.state, "ws_manager", None)
    daemon_factory = getattr(request.app.state, "daemon_factory", None)

    asyncio.create_task(
        _run_webhook_pipeline(
            forge_db=forge_db,
            ws_manager=ws_manager,
            daemon_factory=daemon_factory,
            pipeline_id=pipeline_id,
            project_dir=project_dir,
            task_description=task_description,
            issue_url=issue_url,
            issue_number=issue_number,
            repo_full_name=repo_full_name,
        )
    )

    return Response(
        content=json.dumps({
            "status": "accepted",
            "pipeline_id": pipeline_id,
            "issue_number": issue_number,
        }),
        status_code=202,
    )


async def _post_issue_comment(repo_full_name: str, issue_number: int, body: str, cwd: str) -> None:
    """Post a comment on a GitHub issue using the gh CLI.

    Args:
        repo_full_name: e.g. "owner/repo"
        issue_number: The issue number.
        body: The markdown comment body.
        cwd: Working directory for gh CLI.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "comment", str(issue_number),
        "--repo", repo_full_name,
        "--body", body,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "Failed to post issue comment on %s#%d: %s",
            repo_full_name, issue_number, stderr.decode().strip(),
        )


async def _run_webhook_pipeline(
    *,
    forge_db,
    ws_manager,
    daemon_factory,
    pipeline_id: str,
    project_dir: str,
    task_description: str,
    issue_url: str,
    issue_number: int,
    repo_full_name: str,
) -> None:
    """Execute the full pipeline lifecycle for a webhook-triggered pipeline.

    Posts progress updates as GitHub issue comments at each phase.
    """
    try:
        # Create daemon
        daemon, emitter = daemon_factory(project_dir, "auto")

        # Phase 1: Planning
        await _post_issue_comment(
            repo_full_name, issue_number,
            f"🔧 **Forge pipeline started** (`{pipeline_id[:8]}`)\n\nPlanning task decomposition...",
            project_dir,
        )

        graph = await daemon.plan(task_description, forge_db, pipeline_id=pipeline_id)

        task_list_md = "\n".join(
            f"- **{t.title}** ({t.complexity.value})" for t in graph.tasks
        )
        await _post_issue_comment(
            repo_full_name, issue_number,
            f"📋 **Plan ready: {len(graph.tasks)} tasks**\n\n{task_list_md}",
            project_dir,
        )

        # Phase 2: Execution
        await forge_db.update_pipeline_status(pipeline_id, "executing")
        await daemon.execute(graph, forge_db, pipeline_id=pipeline_id)
        await forge_db.update_pipeline_status(pipeline_id, "complete")

        # Phase 3: Auto-create PR with "Closes #N"
        # The _auto_create_pr function is reused, but we modify the PR body
        # to include the issue closing reference.
        # We import from tasks.py to reuse the existing logic.
        from forge.api.routes.tasks import _auto_create_pr_with_issue
        try:
            pr_url = await _auto_create_pr_with_issue(
                forge_db, pipeline_id, issue_number,
            )
            await _post_issue_comment(
                repo_full_name, issue_number,
                f"✅ **Pipeline complete!** PR: {pr_url}\n\n"
                f"This PR will automatically close this issue when merged.",
                project_dir,
            )
        except Exception as pr_exc:
            logger.warning("Auto-PR failed for webhook pipeline %s: %s", pipeline_id, pr_exc)
            await _post_issue_comment(
                repo_full_name, issue_number,
                f"✅ **Pipeline complete!** (PR creation failed: {pr_exc})",
                project_dir,
            )

    except Exception as exc:
        logger.exception("Webhook pipeline %s failed", pipeline_id)
        await forge_db.update_pipeline_status(pipeline_id, "error")
        await _post_issue_comment(
            repo_full_name, issue_number,
            f"❌ **Pipeline failed**\n\n```\n{str(exc)[:500]}\n```",
            project_dir,
        )
```

### 3.2 Key design decisions

| Decision | Rationale |
|---|---|
| Raw body read before JSON parse | HMAC must be computed on the exact bytes GitHub sent; `await request.body()` returns raw bytes |
| `author_association` from payload (not gh API call) | Avoids an extra API call; GitHub includes this in every issue_comment webhook payload |
| Module-level `_webhook_rate_limit` dict | Same pattern as existing `RateLimiter` in `forge/api/security/rate_limit.py`; simple and sufficient for single-process deployments |
| Background task via `asyncio.create_task` | Matches the existing `_run_execute()` pattern in `tasks.py:execute_pipeline()` |
| `gh issue comment` via subprocess | Matches existing `gh pr create` pattern in `github_service.py`; no new dependencies needed |

---

## 4. Security Model

### 4.1 Signature Verification

```python
# GitHub sends: X-Hub-Signature-256: sha256=<hex-digest>
# We compute: HMAC-SHA256(secret, raw_body) and compare with constant-time comparison
hmac.compare_digest(expected, received)
```

- **Secret source:** `FORGE_GITHUB_WEBHOOK_SECRET` env var, stored on `app.state.github_webhook_secret`
- **Failure mode:** 401 Unauthorized (no information leakage about expected signature)

### 4.2 Permission Checks

Two layers of authorization:

1. **Repository allow-list** (`FORGE_GITHUB_ALLOWED_REPOS`): If set, only webhooks from listed repositories are accepted. If empty/unset, all repos are allowed (for development convenience).

2. **Collaborator check via `author_association`**: Only `OWNER`, `MEMBER`, and `COLLABORATOR` associations can trigger pipelines. This prevents random users from triggering expensive pipeline runs on public repos.

   GitHub's `author_association` values and their meanings:
   | Value | Meaning | Allowed? |
   |---|---|---|
   | `OWNER` | Repository owner | Yes |
   | `MEMBER` | Organization member | Yes |
   | `COLLABORATOR` | Invited collaborator | Yes |
   | `CONTRIBUTOR` | Has merged PR | No |
   | `FIRST_TIME_CONTRIBUTOR` | First PR pending | No |
   | `FIRST_TIMER` | Never contributed | No |
   | `NONE` | No relationship | No |

### 4.3 Rate Limiting

- **Key:** `{repo_full_name}#{issue_number}` (e.g. `owner/repo#42`)
- **Window:** 5 minutes (300 seconds)
- **Limit:** 1 pipeline per key per window
- **Implementation:** In-memory dict with timestamps (same pattern as existing `RateLimiter`)
- **Response:** 429 Too Many Requests

### 4.4 GitHub App Authentication (Future)

For production deployments, a GitHub App provides:
- Fine-grained permissions (issue comments, PR creation)
- Installation-level auth (no user-level `gh` CLI dependency)
- Webhook secret per-installation

**Future implementation path:**
1. Register a GitHub App with `issues:write` and `pull_requests:write` permissions
2. Store app ID + private key in env vars: `FORGE_GITHUB_APP_ID`, `FORGE_GITHUB_APP_PRIVATE_KEY`
3. Generate installation access tokens via `POST /app/installations/{id}/access_tokens`
4. Use the token for API calls instead of `gh` CLI
5. This is out of scope for this design — current implementation uses `gh` CLI (existing pattern)

---

## 5. DB Schema Changes

### 5.1 PipelineRow — new columns

Add to `forge/storage/db.py` `PipelineRow`:

```python
class PipelineRow(Base):
    __tablename__ = "pipelines"

    # ... existing columns ...

    # GitHub issue integration (nullable — only set for webhook-triggered pipelines)
    github_issue_url: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    github_issue_number: Mapped[int | None] = mapped_column(default=None)
```

**Migration:** The existing `_add_missing_columns` mechanism in `Database.initialize()` will automatically add these columns to existing databases via `ALTER TABLE ... ADD COLUMN`. No manual migration needed.

### 5.2 Database method updates

Update `create_pipeline()` to accept the new optional fields:

```python
async def create_pipeline(
    self, id: str, description: str, project_dir: str,
    model_strategy: str = "auto", user_id: str | None = None,
    base_branch: str | None = None, branch_name: str | None = None,
    build_cmd: str | None = None, test_cmd: str | None = None,
    budget_limit_usd: float = 0.0,
    # New: GitHub issue fields
    github_issue_url: str | None = None,
    github_issue_number: int | None = None,
) -> None:
```

---

## 6. Settings Additions

### 6.1 ForgeSettings — new fields

Add to `forge/config/settings.py`:

```python
class ForgeSettings(BaseSettings):
    model_config = {"env_prefix": "FORGE_"}

    # ... existing settings ...

    # GitHub webhook integration
    github_webhook_secret: str = ""  # Required for webhook endpoint
    github_allowed_repos: list[str] = []  # Empty = allow all
    github_webhook_project_dir: str = ""  # Project dir for webhook pipelines; empty = os.getcwd()
```

### 6.2 App state setup

In `forge/api/app.py`, during app creation:

```python
from forge.config.settings import ForgeSettings

settings = ForgeSettings()

# GitHub webhook settings on app.state
app.state.github_webhook_secret = settings.github_webhook_secret
app.state.github_allowed_repos = settings.github_allowed_repos
app.state.webhook_project_dir = settings.github_webhook_project_dir or None
```

### 6.3 Environment variables

| Variable | Default | Description |
|---|---|---|
| `FORGE_GITHUB_WEBHOOK_SECRET` | `""` (empty) | HMAC secret for webhook signature verification. Required. |
| `FORGE_GITHUB_ALLOWED_REPOS` | `[]` (empty) | Comma-separated list of `owner/repo` strings. If empty, all repos are allowed. |
| `FORGE_GITHUB_WEBHOOK_PROJECT_DIR` | `""` (empty) | Absolute path to the project directory for webhook-triggered pipelines. Falls back to `os.getcwd()`. |

---

## 7. API Additions

### 7.1 New endpoint

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/webhooks/github` | Webhook signature | Receive GitHub webhook events |

**Not** behind JWT auth — uses webhook signature verification instead.

### 7.2 Router registration

In `forge/api/app.py`:

```python
from forge.api.routes.webhooks import router as webhooks_router

app.include_router(webhooks_router, prefix="/api")
```

This results in the endpoint being at `POST /api/webhooks/github`.

### 7.3 Modified existing endpoint: `_auto_create_pr`

Add a new variant function `_auto_create_pr_with_issue()` in `forge/api/routes/tasks.py` that wraps the existing `_auto_create_pr` logic but appends `Closes #N` to the PR body:

```python
async def _auto_create_pr_with_issue(forge_db, pipeline_id: str, issue_number: int) -> str:
    """Create a GitHub PR for a webhook-triggered pipeline.

    Same as _auto_create_pr but appends 'Closes #N' to the PR body
    so merging the PR auto-closes the linked GitHub issue.
    """
    # This function reuses the exact same logic as _auto_create_pr,
    # but modifies the PR body to include the issue reference.
    # Implementation: call _auto_create_pr internals with modified body.
```

**Implementation approach:** Extract the PR body construction into a helper that accepts an optional `issue_number` parameter. This avoids duplicating the push/PR-creation logic:

```python
def _build_pr_body(pipeline, task_list: list[dict], issue_number: int | None = None) -> str:
    """Build the PR body markdown. Optionally includes 'Closes #N'."""
    task_summary = "\n".join(f"- {t.get('title', t.get('id', ''))}" for t in task_list)

    body = (
        f"## Summary\n\n"
        f"{pipeline.description}\n\n"
    )

    if issue_number:
        body += f"Closes #{issue_number}\n\n"

    body += (
        f"## Tasks Completed\n\n"
        f"{task_summary}\n\n"
        f"---\n"
        f"*Automated PR created by [Forge](https://github.com/tarunms7/forge-orchestrator) "
        f"pipeline `{pipeline.id[:8]}`*"
    )
    return body
```

### 7.4 History endpoint changes

Update `forge/api/routes/history.py` to include GitHub issue metadata in responses:

```python
# In list_history:
results.append({
    # ... existing fields ...
    "github_issue_url": getattr(p, "github_issue_url", None),
    "github_issue_number": getattr(p, "github_issue_number", None),
})

# In get_history_detail:
return {
    # ... existing fields ...
    "github_issue_url": getattr(pipeline, "github_issue_url", None),
    "github_issue_number": getattr(pipeline, "github_issue_number", None),
}
```

---

## 8. Frontend Changes

### 8.1 Pipeline View Page (`web/src/app/tasks/view/page.tsx`)

Show a GitHub issue link in the pipeline header, next to the description:

```tsx
{/* In the pipeline-meta-wrap div, after the description */}
{githubIssueUrl && (
  <a
    href={githubIssueUrl}
    target="_blank"
    rel="noopener noreferrer"
    className="github-issue-link"
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 6,
      fontSize: 13,
      fontWeight: 500,
      color: "var(--accent)",
      textDecoration: "none",
      marginTop: 4,
    }}
  >
    {/* GitHub issue icon */}
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 9.5a1.5 1.5 0 100-3 1.5 1.5 0 000 3z" />
      <path fillRule="evenodd" d="M8 0a8 8 0 100 16A8 8 0 008 0zM1.5 8a6.5 6.5 0 1113 0 6.5 6.5 0 01-13 0z" />
    </svg>
    Issue #{githubIssueNumber}
  </a>
)}
```

**Data source:** Fetch from the `/api/history/{pipeline_id}` endpoint (already used for pipeline description). Add `github_issue_url` and `github_issue_number` to the fetch.

### 8.2 History Page (`web/src/app/history/page.tsx`)

Show a GitHub issue icon + number badge in the pipeline table row:

```tsx
{/* In the history-pipeline-cell div, after existing badges */}
{item.github_issue_number && (
  <a
    href={item.github_issue_url}
    target="_blank"
    rel="noopener noreferrer"
    onClick={(e) => e.stopPropagation()}
    title={`Triggered from GitHub Issue #${item.github_issue_number}`}
    style={{
      marginLeft: "6px",
      fontSize: "10px",
      fontWeight: 600,
      padding: "1px 5px",
      borderRadius: "4px",
      background: "rgba(255,255,255,0.08)",
      color: "#c9d1d9",
      border: "1px solid rgba(255,255,255,0.15)",
      letterSpacing: "0.03em",
      textDecoration: "none",
    }}
  >
    #️ {item.github_issue_number}
  </a>
)}
```

### 8.3 HistoryItem interface update

```tsx
interface HistoryItem {
  // ... existing fields ...
  github_issue_url: string | null;
  github_issue_number: number | null;
}
```

### 8.4 TaskStore — no changes needed

The webhook pipeline uses the same pipeline events as web-triggered pipelines. The existing `handleEvent` in `taskStore.ts` already handles all the events (`pipeline:phase_changed`, `pipeline:plan_ready`, `pipeline:pr_created`, etc.). WebSocket broadcasting will work if anyone navigates to the pipeline view page with the webhook pipeline's ID.

---

## 9. Example Webhook Payload Handling

### 9.1 Incoming GitHub payload (issue_comment event)

```json
{
  "action": "created",
  "issue": {
    "number": 42,
    "title": "Add dark mode support to the settings page",
    "body": "## Description\n\nWe need dark mode toggle in settings.\n\n## Requirements\n- Theme toggle component\n- Persist preference in localStorage\n- Apply theme globally via CSS variables",
    "html_url": "https://github.com/owner/repo/issues/42"
  },
  "comment": {
    "body": "/forge Also add a keyboard shortcut (Ctrl+Shift+D) to toggle",
    "user": {
      "login": "maintainer-user"
    },
    "author_association": "MEMBER"
  },
  "repository": {
    "full_name": "owner/repo",
    "clone_url": "https://github.com/owner/repo.git"
  }
}
```

### 9.2 Forge processes this as

**Task description sent to planner:**

```
# Add dark mode support to the settings page

## Description

We need dark mode toggle in settings.

## Requirements
- Theme toggle component
- Persist preference in localStorage
- Apply theme globally via CSS variables

## Additional Instructions
Also add a keyboard shortcut (Ctrl+Shift+D) to toggle
```

### 9.3 Issue comments posted by Forge

**Comment 1 (pipeline start):**
> 🔧 **Forge pipeline started** (`a1b2c3d4`)
>
> Planning task decomposition...

**Comment 2 (plan ready):**
> 📋 **Plan ready: 3 tasks**
>
> - **Create ThemeToggle component** (medium)
> - **Add CSS variable theme system** (medium)
> - **Add keyboard shortcut for theme toggle** (low)

**Comment 3 (on success):**
> ✅ **Pipeline complete!** PR: https://github.com/owner/repo/pull/57
>
> This PR will automatically close this issue when merged.

**Comment 3 (on failure):**
> ❌ **Pipeline failed**
>
> ```
> Pre-flight checks failed: not a git repository
> ```

### 9.4 Auto-created PR body

```markdown
## Summary

Add dark mode support to the settings page

Closes #42

## Tasks Completed

- Create ThemeToggle component
- Add CSS variable theme system
- Add keyboard shortcut for theme toggle

---
*Automated PR created by [Forge](https://github.com/tarunms7/forge-orchestrator) pipeline `a1b2c3d4`*
```

---

## 10. File Ownership Map

| File | Change type | Description |
|---|---|---|
| `forge/api/routes/webhooks.py` | **New** | Webhook endpoint, signature verification, payload parsing, rate limiting, pipeline orchestration |
| `forge/api/routes/webhooks_test.py` | **New** | Tests for webhook endpoint |
| `forge/config/settings.py` | **Modify** | Add `github_webhook_secret`, `github_allowed_repos`, `github_webhook_project_dir` |
| `forge/config/settings_test.py` | **Modify** | Tests for new settings |
| `forge/storage/db.py` | **Modify** | Add `github_issue_url`, `github_issue_number` to PipelineRow; update `create_pipeline()` |
| `forge/storage/db_test.py` | **Modify** | Tests for new DB fields |
| `forge/api/app.py` | **Modify** | Register webhooks router, set app.state webhook settings |
| `forge/api/app_test.py` | **Modify** | Test webhook router registration |
| `forge/api/routes/tasks.py` | **Modify** | Extract `_build_pr_body()` helper, add `_auto_create_pr_with_issue()` |
| `forge/api/routes/tasks_test.py` | **Modify** | Tests for PR body with issue reference |
| `forge/api/routes/history.py` | **Modify** | Include GitHub issue fields in list + detail responses |
| `forge/api/routes/history_test.py` | **Modify** | Tests for issue fields in history |
| `web/src/app/tasks/view/page.tsx` | **Modify** | Show GitHub issue link in pipeline header |
| `web/src/app/history/page.tsx` | **Modify** | Show issue badge in history table |

---

## 11. Testing Strategy

### 11.1 Unit tests (webhook endpoint)

- **Signature verification:** Valid signature passes, invalid signature returns 401, missing header returns 401
- **Event filtering:** ping returns 200, non-issue_comment returns 200, issue_comment with action != "created" returns 200
- **Command parsing:** `/forge` with text, `/forge` alone, comment without `/forge` ignored
- **Collaborator check:** OWNER/MEMBER/COLLABORATOR allowed, others return 403
- **Rate limiting:** First request passes, second within 5 min returns 429
- **Repo allow-list:** Allowed repo passes, unlisted repo returns 403, empty list allows all
- **Pipeline creation:** Verify DB record created with correct issue_url and issue_number
- **Issue comment posting:** Mock subprocess calls, verify correct gh CLI arguments

### 11.2 Integration tests

- **End-to-end webhook flow:** Send a valid webhook payload, verify pipeline is created in DB, verify issue comments are posted (mocked gh CLI)
- **PR body with issue reference:** Verify PR body includes `Closes #N`
- **History endpoint:** Verify issue fields appear in list and detail responses

---

## 12. Configuration Guide

### Setting up the webhook:

1. **Set the webhook secret:**
   ```bash
   export FORGE_GITHUB_WEBHOOK_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
   export FORGE_GITHUB_WEBHOOK_PROJECT_DIR=/path/to/your/project
   ```

2. **Configure the GitHub webhook:**
   - Go to your repo → Settings → Webhooks → Add webhook
   - Payload URL: `https://your-forge-server.com/api/webhooks/github`
   - Content type: `application/json`
   - Secret: Same value as `FORGE_GITHUB_WEBHOOK_SECRET`
   - Events: Select "Issue comments"

3. **Optional: restrict to specific repos:**
   ```bash
   export FORGE_GITHUB_ALLOWED_REPOS="owner/repo1,owner/repo2"
   ```

4. **Start the Forge server:**
   ```bash
   forge serve
   ```

5. **Trigger a pipeline:**
   Comment `/forge` on any issue in the configured repository.
   Optionally add instructions: `/forge Also add unit tests for the new endpoint`
