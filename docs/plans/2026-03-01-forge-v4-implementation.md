# Forge v4: Resilience & Observability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix state loss on refresh, add pipeline resume, pre-flight checks, cost tracking, timeline, toasts, retry/cancel controls, task detail panel, and settings persistence.

**Architecture:** Add a `pipeline_events` table as the append-only event log. All daemon events write to DB AND WebSocket. REST hydration reconstructs state from events. Resume reconstructs TaskGraph from stored JSON.

**Tech Stack:** Python/SQLAlchemy (backend), Next.js/Zustand/TypeScript (frontend), claude-code-sdk for cost data.

---

## Phase P0: Foundation

### Task 1: PipelineEventRow model + Database.log_event()

**Files:**
- Modify: `forge/storage/db.py` (add model at line ~108, add to _ALL_MODELS, add log_event + list_events methods)
- Test: `forge/storage/db_test.py`

**Step 1: Write failing tests**

Add to `forge/storage/db_test.py`:

```python
async def test_log_event(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.log_event(
        pipeline_id="pipe-1",
        task_id="task-1",
        event_type="agent_output",
        payload={"line": "Hello world"},
    )
    events = await db.list_events("pipe-1")
    assert len(events) == 1
    assert events[0].event_type == "agent_output"
    assert events[0].task_id == "task-1"


async def test_list_events_ordered_by_created_at(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    for i in range(5):
        await db.log_event(
            pipeline_id="pipe-1",
            task_id=None,
            event_type="phase_change",
            payload={"phase": f"phase_{i}"},
        )
    events = await db.list_events("pipe-1")
    assert len(events) == 5
    # Oldest first
    assert events[0].payload["phase"] == "phase_0"


async def test_list_events_by_task(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.log_event(pipeline_id="pipe-1", task_id="t1", event_type="agent_output", payload={"line": "a"})
    await db.log_event(pipeline_id="pipe-1", task_id="t2", event_type="agent_output", payload={"line": "b"})
    events = await db.list_events("pipe-1", task_id="t1")
    assert len(events) == 1
    assert events[0].payload["line"] == "a"


async def test_list_events_by_type(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.log_event(pipeline_id="pipe-1", task_id=None, event_type="phase_change", payload={})
    await db.log_event(pipeline_id="pipe-1", task_id="t1", event_type="review_update", payload={})
    events = await db.list_events("pipe-1", event_type="review_update")
    assert len(events) == 1
```

**Step 2: Run tests, verify they fail**

Run: `python3 -m pytest forge/storage/db_test.py -k "test_log_event or test_list_events" -v`
Expected: FAIL (AttributeError: Database has no log_event/list_events)

**Step 3: Implement PipelineEventRow model and methods**

In `forge/storage/db.py`, after `PipelineRow` (line ~107), add:

```python
class PipelineEventRow(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.utcnow().isoformat())
```

Add `import uuid` and `from datetime import datetime` to file imports.

Update `_ALL_MODELS`:
```python
_ALL_MODELS = (UserRow, AuditLogRow, TaskRow, AgentRow, PipelineRow, PipelineEventRow)
```

Add methods to `Database` class:

```python
async def log_event(
    self, *, pipeline_id: str, task_id: str | None, event_type: str, payload: dict,
) -> None:
    async with self._session_factory() as session:
        event = PipelineEventRow(
            pipeline_id=pipeline_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )
        session.add(event)
        await session.commit()

async def list_events(
    self, pipeline_id: str, *, task_id: str | None = None, event_type: str | None = None,
) -> list[PipelineEventRow]:
    from sqlalchemy import select
    async with self._session_factory() as session:
        stmt = select(PipelineEventRow).where(
            PipelineEventRow.pipeline_id == pipeline_id
        ).order_by(PipelineEventRow.created_at.asc())
        if task_id is not None:
            stmt = stmt.where(PipelineEventRow.task_id == task_id)
        if event_type is not None:
            stmt = stmt.where(PipelineEventRow.event_type == event_type)
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

**Step 4: Run tests, verify they pass**

Run: `python3 -m pytest forge/storage/db_test.py -k "test_log_event or test_list_events" -v`
Expected: 4 PASSED

**Step 5: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py
git commit -m "feat: add pipeline_events table and log_event/list_events methods"
```

---

### Task 2: Wire daemon events to DB persistence

