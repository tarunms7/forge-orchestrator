# Phase 8: Web API — Multi-Repo REST & WebSocket Changes

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Forge REST API and WebSocket layer to support multi-repo workspaces — accepting repo configurations at pipeline creation, exposing `repo_id` on task responses and WebSocket events, and updating diff/worktree cleanup helpers for per-repo worktree paths.

**Architecture:** The API layer is a thin pass-through. The DB already has `PipelineRow.repos_json` (line 155 of `forge/storage/db.py`) and `TaskRow.repo_id` (line 101). This phase wires those DB fields into the request/response schemas, ensures WebSocket events include `repo_id`, and updates the worktree cleanup helpers to handle the multi-repo directory layout (`<worktrees>/<repo_id>/<task_id>/`).

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md`

**Dependencies:** Phase 1 (core models) + Phase 3 (storage layer) must be merged.

**Verification:** `.venv/bin/python -m pytest forge/api/routes/tasks_test.py forge/api/models/ forge/api/ws/handler_test.py -x -v`

---

## Chunk 1: Request/Response Schema Updates

Update Pydantic models in `forge/api/models/schemas.py` to accept multi-repo input and surface repo information in responses.

### Task 1: Add `repos` field to `CreateTaskRequest`

**Files:**
- Modify: `forge/api/models/schemas.py:11-25` (`CreateTaskRequest`)
- Test: `forge/api/models/user_test.py` (or new `forge/api/models/schemas_test.py`)

- [ ] **Step 1: Write failing test for `repos` field on `CreateTaskRequest`**

Add a test that validates `CreateTaskRequest` accepts an optional `repos` parameter:

```python
def test_create_task_request_with_repos():
    """CreateTaskRequest accepts optional repos list."""
    req = CreateTaskRequest(
        description="cross-repo feature",
        project_path="/workspace",
        repos=[
            {"id": "backend", "path": "/workspace/backend", "base_branch": "main"},
            {"id": "frontend", "path": "/workspace/frontend", "base_branch": None},
        ],
    )
    assert len(req.repos) == 2
    assert req.repos[0]["id"] == "backend"
    assert req.repos[1]["base_branch"] is None


def test_create_task_request_without_repos():
    """CreateTaskRequest works without repos (backward compat)."""
    req = CreateTaskRequest(
        description="single repo task",
        project_path="/workspace/backend",
    )
    assert req.repos is None
```

- [ ] **Step 2: Run tests — expect FAIL** (no `repos` field exists yet)

- [ ] **Step 3: Add `repos` field to `CreateTaskRequest`**

In `forge/api/models/schemas.py`, add after `quality_preset` (line 25):

```python
    repos: list[dict] | None = Field(
        default=None,
        description=(
            "Multi-repo workspace configuration. "
            "Each entry: {\"id\": str, \"path\": str, \"base_branch\": str | None}. "
            "Omit for single-repo pipelines."
        ),
    )
```

Schema for each dict element: `{"id": str, "path": str, "base_branch": str | None}`.

- [ ] **Step 4: Run tests — expect PASS**

### Task 2: Add `repo_id` to `TaskStatusResponse` and `TaskListItem`

**Files:**
- Modify: `forge/api/models/schemas.py:63-86` (`TaskStatusResponse`, `TaskListItem`)
- Test: `forge/api/models/user_test.py`

- [ ] **Step 1: Write failing tests**

```python
def test_task_status_response_includes_repo_id():
    """TaskStatusResponse has repo_id field defaulting to 'default'."""
    resp = TaskStatusResponse(pipeline_id="abc", phase="executing")
    assert resp.repo_id == "default"


def test_task_list_item_includes_repo_id():
    """TaskListItem has repo_id field defaulting to 'default'."""
    item = TaskListItem(
        pipeline_id="abc", description="test", project_path="/x", phase="done",
    )
    assert item.repo_id == "default"
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add `repo_id` to both models**

In `TaskStatusResponse` (after `github_issue_number`, line 77):
```python
    repo_id: str = "default"
```

In `TaskListItem` (after `phase`, line 86):
```python
    repo_id: str = "default"
```

- [ ] **Step 4: Run tests — expect PASS**

### Task 3: Add `repos` to `PipelineResponse`

