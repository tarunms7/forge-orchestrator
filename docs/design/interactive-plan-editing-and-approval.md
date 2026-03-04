# Design: Interactive Plan Editing & Pre-Merge Human Approval

## 1. Plan Editing UI

### 1.1 Wireframe — Editable PlanPanel

```
┌─────────────────────────────────────────────────────────────────────┐
│  Plan — 4 tasks                               [+ Add Task] [Execute Plan] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─ Task Card (editable) ─────────────────────────────────────────┐ │
│  │ [drag ≡]  #task-1  [__Title input_______________]  [low ▾]     │ │
│  │                                                    [🗑 Delete]  │ │
│  │  ▸ expand                                                       │ │
│  │  ┌─────────────────────────────────────────────────────────────┐│ │
│  │  │ Description: [textarea______________________________________]│ │
│  │  │ Target files: [file-1.py] [file-2.py] [+ add file]          │ │
│  │  │ Dependencies: [task-2 ▾] [+ add dep]                        │ │
│  │  └─────────────────────────────────────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─ Task Card (collapsed) ────────────────────────────────────────┐ │
│  │ [drag ≡]  #task-2  Auth middleware setup         [medium ▾]    │ │
│  │                                                    [🗑 Delete]  │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─ Task Card ────────────────────────────────────────────────────┐ │
│  │ [drag ≡]  #task-3  Database migrations           [high ▾]     │ │
│  │           Depends: task-1, task-2                 [🗑 Delete]  │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─ NEW TASK (inline form, shown after clicking "+ Add Task") ────┐ │
│  │ ID: [auto: task-4]                                              │ │
│  │ Title: [________________________________]                       │ │
│  │ Description: [textarea___________________]                      │ │
│  │ Files: [_____________] [+ add]                                  │ │
│  │ Dependencies: [none ▾] [+ add]                                  │ │
│  │ Complexity: [medium ▾]                                          │ │
│  │                                     [Cancel]  [Add to Plan]     │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ⚠ Validation: No errors                                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Breakdown

| Component | Location | Purpose |
|-----------|----------|---------|
| `EditablePlanPanel` | `web/src/components/task/EditablePlanPanel.tsx` | New component replacing `PlanPanel` when `phase === "planned"` |
| `EditableTaskCard` | `web/src/components/task/EditableTaskCard.tsx` | Inline-editable card with title, desc, files, deps, complexity |
| `AddTaskForm` | `web/src/components/task/AddTaskForm.tsx` | Inline form for creating a new task |
| `PlanValidationBanner` | `web/src/components/task/PlanValidationBanner.tsx` | Shows client-side validation errors |

**Drag-and-drop**: Use `@dnd-kit/core` + `@dnd-kit/sortable` for reordering.
Visual ordering is for UX only — execution order is always derived from `depends_on`.

### 1.3 Client-side Validation Logic

The frontend validates the edited graph before allowing "Execute Plan".
Runs on every edit and displays errors inline.

```typescript
// web/src/lib/validateTaskGraph.ts

interface EditableTask {
  id: string;
  title: string;
  description: string;
  files: string[];
  depends_on: string[];
  complexity: "low" | "medium" | "high";
}

interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export function validateTaskGraph(tasks: EditableTask[]): ValidationResult {
  const errors: string[] = [];
  const ids = new Set(tasks.map(t => t.id));

  // 1. No empty tasks
  if (tasks.length === 0) {
    errors.push("Plan must have at least one task.");
  }

  // 2. No duplicate IDs
  const seenIds = new Set<string>();
  for (const t of tasks) {
    if (seenIds.has(t.id)) errors.push(`Duplicate task ID: "${t.id}".`);
    seenIds.add(t.id);
  }

  // 3. All dependencies reference valid IDs
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (!ids.has(dep)) {
        errors.push(`Task "${t.id}" depends on unknown task "${dep}".`);
      }
    }
  }

  // 4. No self-dependencies
  for (const t of tasks) {
    if (t.depends_on.includes(t.id)) {
      errors.push(`Task "${t.id}" depends on itself.`);
    }
  }

  // 5. Cycle detection (DFS)
  const visited = new Set<string>();
  const inStack = new Set<string>();
  const adj: Record<string, string[]> = {};
  for (const t of tasks) adj[t.id] = t.depends_on;

  function dfs(node: string): boolean {
    visited.add(node);
    inStack.add(node);
    for (const dep of adj[node] || []) {
      if (inStack.has(dep)) {
        errors.push(`Cycle detected involving tasks: ${node} → ${dep}.`);
        return true;
      }
      if (!visited.has(dep) && dfs(dep)) return true;
    }
    inStack.delete(node);
    return false;
  }
  for (const t of tasks) {
    if (!visited.has(t.id)) dfs(t.id);
  }

  // 6. Every task must have at least one file
  for (const t of tasks) {
    if (t.files.length === 0) {
      errors.push(`Task "${t.id}" must declare at least one target file.`);
    }
  }

  // 7. No file conflicts (same file in two independent tasks)
  // NOTE: file conflicts between tasks with a dependency chain are OK
  // (the dependent task intentionally modifies the same file).
  // Only flag conflicts between tasks with NO transitive dependency.
  const fileOwners: Record<string, string> = {};
  for (const t of tasks) {
    for (const f of t.files) {
      if (f in fileOwners && !hasTransitiveDep(tasks, t.id, fileOwners[f]) &&
          !hasTransitiveDep(tasks, fileOwners[f], t.id)) {
        errors.push(
          `File "${f}" is claimed by both "${fileOwners[f]}" and "${t.id}" ` +
          `with no dependency between them.`
        );
      }
      if (!(f in fileOwners)) fileOwners[f] = t.id;
    }
  }

  // 8. Non-empty title and description
  for (const t of tasks) {
    if (!t.title.trim()) errors.push(`Task "${t.id}" has an empty title.`);
  }

  return { valid: errors.length === 0, errors };
}

function hasTransitiveDep(
  tasks: EditableTask[], fromId: string, toId: string
): boolean {
  const visited = new Set<string>();
  const adj: Record<string, string[]> = {};
  for (const t of tasks) adj[t.id] = t.depends_on;

  function dfs(node: string): boolean {
    if (node === toId) return true;
    visited.add(node);
    for (const dep of adj[node] || []) {
      if (!visited.has(dep) && dfs(dep)) return true;
    }
    return false;
  }
  return dfs(fromId);
}
```

### 1.4 Zustand Store Changes

```typescript
// Additions to web/src/stores/taskStore.ts

// New state in PipelineState:
editedTasks: EditableTask[] | null;  // null = no edits, use original plan
planValidation: ValidationResult;

// New actions:
setEditedTasks: (tasks: EditableTask[]) => void;
updateEditedTask: (id: string, patch: Partial<EditableTask>) => void;
deleteEditedTask: (id: string) => void;
addEditedTask: (task: EditableTask) => void;
reorderEditedTasks: (fromIndex: number, toIndex: number) => void;
resetEdits: () => void;  // revert to original plan
```

When `pipeline:plan_ready` fires, `editedTasks` is initialized as a deep copy of the incoming tasks. All edits mutate `editedTasks`. `planValidation` is recomputed on every mutation.

### 1.5 Delete Cascade UX

When a user clicks "Delete" on a task:

1. Check if any other tasks depend on it.
2. If yes, show a confirmation dialog:
   ```
   ┌─────────────────────────────────────────────┐
   │  Delete Task "task-2"?                       │
   │                                              │
   │  The following tasks depend on this task:     │
   │    • task-3: Database migrations              │
   │    • task-4: API endpoints                    │
   │                                              │
   │  Their dependency on "task-2" will be         │
   │  removed. You may need to adjust them.        │
   │                                              │
   │              [Cancel]  [Delete Anyway]         │
   └─────────────────────────────────────────────┘
   ```
3. On confirm, remove the task AND remove its ID from all other tasks' `depends_on` arrays.

### 1.6 Add Task — ID Generation

New tasks get auto-generated IDs following the existing convention:

```typescript
function generateTaskId(existingIds: Set<string>, prefix: string): string {
  // prefix is the pipeline's 8-char prefix, e.g. "a1b2c3d4"
  let counter = existingIds.size + 1;
  while (existingIds.has(`${prefix}-task-${counter}`)) counter++;
  return `${prefix}-task-${counter}`;
}
```

---

## 2. API Contract for Plan Submission

### 2.1 Modified Execute Endpoint

The existing `POST /tasks/{pipeline_id}/execute` already accepts `ExecuteRequest` with an optional `tasks` field. We formalize its schema:

```python
# forge/api/models/schemas.py — updated