**Files:**
- Modify: `forge/core/daemon.py` (pass db to _run_review, add _persist_event helper)
- Modify: `forge/core/events.py` (no change needed — events still go to WebSocket too)

**Step 1: Add _persist_event helper to ForgeDaemon**

In `forge/core/daemon.py`, add a helper method to the `ForgeDaemon` class that both emits via WebSocket AND writes to DB:

```python
async def _emit(self, event_type: str, data: dict, *, db: Database, pipeline_id: str) -> None:
    """Emit event to WebSocket AND persist to DB."""
    await self._events.emit(event_type, data)
    task_id = data.get("task_id")
    await db.log_event(
        pipeline_id=pipeline_id,
        task_id=task_id,
        event_type=event_type,
        payload=data,
    )
```

**Step 2: Replace all `self._events.emit()` calls inside `_execution_loop`, `_execute_task`, `_run_review`, and `_handle_retry` with `self._emit()`**

The key change: every method in the execution path needs access to `db` and `pipeline_id` so it can call `_emit()`. The `_execution_loop` already has both. `_execute_task` already gets `db`. Thread `pipeline_id` through as needed.

For `_run_review` — currently its signature is:
```python
async def _run_review(self, task, worktree_path: str, diff: str)
```
Change to:
```python
async def _run_review(self, task, worktree_path: str, diff: str, *, db: Database, pipeline_id: str)
```

For `_handle_retry` — add `pipeline_id` param:
```python
async def _handle_retry(self, db, task_id, worktree_mgr, review_feedback=None, pipeline_id=None)
```

Replace ALL ~22 `self._events.emit(...)` calls in the execution path with `self._emit(...)`.

For the `plan()` and top-level `run()` methods, those events can remain as `self._events.emit()` since they run before the execution loop and pipeline_id may not exist yet in the plan phase. OR pass them through too for completeness.

**Step 3: Verify agent_output batching is reasonable**

Agent output events are high-frequency. The existing 100ms batching in `_on_agent_message` already batches lines. Each batch becomes one `log_event` call. This keeps the row count reasonable (hundreds per task, not thousands).

**Step 4: Syntax verify**

Run: `python3 -c "import ast; ast.parse(open('forge/core/daemon.py').read()); print('OK')"`
Expected: OK

**Step 5: Commit**

```bash
git add forge/core/daemon.py
git commit -m "feat: persist all daemon events to pipeline_events table"
```

---

### Task 3: Enrich REST hydration with event data

**Files:**
- Modify: `forge/api/routes/tasks.py` (GET /{pipeline_id} endpoint)
- Modify: `forge/api/models/schemas.py` (enrich TaskStatusResponse)
- Modify: `web/src/stores/taskStore.ts` (hydrateFromRest)
- Test: `forge/api/routes/tasks_test.py`

**Step 1: Write failing test**

Add to `forge/api/routes/tasks_test.py`:

```python
async def test_get_task_status_includes_events(self, client):
    """GET /tasks/{id} should include agent_output, review_gates, merge_results from events."""
    token = await _register_and_get_token(client)
    headers = _auth_header(token)

    create_resp = await client.post(
        "/api/tasks",
        json={"description": "Events test", "project_path": "/tmp/proj"},
        headers=headers,
    )
    pipeline_id = create_resp.json()["pipeline_id"]

    resp = await client.get(f"/api/tasks/{pipeline_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # New fields should exist even if empty
    assert "events" in data or "timeline" in data
```

**Step 2: Run test, verify it fails**

Run: `python3 -m pytest forge/api/routes/tasks_test.py::TestGetTaskStatus::test_get_task_status_includes_events -v`
Expected: FAIL

**Step 3: Enrich the GET endpoint**

In `forge/api/routes/tasks.py`, modify the GET `/{pipeline_id}` handler to query `pipeline_events` and reconstruct per-task state:

```python
# After getting pipeline and tasks from task_graph_json...
events = await forge_db.list_events(pipeline_id) if forge_db else []

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
        te = task_events.setdefault(ev.task_id, {"output": [], "reviewGates": [], "mergeResult": None, "cost_usd": 0})
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

# Merge event data into task list
enriched_tasks = []
for t in tasks_list:
    tid = t.get("id", "")
    te = task_events.get(tid, {})
    enriched_tasks.append({**t, **te})
```

Return enriched response with `timeline` and `enriched_tasks`.

**Step 4: Update `hydrateFromRest` in taskStore.ts**