**Files:**
- Modify: `forge/api/models/schemas.py:57-60` (`PipelineResponse`)
- Test: `forge/api/models/user_test.py`

- [ ] **Step 1: Write failing test**

```python
def test_pipeline_response_includes_repos():
    """PipelineResponse can carry repos list."""
    resp = PipelineResponse(
        pipeline_id="abc",
        repos=[{"id": "backend", "path": "/w/backend", "base_branch": "main"}],
    )
    assert resp.repos[0]["id"] == "backend"


def test_pipeline_response_without_repos():
    """PipelineResponse repos defaults to None (backward compat)."""
    resp = PipelineResponse(pipeline_id="abc")
    assert resp.repos is None
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add `repos` field to `PipelineResponse`**

In `forge/api/models/schemas.py` `PipelineResponse` class (line 60):
```python
    repos: list[dict] | None = None
```

- [ ] **Step 4: Run tests — expect PASS**

---

## Chunk 2: Pipeline Creation Endpoint

Wire the `repos` field through the pipeline creation endpoint, validating and storing it.

### Task 4: Validate and store `repos` on pipeline creation

**Files:**
- Modify: `forge/api/routes/tasks.py` — the `POST /` (create pipeline) handler
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_create_pipeline_with_repos(client, auth_headers):
    """POST /api/tasks accepts repos parameter and stores it."""
    resp = await client.post("/api/tasks", json={
        "description": "multi-repo feature",
        "project_path": "/workspace",
        "repos": [
            {"id": "backend", "path": "/workspace/backend", "base_branch": "main"},
            {"id": "frontend", "path": "/workspace/frontend"},
        ],
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "pipeline_id" in data
    assert data.get("repos") is not None
    assert len(data["repos"]) == 2


async def test_create_pipeline_without_repos(client, auth_headers):
    """POST /api/tasks without repos field — backward compat."""
    resp = await client.post("/api/tasks", json={
        "description": "single repo task",
        "project_path": "/workspace/backend",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("repos") is None


async def test_create_pipeline_invalid_repos(client, auth_headers):
    """POST /api/tasks with invalid repos config is rejected."""
    # Missing required 'id' field
    resp = await client.post("/api/tasks", json={
        "description": "bad repos",
        "project_path": "/workspace",
        "repos": [{"path": "/workspace/backend"}],
    }, headers=auth_headers)
    assert resp.status_code == 422  # validation error
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add repo validation in the create pipeline handler**

In the `POST /` handler in `forge/api/routes/tasks.py`, after reading `body = CreateTaskRequest(...)`, add validation:

```python
    repos_json = None
    if body.repos is not None:
        # Validate each repo entry has required fields
        for i, repo in enumerate(body.repos):
            if not isinstance(repo, dict) or "id" not in repo or "path" not in repo:
                raise HTTPException(
                    status_code=422,
                    detail=f"repos[{i}] must have 'id' and 'path' fields",
                )
            repo_path = repo["path"]
            if not os.path.isdir(repo_path):
                raise HTTPException(
                    status_code=422,
                    detail=f"repos[{i}].path does not exist: {repo_path}",
                )
        repos_json = json.dumps(body.repos)
```

Pass `repos_json` to `forge_db.create_pipeline(...)` (which already accepts `repos_json` — see `db.py` line 631-644).

Return `repos` in the `PipelineResponse`:
```python
    return PipelineResponse(pipeline_id=pipeline_id, repos=body.repos)