class EditedTaskDefinition(BaseModel):
    """A single task as edited by the user."""
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    files: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    complexity: Literal["low", "medium", "high"] = "medium"

class ExecuteRequest(BaseModel):
    """Optional: edited task graph to execute instead of the planned one."""
    tasks: list[EditedTaskDefinition] | None = None
```

### 2.2 Backend Execute Flow (modified)

```python
# In POST /{pipeline_id}/execute handler:

body = ExecuteRequest.parse_obj(await request.json()) if has_body else ExecuteRequest()

if body.tasks is not None:
    # User submitted an edited graph — build TaskGraph from it
    from forge.core.models import TaskDefinition, TaskGraph, Complexity
    from forge.core.validator import validate_task_graph

    task_defs = [
        TaskDefinition(
            id=t.id, title=t.title, description=t.description,
            files=t.files, depends_on=t.depends_on,
            complexity=Complexity(t.complexity),
        )
        for t in body.tasks
    ]
    graph = TaskGraph(tasks=task_defs)

    # Re-validate server-side (never trust client)
    try:
        validate_task_graph(graph)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid task graph: {e}")

    # Replace the pending graph
    daemon = pending_graphs[pipeline_id][1]  # keep the daemon
    pending_graphs[pipeline_id] = (graph, daemon)

    # Update stored plan in DB
    await forge_db.set_pipeline_plan(pipeline_id, json.dumps({
        "tasks": [t.model_dump() for t in body.tasks]
    }))
else:
    # Use original planned graph (existing behavior)
    pass
```

### 2.3 Frontend Execute Call (modified)

```typescript
async function handleExecute() {
  if (!token || !pipelineId) return;
  setExecuting(true);
  try {
    const payload = editedTasks
      ? { tasks: editedTasks }  // send edited graph
      : {};                      // use original plan
    await apiPost(`/tasks/${pipelineId}/execute`, payload, token);
  } finally {
    setExecuting(false);
  }
}
```

---

## 3. Pre-Merge Approval

### 3.1 State Machine

```
                            ┌──────────────────────────┐
                            │                          │
                            ▼                          │
┌──────┐  dispatch  ┌─────────────┐  agent done  ┌──────────┐  review  ┌─────────────────────┐
│ TODO │ ─────────▶ │ IN_PROGRESS │ ───────────▶ │ IN_REVIEW│ ──────▶ │  review passed?      │
└──────┘            └─────────────┘              └──────────┘         └──────┬──────────────┘
                                                                           │
                                                    ┌──────────────────────┤
                                                    │ No                   │ Yes
                                                    ▼                      ▼
                                              ┌──────────┐    ┌────────────────────────┐
                                              │  RETRY   │    │ require_approval?       │
                                              └──────────┘    └───────┬────────────────┘
                                                                      │
                                                   ┌──────────────────┤
                                                   │ No               │ Yes
                                                   ▼                  ▼
                                             ┌──────────┐    ┌────────────────────┐
                                             │ MERGING  │    │ AWAITING_APPROVAL  │
                                             └────┬─────┘    └────────┬───────────┘
                                                  │                   │
                                                  │         ┌────────┤─────────┐
                                                  │         │ approve          │ reject
                                                  │         ▼                  ▼
                                                  │   ┌──────────┐      ┌──────────┐
                                                  │   │ MERGING  │      │  RETRY   │
                                                  │   └────┬─────┘      └──────────┘
                                                  │        │
                                                  ▼        ▼
                                             ┌──────────────────┐
                                             │   DONE / ERROR   │
                                             └──────────────────┘
```

### 3.2 New TaskState Enum Value

```python
# forge/core/models.py — add to TaskState:

class TaskState(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    AWAITING_APPROVAL = "awaiting_approval"  # NEW
    MERGING = "merging"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"
```

### 3.3 Configuration

```python
# forge/config/settings.py — add:
require_approval: bool = False  # env var: FORGE_REQUIRE_APPROVAL

# forge/api/models/schemas.py — add to CreateTaskRequest:
require_approval: bool | None = None  # per-pipeline override

# PipelineRow — add column:
require_approval: Mapped[bool] = mapped_column(default=False)
```

Resolution order: per-pipeline field > `FORGE_REQUIRE_APPROVAL` env var > default `False`.

### 3.4 Daemon Integration

In `daemon_executor.py::_attempt_merge()`, after review passes but before `merge_worker.merge()`:

```python
# After review passes and before merge:
pipeline = await db.get_pipeline(pid)
require_approval = getattr(pipeline, "require_approval", False) or self._settings.require_approval

if require_approval:
    # Pause this task — await human approval
    await db.update_task_state(task_id, TaskState.AWAITING_APPROVAL.value)
    await self._emit("task:state_changed", {
        "task_id": task_id, "state": "awaiting_approval",
    }, db=db, pipeline_id=pid)

    # Send diff preview via WebSocket
    diff = _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)
    diff_preview = "\n".join(diff.splitlines()[:200])
    await self._emit("task:awaiting_approval", {
        "task_id": task_id,
        "diff_preview": diff_preview,
    }, db=db, pipeline_id=pid)

    # Now we must NOT block the execution loop. Return here.
    # The merge will be triggered by the /approve endpoint.
    # Store the merge context so the approve handler can resume it.
    await db.set_task_approval_context(task_id, json.dumps({
        "worktree_path": worktree_path,
        "agent_model": agent_model,
        "pipeline_branch": pipeline_branch,
    }))
    # Release agent so other tasks can use it
    await db.release_agent(agent_id)  # caller handles this
    return  # exit _attempt_merge; do NOT proceed to merge_worker.merge()
```

**Key design choice**: The `_execute_task` coroutine returns after setting
`AWAITING_APPROVAL`. The execution loop continues dispatching other tasks.
The merge is later triggered by the `/approve` endpoint, which calls
`_attempt_merge` with `skip_review=True`.

### 3.5 New API Endpoints

#### GET /tasks/{pipeline_id}/tasks/{task_id}/diff

```
Response 200:
{
  "task_id": "a1b2c3d4-task-1",
  "diff": "diff --git a/foo.py b/foo.py\n...",
  "stats": {
    "files_changed": 3,
    "lines_added": 42,
    "lines_removed": 7
  }
}
```

Implementation:
```python
@router.get("/{pipeline_id}/tasks/{task_id}/diff")
async def get_task_diff(
    pipeline_id: str, task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    forge_db = _get_forge_db(request)
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404)

    task = await forge_db.get_task(task_id)
    if task is None or task.pipeline_id != pipeline_id:
        raise HTTPException(status_code=404)
    if task.state != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Task is not awaiting approval")

    ctx = json.loads(task.approval_context) if task.approval_context else {}
    worktree_path = ctx.get("worktree_path")
    pipeline_branch = ctx.get("pipeline_branch")

    if not worktree_path or not os.path.isdir(worktree_path):
        raise HTTPException(status_code=410, detail="Worktree no longer exists")

    diff = _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)
    stats = _get_diff_stats(worktree_path, pipeline_branch=pipeline_branch)

    return {"task_id": task_id, "diff": diff, "stats": stats}
```

#### POST /tasks/{pipeline_id}/tasks/{task_id}/approve

```
Request body: {} (empty)
Response 202:
{ "status": "merging", "task_id": "a1b2c3d4-task-1" }
```

Implementation:
```python
@router.post("/{pipeline_id}/tasks/{task_id}/approve", status_code=202)
async def approve_task(
    pipeline_id: str, task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    forge_db = _get_forge_db(request)
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404)

    task = await forge_db.get_task(task_id)
    if task is None or task.state != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Task is not awaiting approval")

    # Launch merge in background
    async def _do_merge():
        ctx = json.loads(task.approval_context)
        daemon = _get_daemon_for_pipeline(request, pipeline_id)
        # ... reconstruct merge_worker, worktree_mgr from pipeline context
        # ... call daemon._attempt_merge(skip_review=True)

    asyncio.create_task(_do_merge())
    return {"status": "merging", "task_id": task_id}
```

#### POST /tasks/{pipeline_id}/tasks/{task_id}/reject

```
Request body:
{ "reason": "The auth logic doesn't handle token expiry" }  // optional