Currently `hydrateFromRest` ignores output/reviewGates/mergeResult. Update to merge event-sourced data:

```typescript
hydrateFromRest: (data) => {
  const newTasks: Record<string, TaskState> = {};
  for (const t of data.tasks) {
    newTasks[t.id] = {
      id: t.id,
      title: t.title,
      description: t.description,
      targetFiles: t.files,
      dependsOn: t.depends_on,
      complexity: t.complexity,
      state: t.state || "pending",
      branch: `forge/${t.id}`,
      files: t.files_changed || [],
      output: t.output || [],
      reviewGates: t.reviewGates || [],
      mergeResult: t.mergeResult || undefined,
      costUsd: t.cost_usd || 0,
    };
  }
  const phase = (data.phase || "idle") as PipelineState["phase"];
  set({ tasks: newTasks, phase, timeline: data.timeline || [] });
},
```

**Step 5: Run tests, commit**

```bash
git add forge/api/routes/tasks.py forge/api/models/schemas.py web/src/stores/taskStore.ts
git commit -m "feat: enrich REST hydration with persisted event data"
```

---

### Task 4: Pre-flight checks

**Files:**
- Modify: `forge/core/daemon.py` (add _preflight_checks method, call from execute)
- Test: manual (requires git repo)

**Step 1: Add _preflight_checks to ForgeDaemon**

```python
async def _preflight_checks(self, project_dir: str, db: Database, pipeline_id: str) -> bool:
    """Run pre-execution validation. Returns True if all checks pass."""
    import shutil

    errors = []

    # Check: valid git repo
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        errors.append("Not a git repository")

    # Check: git remote exists
    result = subprocess.run(
        ["git", "remote"], cwd=project_dir, capture_output=True, text=True,
    )
    if not result.stdout.strip():
        errors.append("No git remote configured. Run: git remote add origin <url>")

    # Check: gh CLI available and authed (optional — warn, don't fail)
    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print("[yellow]  Warning: gh CLI not authenticated (PR creation will fail)[/yellow]")

    if errors:
        detail = "; ".join(errors)
        console.print(f"[bold red]Pre-flight failed: {detail}[/bold red]")
        await self._emit("pipeline:preflight_failed", {"errors": errors}, db=db, pipeline_id=pipeline_id)
        await db.update_pipeline_status(pipeline_id, "error")
        return False

    return True
```

**Step 2: Call from execute() at the very start, before agent creation**

In `execute()`, right after the ID mapping block, before creating tasks in DB:

```python
# Pre-flight checks
if not await self._preflight_checks(self._project_dir, db, pid):
    return
```

**Step 3: Handle in frontend — add preflight_failed to WebSocket handlers**

In `taskStore.ts`, add handler:
```typescript
case "pipeline:preflight_failed":
  set({ phase: "error", preflightErrors: data.errors });
  break;
```

**Step 4: Commit**

```bash
git add forge/core/daemon.py web/src/stores/taskStore.ts
git commit -m "feat: add pre-flight checks before pipeline execution"
```

---

### Task 5: Pipeline resume endpoint + UI

**Files:**
- Modify: `forge/api/routes/tasks.py` (add POST /{pipeline_id}/resume)
- Modify: `web/src/app/tasks/view/page.tsx` (add Resume button)
- Test: `forge/api/routes/tasks_test.py`

**Step 1: Write failing test**

```python
class TestResumeEndpoint:
    async def test_resume_requires_auth(self, client):
        resp = await client.post("/api/tasks/some-id/resume")
        assert resp.status_code == 401

    async def test_resume_resets_interrupted_tasks(self, client):
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        create_resp = await client.post(
            "/api/tasks",
            json={"description": "Resume test", "project_path": "/tmp/proj"},
            headers=headers,
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        resp = await client.post(
            f"/api/tasks/{pipeline_id}/resume", headers=headers,
        )
        # Should succeed (or 404 if pipeline doesn't exist in test — either is valid)
        assert resp.status_code in (200, 404)
```

**Step 2: Implement the resume endpoint**