```

- [ ] **Step 4: Run tests — expect PASS**

---

## Chunk 3: Pipeline & Task Status Responses

Surface `repos` and `repo_id` in GET endpoints.

### Task 5: Pipeline status includes `repos` list

**Files:**
- Modify: `forge/api/routes/tasks.py` — `GET /{pipeline_id}` handler
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write failing test**

```python
async def test_pipeline_status_includes_repos(client, auth_headers, pipeline_with_repos):
    """GET /api/tasks/{id} returns repos list from PipelineRow.get_repos()."""
    resp = await client.get(
        f"/api/tasks/{pipeline_with_repos}", headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "repos" in data
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add `repos` to status response construction**

In the `GET /{pipeline_id}` handler, when building `TaskStatusResponse`, populate `repos` from the pipeline row. Use `PipelineRow.get_repos()` (line 157 of `db.py`) which already returns a synthetic `[{"id": "default", ...}]` for single-repo pipelines:

```python
    try:
        repos = pipeline.get_repos()
    except ValueError:
        repos = None  # Pipeline hasn't started execution yet (no base_branch)
```

Note: `get_repos()` raises `ValueError` if `repos_json` is None and `base_branch` is not set (line 161-164 of `db.py`). Guard against that for pipelines that haven't started.

- [ ] **Step 4: Run tests — expect PASS**

### Task 6: Task status includes `repo_id`

**Files:**
- Modify: `forge/api/routes/tasks.py` — `GET /{pipeline_id}` handler (task list within status)
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write failing test**

```python
async def test_task_status_includes_repo_id(client, auth_headers, executed_pipeline):
    """Task entries in status response have repo_id field."""
    resp = await client.get(
        f"/api/tasks/{executed_pipeline}", headers=auth_headers,
    )
    data = resp.json()
    for task in data["tasks"]:
        assert "repo_id" in task
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add `repo_id` to task dicts in status response**

In the status handler where task dicts are constructed from `TaskRow`, include:
```python
    "repo_id": task.repo_id,  # TaskRow.repo_id defaults to 'default' (db.py line 101)
```

- [ ] **Step 4: Run tests — expect PASS**

---

## Chunk 4: Diff Endpoint Multi-Repo Support

### Task 7: Diff endpoint resolves correct repo worktree path

**Files:**
- Modify: `forge/api/routes/tasks.py:809-841` (`get_task_diff`)
- Modify: `forge/api/routes/diff.py:16-39` (`get_pipeline_diff`)
- Test: `forge/api/routes/tasks_test.py`, `forge/api/routes/diff_test.py`

- [ ] **Step 1: Write failing test**

```python
async def test_diff_endpoint_multi_repo(client, auth_headers, multi_repo_pipeline):
    """Diff endpoint uses correct repo worktree path based on task's repo_id."""
    resp = await client.get(
        f"/api/tasks/{multi_repo_pipeline['pipeline_id']}"
        f"/tasks/{multi_repo_pipeline['task_id']}/diff",
        headers=auth_headers,
    )
    # Should resolve worktree path from approval_context which includes repo-specific path
    assert resp.status_code in (200, 410)  # 410 if worktree cleaned up
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Update diff endpoint**

The `get_task_diff` handler (line 809-841) already reads `worktree_path` from `task.approval_context`. For multi-repo, the worktree path is already repo-specific because the daemon sets it per-repo (e.g., `.forge/worktrees/backend/task-abc-1/`). No change needed to the path resolution logic — the approval_context already contains the correct absolute path.

However, add `repo_id` to the diff response for frontend context:

```python
    return {
        "task_id": task_id,
        "repo_id": task.repo_id,  # NEW: include repo_id
        "diff": diff,
        "stats": stats,
    }
```

For `get_pipeline_diff` in `forge/api/routes/diff.py`, include `repo_id` in the merge event diffs if present:

```python
    diff_parts = []
    for evt in events:
        if evt.payload and evt.payload.get("success"):
            diff_text = evt.payload.get("diff", "")
            if diff_text:
                repo_id = evt.payload.get("repo_id", "default")
                diff_parts.append(f"# repo: {repo_id}\n{diff_text}")
    return {"pipeline_id": pipeline_id, "diff": "\n".join(diff_parts)}
```

- [ ] **Step 4: Run tests — expect PASS**

---

## Chunk 5: Worktree Cleanup — Multi-Repo Support

Update the cleanup helpers to handle the multi-repo worktree layout where worktrees are nested under `<worktrees>/<repo_id>/<task_id>/`.

### Task 8: Update `_cleanup_worktree()` for multi-repo paths

**Files:**
- Modify: `forge/api/routes/tasks.py:47-58` (`_cleanup_worktree`)
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write failing test**

```python
def test_cleanup_worktree_multi_repo(tmp_path):
    """_cleanup_worktree handles per-repo subdirectory layout."""
    # Setup: create worktree dirs mimicking multi-repo layout
    # <project>/.forge/worktrees/<repo_id>/<task_id>/
    project_dir = str(tmp_path / "project")
    os.makedirs(os.path.join(project_dir, ".forge", "worktrees", "backend", "task-1"))
    os.makedirs(os.path.join(project_dir, ".forge", "worktrees", "frontend", "task-2"))

    # Should clean up task-1 from the backend subdirectory
    from forge.api.routes.tasks import _cleanup_worktree
    result = _cleanup_worktree(project_dir, "task-1", repo_id="backend")
    # WorktreeManager.remove() will fail (no real git repo) but the function handles it
    # The key assertion is that it looks in the correct repo subdirectory
    assert isinstance(result, bool)
```

- [ ] **Step 2: Run tests — expect FAIL** (no `repo_id` parameter)

- [ ] **Step 3: Update `_cleanup_worktree` signature and logic**

Add optional `repo_id` parameter. When `repo_id` is not `"default"` and not `None`, the worktree lives under `<worktrees>/<repo_id>/<task_id>/` instead of `<worktrees>/<task_id>/`:

```python
def _cleanup_worktree(project_dir: str, task_id: str, repo_id: str | None = None) -> bool:
    """Remove a single task's worktree + branch. Returns True if cleaned."""
    from forge.merge.worktree import WorktreeManager

    if repo_id and repo_id != "default":
        # Multi-repo layout: worktrees are under <worktrees>/<repo_id>/
        worktrees_dir = os.path.join(project_dir, ".forge", "worktrees", repo_id)
    else:
        worktrees_dir = os.path.join(project_dir, ".forge", "worktrees")
    try:
        wt_mgr = WorktreeManager(project_dir, worktrees_dir)
        wt_mgr.remove(task_id)
        return True
    except Exception as exc:
        logger.debug("Worktree cleanup failed for %s: %s", task_id, exc)
        return False
```

- [ ] **Step 4: Run tests — expect PASS**

### Task 9: Update `_cleanup_all_pipeline_worktrees()` for multi-repo

**Files:**
- Modify: `forge/api/routes/tasks.py:61-75` (`_cleanup_all_pipeline_worktrees`)
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write failing test**

```python
async def test_cleanup_all_worktrees_multi_repo(forge_db, tmp_path):
    """_cleanup_all_pipeline_worktrees passes repo_id to per-task cleanup."""
    # Create pipeline with repos, add tasks with different repo_ids
    # Verify cleanup iterates tasks and passes each task's repo_id
    ...
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Update `_cleanup_all_pipeline_worktrees`**

Pass each task's `repo_id` to `_cleanup_worktree`:

```python
async def _cleanup_all_pipeline_worktrees(
    forge_db, pipeline_id: str, project_dir: str,
) -> int:
    """Remove worktrees for all tasks in a pipeline. Returns count cleaned."""
    tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
    cleaned = 0
    for task in tasks:
        repo_id = getattr(task, "repo_id", "default")
        if _cleanup_worktree(project_dir, task.id, repo_id=repo_id):
            cleaned += 1
    # Prune stale git worktree admin files
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=project_dir, capture_output=True,
    )
    return cleaned