Response 200:
{ "status": "retrying", "task_id": "a1b2c3d4-task-1" }
```

Implementation:
```python
@router.post("/{pipeline_id}/tasks/{task_id}/reject")
async def reject_task(
    pipeline_id: str, task_id: str,
    request: Request,
    body: RejectRequest,  # new schema: { reason: str | None }
    user_id: str = Depends(get_current_user),
) -> dict:
    forge_db = _get_forge_db(request)
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404)

    task = await forge_db.get_task(task_id)
    if task is None or task.state != "awaiting_approval":
        raise HTTPException(status_code=409)

    # Set review feedback (the rejection reason) and trigger retry
    await forge_db.update_task_review_feedback(task_id, body.reason or "Rejected by user")
    await forge_db.update_task_state(task_id, "todo")
    await forge_db.increment_retry_count(task_id)

    # Clean up approval context
    await forge_db.clear_task_approval_context(task_id)

    # Emit event so frontend updates
    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast(pipeline_id, {
        "type": "task:state_changed",
        "task_id": task_id,
        "state": "todo",
    })

    return {"status": "retrying", "task_id": task_id}
```

### 3.6 Approval Context Storage

New column on `TaskRow`:

```python
# forge/storage/db.py — add to TaskRow:
approval_context: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

This stores a JSON blob with the worktree path, agent model, and pipeline branch
needed to resume the merge after approval. Cleared on reject or after successful merge.

New DB methods:
```python
async def set_task_approval_context(self, task_id: str, context_json: str) -> None: ...
async def clear_task_approval_context(self, task_id: str) -> None: ...
```

---

## 4. WebSocket Events

### New Events

| Event | Payload | When |
|-------|---------|------|
| `task:awaiting_approval` | `{ task_id, diff_preview }` | Review passed, approval required |
| `task:approval_approved` | `{ task_id }` | User approved, merge starting |
| `task:approval_rejected` | `{ task_id, reason }` | User rejected, retry queued |
| `pipeline:paused` | `{ paused_by: "user" }` | Pipeline paused |
| `pipeline:resumed` | `{}` | Pipeline resumed |

### Frontend Event Handlers

```typescript
// Additions to taskStore.ts handleEvent switch:

case "task:awaiting_approval": {
  const taskId = data.task_id as string;
  const existing = state.tasks[taskId];
  if (!existing) return { timeline: newTimeline };
  return {
    tasks: {
      ...state.tasks,
      [taskId]: {
        ...existing,
        state: "awaiting_approval",
        diffPreview: data.diff_preview as string,
      },
    },
    timeline: newTimeline,
  };
}

case "pipeline:paused":
  return { phase: "paused", timeline: newTimeline };

case "pipeline:resumed":
  return { phase: "executing", timeline: newTimeline };
```

### Frontend TaskState Update

```typescript
// Add to TaskState interface:
export interface TaskState {
  // ... existing fields ...
  diffPreview?: string;  // first 200 lines of diff for approval UI
}

// Add to BACKEND_STATE_MAP:
awaiting_approval: "awaiting_approval",

// Add to PipelineState phase union:
phase: "idle" | "planning" | "planned" | "executing" | "reviewing"
      | "paused" | "complete" | "cancelled" | "error";

// Add to TaskState["state"] union:
state: "pending" | "working" | "in_review" | "awaiting_approval"
      | "done" | "error" | "retrying" | "cancelled";
```

---

## 5. Approval UI Component

```
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Card — task-1: Add JWT Auth                                  │
│  State: ⏳ Awaiting Approval                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ diff --git a/forge/api/security/jwt.py                        │  │
│  │ +++ b/forge/api/security/jwt.py                               │  │
│  │ @@ -1,5 +1,25 @@                                              │  │
│  │ +import jwt                                                    │  │
│  │ +from datetime import timedelta                                │  │
│  │ ...                                                            │  │
│  │                                    [View Full Diff]            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Rejection reason (optional):                                       │
│  [__________________________________________________]              │
│                                                                     │
│  [Reject & Retry]                                  [Approve Merge]  │
└─────────────────────────────────────────────────────────────────────┘
```

New component: `web/src/components/task/ApprovalPanel.tsx`

- Shown in `TaskDetailPanel` when `task.state === "awaiting_approval"`.
- "View Full Diff" button calls `GET /tasks/{pid}/tasks/{tid}/diff` and opens
  a modal with syntax-highlighted diff (use existing code block styling).
- "Approve Merge" calls `POST .../approve` and updates local state optimistically.
- "Reject & Retry" calls `POST .../reject` with optional reason.

---

## 6. Pause/Resume Pipeline

### 6.1 Pipeline Phase Enum