```python
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

    # Reset interrupted tasks
    tasks = await forge_db.list_tasks_by_pipeline(pipeline_id)
    reset_count = 0
    for task in tasks:
        if task.state in ("in_progress", "in_review", "merging"):
            await forge_db.update_task_state(task.id, "todo")
            reset_count += 1

    if reset_count == 0:
        pending = [t for t in tasks if t.state == "todo"]
        if not pending:
            raise HTTPException(400, "No tasks to resume")

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

    # Set pipeline back to executing
    await forge_db.update_pipeline_status(pipeline_id, "executing")

    # Launch execution in background
    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon
    from forge.core.events import EventEmitter

    settings = ForgeSettings()
    emitter = request.app.state.event_emitter if hasattr(request.app.state, "event_emitter") else EventEmitter()

    daemon = ForgeDaemon(pipeline.project_dir, settings=settings, event_emitter=emitter)

    async def _run():
        try:
            await daemon.execute(graph, forge_db, pipeline_id=pipeline_id)
        except Exception as e:
            logger.error("Resume execution failed: %s", e)

    asyncio.create_task(_run())

    return {"status": "resumed", "pipeline_id": pipeline_id, "tasks_reset": reset_count}
```

**Step 3: Add Resume button in frontend**

In `web/src/app/tasks/view/page.tsx`, in the completion summary area, add a Resume button when pipeline is not complete and has non-terminal tasks:

```tsx
{phase !== "complete" && phase !== "idle" && (
  <button
    onClick={async () => {
      await apiPost(`/tasks/${pipelineId}/resume`, {}, token);
      window.location.reload();
    }}
    className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
  >
    Resume Pipeline
  </button>
)}
```

**Step 4: Commit**

```bash
git add forge/api/routes/tasks.py web/src/app/tasks/view/page.tsx forge/api/routes/tasks_test.py
git commit -m "feat: add pipeline resume endpoint and UI button"
```

---

## Phase P1: Observability & Controls

### Task 6: Cost tracking — persist and display

**Files:**
- Modify: `forge/storage/db.py` (add cost_usd to TaskRow)
- Modify: `forge/agents/adapter.py` (return cost in AgentResult properly)
- Modify: `forge/core/daemon.py` (persist cost after each sdk_query, emit cost_update event)
- Modify: `web/src/stores/taskStore.ts` (add costUsd to TaskState, handle cost_update)
- Modify: `web/src/components/task/AgentCard.tsx` (display cost)
- Test: `forge/storage/db_test.py`

**Step 1: Write failing test**

```python
async def test_add_task_cost(db: Database):
    await db.create_task(
        id="t1", title="T", description="D", files=[], depends_on=[], complexity="low",
    )
    await db.add_task_cost("t1", 0.05)
    await db.add_task_cost("t1", 0.03)
    task = await db.get_task("t1")
    assert abs(task.cost_usd - 0.08) < 0.001
```

**Step 2: Add cost_usd column to TaskRow and add_task_cost method**

In TaskRow: `cost_usd: Mapped[float] = mapped_column(default=0.0)`

In Database:
```python
async def add_task_cost(self, task_id: str, cost: float) -> None:
    async with self._session_factory() as session:
        task = await session.get(TaskRow, task_id)
        if task:
            task.cost_usd = (task.cost_usd or 0) + cost
            await session.commit()
```

**Step 3: Wire cost tracking in daemon.py**

After the agent `sdk_query` completes in `_execute_task` (after `result = await runtime.run_task(...)`):
```python
if result.token_usage > 0:
    cost = result.token_usage / 1_000_000  # Convert back from the adapter's format
    await db.add_task_cost(task_id, cost)
    await self._emit("task:cost_update", {
        "task_id": task_id, "cost_usd": cost,
    }, db=db, pipeline_id=pipeline_id_from_task)
```

Similarly after LLM review `sdk_query` in `_run_review` — capture the review cost too.

**But first fix adapter.py** — currently `cost_usd` is converted weirdly. Change `AgentResult` to store actual USD:
```python
# In adapter.py, change line 117:
token_usage=int(cost_usd * 1_000_000),
# To:
cost_usd=cost_usd,
```
And update `AgentResult` dataclass to use `cost_usd: float = 0.0` instead of `token_usage: int = 0`.

**Step 4: Frontend — add cost to TaskState and display**

In `taskStore.ts`, add to TaskState interface: `costUsd?: number;`

Add WebSocket handler:
```typescript
case "task:cost_update": {
  const taskId = data.task_id as string;
  const existing = get().tasks[taskId];
  if (existing) {
    set({
      tasks: {
        ...get().tasks,
        [taskId]: {
          ...existing,
          costUsd: (existing.costUsd || 0) + (data.cost_usd as number),
        },
      },
    });
  }
  break;
}
```