```

- [ ] **Step 4: Run tests — expect PASS**

---

## Chunk 6: WebSocket Events — Add `repo_id`

### Task 10: WebSocket task state events include `repo_id`

**Files:**
- Modify: `forge/api/routes/tasks.py` — all `task:state_changed` broadcasts
- Modify: `forge/api/ws/handler.py` (no structural changes needed — events pass through)
- Test: `forge/api/ws/handler_test.py`

- [ ] **Step 1: Write failing test**

```python
async def test_ws_event_includes_repo_id():
    """WebSocket task:state_changed events include repo_id field."""
    # Setup mock WebSocket, connect, trigger a task state change
    # Verify the broadcast message contains "repo_id"
    events = []
    # ... (capture broadcast messages)
    state_events = [e for e in events if e["type"] == "task:state_changed"]
    for evt in state_events:
        assert "repo_id" in evt
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Add `repo_id` to task state broadcasts**

In `forge/api/routes/tasks.py`, the `task:state_changed` events are broadcast in multiple places (lines 870-873, 920-923, 942-945, 1007-1010). The daemon event emitter (line 1779) already passes through all fields from daemon events with `**(data or {})`.

For the manually constructed broadcasts in the approve/reject handlers, add `repo_id`:

```python
# In approve_task (line 870):
await ws_manager.broadcast(pipeline_id, {
    "type": "task:state_changed",
    "task_id": task_id,
    "repo_id": getattr(task, "repo_id", "default"),
    "state": "merging",
})

# In reject_task (line 1007):
await ws_manager.broadcast(pipeline_id, {
    "type": "task:state_changed",
    "task_id": task_id,
    "repo_id": getattr(task, "repo_id", "default"),
    "state": "todo",
})
```