```python
# The pipeline status field in PipelineRow is a free-form string.
# Valid values become: planning, planned, executing, paused, complete, error, cancelled
```

No new column needed — `status` already supports arbitrary strings.

### 6.2 New Column on PipelineRow

```python
# forge/storage/db.py — add to PipelineRow:
paused: Mapped[bool] = mapped_column(default=False)
```

### 6.3 API Endpoints

#### POST /tasks/{pipeline_id}/pause

```python
@router.post("/{pipeline_id}/pause")
async def pause_pipeline(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    forge_db = _get_forge_db(request)
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404)
    if pipeline.status not in ("executing", "planned"):
        raise HTTPException(status_code=409, detail="Pipeline is not running")

    await forge_db.set_pipeline_paused(pipeline_id, True)
    await forge_db.update_pipeline_status(pipeline_id, "paused")

    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast(pipeline_id, {
        "type": "pipeline:paused",
        "paused_by": "user",
    })

    return {"status": "paused"}
```

#### POST /tasks/{pipeline_id}/resume

```python
@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    forge_db = _get_forge_db(request)
    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404)

    await forge_db.set_pipeline_paused(pipeline_id, False)
    await forge_db.update_pipeline_status(pipeline_id, "executing")

    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast(pipeline_id, {
        "type": "pipeline:resumed",
    })

    return {"status": "executing"}
```

### 6.4 Execution Loop Integration

The `_execution_loop` in `daemon.py` checks the paused flag before dispatching:

```python
async def _execution_loop(self, db, runtime, worktree_mgr, merge_worker,
                          monitor, pipeline_id=None):
    """Loop until all tasks are DONE or ERROR."""
    prefix = pipeline_id[:8] if pipeline_id else None
    while True:
        # === NEW: Check pause flag ===
        if pipeline_id:
            pipeline = await db.get_pipeline(pipeline_id)
            if pipeline and getattr(pipeline, "paused", False):
                # Don't dispatch new tasks. Already-running tasks continue.
                await asyncio.sleep(self._settings.scheduler_poll_interval)
                continue

        tasks = await (db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks())
        _print_status_table(tasks)

        all_done = all(
            t.state in (TaskState.DONE.value, TaskState.ERROR.value,
                        TaskState.AWAITING_APPROVAL.value)  # NEW: treat as "parked"
            for t in tasks
        )
        # ... rest of existing logic unchanged ...
```