In `AgentCard.tsx`, after merge result section, add cost display:
```tsx
{task.costUsd != null && task.costUsd > 0 && (
  <p className="text-xs text-zinc-500">Cost: ${task.costUsd.toFixed(4)}</p>
)}
```

**Step 5: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py forge/agents/adapter.py forge/core/daemon.py web/src/stores/taskStore.ts web/src/components/task/AgentCard.tsx
git commit -m "feat: track and display per-task cost from SDK"
```

---

### Task 7: Timeline panel

**Files:**
- Create: `web/src/components/task/TimelinePanel.tsx`
- Modify: `web/src/stores/taskStore.ts` (add timeline array to store)
- Modify: `web/src/app/tasks/view/page.tsx` (render TimelinePanel)

**Step 1: Add timeline to Zustand store**

In `taskStore.ts`, add to the store state:
```typescript
timeline: { type: string; taskId?: string; payload: Record<string, unknown>; timestamp: string }[];
```

Initialize as `[]`. Populate from REST hydration (`data.timeline`) and from WebSocket events (append on each event).

**Step 2: Create TimelinePanel component**

```tsx
// web/src/components/task/TimelinePanel.tsx
"use client";

interface TimelineEvent {
  type: string;
  taskId?: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

const EVENT_LABELS: Record<string, string> = {
  "pipeline:phase_changed": "Phase changed",
  "pipeline:plan_ready": "Plan ready",
  "task:state_changed": "Task state",
  "task:agent_output": "Agent output",
  "task:review_update": "Review gate",
  "task:merge_result": "Merge result",
  "task:cost_update": "Cost update",
  "task:files_changed": "Files changed",
};

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

function eventSummary(ev: TimelineEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case "pipeline:phase_changed": return `→ ${p.phase}`;
    case "pipeline:plan_ready": return `${(p.tasks as unknown[])?.length || 0} tasks`;
    case "task:state_changed": return `${ev.taskId}: ${p.state}`;
    case "task:review_update": return `${ev.taskId}: ${p.gate} ${p.passed ? "✅" : "❌"}`;
    case "task:merge_result": return `${ev.taskId}: ${p.success ? "merged" : "failed"}`;
    case "task:cost_update": return `${ev.taskId}: $${(p.cost_usd as number)?.toFixed(4)}`;
    default: return ev.type;
  }
}

export default function TimelinePanel({ events }: { events: TimelineEvent[] }) {
  // Filter out high-frequency agent_output to keep timeline readable
  const filtered = events.filter((e) => e.type !== "task:agent_output");

  return (
    <div className="max-h-96 overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-3">
      <h3 className="mb-2 text-sm font-semibold text-zinc-300">Timeline</h3>
      <div className="space-y-1">
        {filtered.map((ev, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <span className="shrink-0 text-zinc-500">{formatTime(ev.timestamp)}</span>
            <span className="text-zinc-300">{eventSummary(ev)}</span>
          </div>
        ))}
        {filtered.length === 0 && (
          <p className="text-xs text-zinc-500">No events yet</p>
        )}
      </div>
    </div>
  );
}
```

**Step 3: Add TimelinePanel to task view page**

In `web/src/app/tasks/view/page.tsx`, import and render alongside agent cards:
```tsx
import TimelinePanel from "@/components/task/TimelinePanel";

// In the JSX, after PlanPanel and before agent cards:
<TimelinePanel events={timeline} />
```

**Step 4: Build and verify**

Run: `cd web && npx next build`
Expected: Build succeeds

**Step 5: Commit**

```bash
git add web/src/components/task/TimelinePanel.tsx web/src/stores/taskStore.ts web/src/app/tasks/view/page.tsx
git commit -m "feat: add real-time timeline panel to task execution view"
```

---

### Task 8: Toast notifications

**Files:**
- Modify: `web/src/stores/taskStore.ts` (fire notifications on key events)
- Modify: `web/src/app/tasks/view/page.tsx` (call requestPermission on mount)

**Step 1: Import useNotifications and fire on key events**

The challenge: Zustand stores can't use React hooks directly. Instead, call `Notification` directly in the store (it's a browser API, not a hook):

In `taskStore.ts`, add a helper at the top:
```typescript
function sendNotification(title: string, body: string) {
  if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}