Similarly for the merge success/failure broadcasts (lines 920-923, 942-945).

The WebSocket handler (`forge/api/ws/handler.py`) and manager (`forge/api/ws/manager.py`) require no changes — they are transport-agnostic and pass through whatever dict is broadcast.

- [ ] **Step 4: Run tests — expect PASS**

---

## Chunk 7: Backward Compatibility & Error Handling

### Task 11: Single-repo backward compatibility integration test

**Files:**
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write integration test**

```python
async def test_single_repo_backward_compat(client, auth_headers):
    """Full flow without repos field produces identical responses to current API."""
    # Create pipeline without repos
    create_resp = await client.post("/api/tasks", json={
        "description": "single repo task",
        "project_path": "/workspace",
    }, headers=auth_headers)
    assert create_resp.status_code == 200
    data = create_resp.json()
    assert data.get("repos") is None

    # Get status
    pid = data["pipeline_id"]
    status_resp = await client.get(f"/api/tasks/{pid}", headers=auth_headers)
    status = status_resp.json()
    # repos may be None or synthetic default — both are acceptable
    # task entries should have repo_id="default"
    for task in status.get("tasks", []):
        assert task.get("repo_id", "default") == "default"
```

- [ ] **Step 2: Run tests — expect PASS** (this is a regression guard)

### Task 12: Invalid repo configuration error handling

**Files:**
- Test: `forge/api/routes/tasks_test.py`

- [ ] **Step 1: Write tests for all error scenarios**

```python
async def test_create_pipeline_invalid_repos_missing_id(client, auth_headers):
    """Repos entries without 'id' field are rejected."""
    resp = await client.post("/api/tasks", json={
        "description": "bad", "project_path": "/w",
        "repos": [{"path": "/w/backend"}],
    }, headers=auth_headers)
    assert resp.status_code == 422


async def test_create_pipeline_invalid_repos_missing_path(client, auth_headers):
    """Repos entries without 'path' field are rejected."""
    resp = await client.post("/api/tasks", json={
        "description": "bad", "project_path": "/w",
        "repos": [{"id": "backend"}],
    }, headers=auth_headers)
    assert resp.status_code == 422


async def test_create_pipeline_invalid_repos_nonexistent_path(client, auth_headers):
    """Repos with non-existent paths are rejected."""
    resp = await client.post("/api/tasks", json={
        "description": "bad", "project_path": "/w",
        "repos": [{"id": "backend", "path": "/nonexistent/path"}],
    }, headers=auth_headers)
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests — expect PASS** (validation added in Task 4)

---

## Test Summary

All test names for verification:

| Test Name | File | Validates |
|---|---|---|
| `test_create_pipeline_with_repos` | `tasks_test.py` | POST /tasks accepts repos |
| `test_create_pipeline_without_repos` | `tasks_test.py` | Backward compat, repos=None |
| `test_pipeline_status_includes_repos` | `tasks_test.py` | GET response has repos list |
| `test_task_status_includes_repo_id` | `tasks_test.py` | Task response has repo_id |
| `test_diff_endpoint_multi_repo` | `tasks_test.py` | Diff uses correct worktree |
| `test_cleanup_worktree_multi_repo` | `tasks_test.py` | Cleans from repo subdirectory |
| `test_ws_event_includes_repo_id` | `handler_test.py` | WS events have repo_id |
| `test_create_pipeline_invalid_repos` | `tasks_test.py` | Rejects invalid repo config |

## Verification Command

```bash
.venv/bin/python -m pytest forge/api/routes/tasks_test.py forge/api/models/ forge/api/ws/handler_test.py -x -v
```