**Interaction with AWAITING_APPROVAL**:
- `AWAITING_APPROVAL` is NOT treated as terminal for the `all_done` check, but
  tasks in this state are skipped by the scheduler (they don't appear as `TODO`).
- If ALL remaining tasks are `DONE`, `ERROR`, or `AWAITING_APPROVAL`, the loop
  sleeps and polls periodically, waiting for approvals/rejections to create new
  work.
- When a task is approved (merged), or rejected (retried), the execution loop
  picks it up on the next iteration.

**Interaction with pause**:
- Pause prevents NEW task dispatches. Already-running agents complete normally.
- Tasks in `AWAITING_APPROVAL` remain in that state regardless of pause.
- Resume clears the flag; next loop iteration dispatches ready tasks.

### 6.5 Frontend Pause/Resume Button

Added to the pipeline header area, next to the existing cancel button:

```tsx
// In TaskExecutionPageInner, alongside the Cancel button:

{phase === "executing" && (
  <button onClick={handlePause} className="btn btn-warning">
    Pause Pipeline
  </button>
)}

{phase === "paused" && (
  <button onClick={handleResume} className="btn btn-primary btn-glow">
    Resume Pipeline
  </button>
)}
```

The `PipelineProgress` component adds "paused" as a valid step with
an amber/yellow color treatment.

---

## 7. DB Migration Summary

### New Columns

| Table | Column | Type | Default | Purpose |
|-------|--------|------|---------|---------|
| `tasks` | `approval_context` | `TEXT NULL` | `NULL` | JSON blob: worktree_path, agent_model, pipeline_branch |
| `pipelines` | `paused` | `BOOLEAN` | `FALSE` | Pause flag checked by execution loop |
| `pipelines` | `require_approval` | `BOOLEAN` | `FALSE` | Per-pipeline approval setting |

### New TaskState Value

`"awaiting_approval"` added to the `TaskState` enum. No schema migration needed
since the `state` column is a free-form `String` — the enum is enforced in Python only.

### New DB Methods

```python
# On Database class:
async def set_task_approval_context(self, task_id: str, context_json: str) -> None
async def clear_task_approval_context(self, task_id: str) -> None
async def set_pipeline_paused(self, pipeline_id: str, paused: bool) -> None
```

---

## 8. Summary of Files Changed

### Backend (Python)

| File | Changes |
|------|---------|
| `forge/core/models.py` | Add `AWAITING_APPROVAL` to `TaskState` enum |
| `forge/config/settings.py` | Add `require_approval: bool = False` |
| `forge/storage/db.py` | Add `approval_context` to `TaskRow`, `paused` + `require_approval` to `PipelineRow`, new DB methods |
| `forge/core/daemon.py` | Check `paused` flag in `_execution_loop`, treat `AWAITING_APPROVAL` as parked |
| `forge/core/daemon_executor.py` | Insert approval gate in `_attempt_merge` after review passes |
| `forge/api/models/schemas.py` | Add `EditedTaskDefinition`, update `ExecuteRequest`, add `RejectRequest`, add `require_approval` to `CreateTaskRequest` |
| `forge/api/routes/tasks.py` | Add `GET .../diff`, `POST .../approve`, `POST .../reject`, `POST .../pause`, `POST .../resume`; modify execute to accept edited graph |
| `forge/core/validator.py` | No changes (reused as-is for server-side re-validation) |

### Frontend (TypeScript/React)

| File | Changes |
|------|---------|
| `web/src/stores/taskStore.ts` | Add `editedTasks`, `planValidation`, new actions; handle new WS events; add `"paused"` phase + `"awaiting_approval"` state |
| `web/src/app/tasks/view/page.tsx` | Replace `PlanPanel` with `EditablePlanPanel` when planned; add pause/resume buttons; render `ApprovalPanel` for awaiting tasks |
| `web/src/lib/validateTaskGraph.ts` | **New file** — client-side graph validation |
| `web/src/components/task/EditablePlanPanel.tsx` | **New file** — editable plan container |
| `web/src/components/task/EditableTaskCard.tsx` | **New file** — inline-editable task card |
| `web/src/components/task/AddTaskForm.tsx` | **New file** — add-task inline form |
| `web/src/components/task/PlanValidationBanner.tsx` | **New file** — validation error display |
| `web/src/components/task/ApprovalPanel.tsx` | **New file** — diff preview + approve/reject UI |
| `web/src/components/task/PipelineProgress.tsx` | Add "paused" step rendering |
| `web/src/components/task/AgentCard.tsx` | Add "awaiting_approval" badge/state styling |
| `web/src/lib/api.ts` | Add `approveTask()`, `rejectTask()`, `pausePipeline()`, `resumePipeline()`, `getTaskDiff()` helpers |

### New Dependencies

| Package | Purpose |
|---------|---------|
| `@dnd-kit/core` + `@dnd-kit/sortable` | Drag-and-drop task reordering in plan editor |

---

## 9. Edge Cases & Open Questions

1. **Concurrent approval + pause**: If a pipeline is paused and a task is in
   `AWAITING_APPROVAL`, the user can still approve/reject it. The resulting
   merge or retry respects the pause flag (won't dispatch new work until resumed).

2. **Approval timeout**: No automatic timeout for now. A task can sit in
   `AWAITING_APPROVAL` indefinitely. Future: add configurable timeout that
   auto-approves or auto-rejects.

3. **Plan editing after execution starts**: Not supported. The `EditablePlanPanel`
   is only shown when `phase === "planned"`. Once execution begins, the plan
   becomes read-only.

4. **File conflict relaxation**: The current `validate_task_graph` rejects any
   file shared between two tasks. With plan editing, users may intentionally
   assign the same file to dependent tasks. The validator should be updated to
   only flag conflicts between tasks with no transitive dependency (matching the
   client-side logic in section 1.3). This is a separate, backward-compatible
   change.

5. **Worktree lifecycle during approval**: Worktrees are NOT cleaned up when
   entering `AWAITING_APPROVAL` (the agent slot is released, but the worktree
   persists). Cleanup happens after merge or after rejection + retry completes.

6. **Re-execution of edited plan from REST hydration**: If a user refreshes the
   page while in the `planned` phase, the edited tasks should be re-fetched from
   `task_graph_json` in the pipeline DB row. The hydration flow already reads
   this field.