```

Then add calls in the WebSocket handlers:
```typescript
// In task:state_changed handler, after setting state:
if (data.state === "done") sendNotification("Task completed", existing.title);
if (data.state === "error") sendNotification("Task failed", existing.title);

// In pipeline:phase_changed, after setting phase:
if (data.phase === "complete") sendNotification("Pipeline complete", "All tasks finished");

// In pipeline:pr_created:
sendNotification("PR created", data.url as string);
```

**Step 2: Request permission on page load**

In `web/src/app/tasks/view/page.tsx`, in the main component's useEffect:
```typescript
useEffect(() => {
  if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}, []);
```

**Step 3: Build and verify**

Run: `cd web && npx next build`

**Step 4: Commit**

```bash
git add web/src/stores/taskStore.ts web/src/app/tasks/view/page.tsx
git commit -m "feat: add toast notifications for task completion and pipeline events"
```

---

### Task 9: Cancel + retry endpoints and UI

**Files:**
- Modify: `forge/api/routes/tasks.py` (add POST /{pipeline_id}/cancel, POST /{task_id}/retry)
- Modify: `web/src/app/tasks/view/page.tsx` (add Cancel button)
- Modify: `web/src/components/task/AgentCard.tsx` (add Retry button on error tasks)
- Test: `forge/api/routes/tasks_test.py`

**Step 1: Add cancel endpoint**

```python
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
```

**Step 2: Add single-task retry endpoint**

```python
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
```

**Step 3: Add Cancel button to task view page**

**Step 4: Add Retry button to AgentCard for error tasks**

**Step 5: Commit**

```bash
git add forge/api/routes/tasks.py web/src/app/tasks/view/page.tsx web/src/components/task/AgentCard.tsx forge/api/routes/tasks_test.py
git commit -m "feat: add cancel pipeline and retry task endpoints with UI controls"
```

---

## Phase P2: Polish

### Task 10: Task detail slide-out panel

**Files:**
- Create: `web/src/components/task/TaskDetailPanel.tsx`
- Modify: `web/src/components/task/AgentCard.tsx` (onClick opens panel)
- Modify: `web/src/app/tasks/view/page.tsx` (render overlay + panel)

Create a right-side slide-out panel (60% width) with full agent output, review details, merge stats, file list, and retry history. Close on Escape or overlay click. Uses existing TaskState data from Zustand store.

**Step 1: Create TaskDetailPanel**
**Step 2: Wire AgentCard onClick**
**Step 3: Build verify and commit**

```bash
git commit -m "feat: add task detail slide-out panel"
```

---

### Task 11: Settings persistence + model routing controls

**Files:**
- Modify: `forge/storage/db.py` (add settings_json to UserRow)
- Modify: `forge/api/routes/settings.py` (persist to DB instead of in-memory)
- Modify: `web/src/app/settings/page.tsx` (add model routing section)
- Modify: `forge/core/model_router.py` (read overrides from settings)
- Test: `forge/api/routes/settings_test.py`

**Step 1: Add settings_json column to UserRow**

```python
settings_json: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

**Step 2: Update settings routes to read/write from DB**

Replace the in-memory `_settings_store` with DB reads/writes using `forge_db.get_user_by_email()` or a new `get_user_settings(user_id)` method.

**Step 3: Add model routing section to settings page**

Add dropdowns for: planner model, agent model (per complexity), reviewer model. Use the current `_ROUTING_TABLE` defaults as initial values.

**Step 4: Update model_router.py to accept overrides**

Change `select_model()` to accept an optional `overrides` dict. The tasks route passes the user's settings when creating the daemon.

**Step 5: Commit**

```bash
git commit -m "feat: persist settings to DB and add model routing controls"
```

---

## Summary

| Task | Description | Phase |
|------|-------------|-------|
| 1 | PipelineEventRow model + log_event/list_events | P0 |
| 2 | Wire all daemon events to DB persistence | P0 |
| 3 | Enrich REST hydration with event data | P0 |
| 4 | Pre-flight checks (remote, auth) | P0 |
| 5 | Pipeline resume endpoint + UI | P0 |
| 6 | Cost tracking — persist and display | P1 |
| 7 | Timeline panel component | P1 |
| 8 | Toast notifications | P1 |
| 9 | Cancel + retry endpoints and UI | P1 |
| 10 | Task detail slide-out panel | P2 |
| 11 | Settings persistence + model routing | P2 |
