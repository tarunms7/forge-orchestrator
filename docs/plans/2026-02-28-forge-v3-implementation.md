# Forge v3: Production Readiness Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 7 production-readiness issues: persistent DB, auth persistence, live streaming logs, history/dashboard from real data, smart merge retry, and Create PR button.

**Architecture:** Backend changes in Python (FastAPI + SQLAlchemy async + claude-code-sdk), frontend changes in TypeScript (Next.js + Zustand). All sections are independent except Section 4 (History/Dashboard) which depends on Section 1 (Persistent DB). TDD with `pytest-asyncio` for backend, manual verification for frontend.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 async, aiosqlite, pytest-asyncio, httpx, Next.js 14, Zustand 5, TypeScript, Tailwind v4.

---

### Task 1: Persistent Database — Add `pipeline_id` to TaskRow

**Files:**
- Modify: `forge/storage/db.py`
- Test: `forge/storage/db_test.py`

**Step 1: Write the failing test**

Add to `forge/storage/db_test.py`:

```python
async def test_create_task_with_pipeline_id(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test pipeline",
        project_dir="/tmp", model_strategy="auto",
    )
    await db.create_task(
        id="task-1", title="Test task", description="A test",
        files=["a.py"], depends_on=[], complexity="low",
        pipeline_id="pipe-1",
    )
    task = await db.get_task("task-1")
    assert task is not None
    assert task.pipeline_id == "pipe-1"


async def test_list_tasks_by_pipeline(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="P1", project_dir="/tmp", model_strategy="auto",
    )
    await db.create_pipeline(
        id="pipe-2", description="P2", project_dir="/tmp", model_strategy="auto",
    )
    await db.create_task(
        id="t1", title="T1", description="D", files=["a.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-1",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=["b.py"],
        depends_on=[], complexity="low", pipeline_id="pipe-2",
    )
    tasks = await db.list_tasks_by_pipeline("pipe-1")
    assert len(tasks) == 1
    assert tasks[0].id == "t1"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest forge/storage/db_test.py -v -k "pipeline"`
Expected: FAIL — `create_task()` doesn't accept `pipeline_id`, `list_tasks_by_pipeline` doesn't exist.

**Step 3: Implement — Add `pipeline_id` to TaskRow and update DB methods**

In `forge/storage/db.py`:

1. Add `pipeline_id` column to `TaskRow`:
```python
pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

2. Update `create_task()` to accept optional `pipeline_id`:
```python
async def create_task(
    self, id: str, title: str, description: str, files: list[str],
    depends_on: list[str], complexity: str, pipeline_id: str | None = None,
) -> None:
    async with self._session_factory() as session:
        row = TaskRow(
            id=id, title=title, description=description,
            files=files, depends_on=depends_on, complexity=complexity,
            pipeline_id=pipeline_id,
        )
        session.add(row)
        await session.commit()
```

3. Add `list_tasks_by_pipeline()`:
```python
async def list_tasks_by_pipeline(self, pipeline_id: str) -> list[TaskRow]:
    async with self._session_factory() as session:
        stmt = select(TaskRow).where(TaskRow.pipeline_id == pipeline_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest forge/storage/db_test.py -v`
Expected: ALL PASS (existing tests still pass since `pipeline_id` is optional).

**Step 5: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py
git commit -m "feat: add pipeline_id to TaskRow with list_tasks_by_pipeline query"
```

---

### Task 2: Persistent Database — Remove DB deletion + Add `pr_url` to PipelineRow

**Files:**
- Modify: `forge/core/daemon.py`
- Modify: `forge/storage/db.py`
- Test: `forge/storage/db_test.py`

**Step 1: Write the failing test for `pr_url`**

Add to `forge/storage/db_test.py`:

```python
async def test_set_pipeline_pr_url(db: Database):
    await db.create_pipeline(
        id="pipe-1", description="Test", project_dir="/tmp", model_strategy="auto",
    )
    await db.set_pipeline_pr_url("pipe-1", "https://github.com/user/repo/pull/42")
    pipeline = await db.get_pipeline("pipe-1")
    assert pipeline.pr_url == "https://github.com/user/repo/pull/42"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest forge/storage/db_test.py::test_set_pipeline_pr_url -v`
Expected: FAIL — `pr_url` column and `set_pipeline_pr_url` don't exist.

**Step 3: Implement**

In `forge/storage/db.py`:

1. Add `pr_url` column to `PipelineRow`:
```python
pr_url: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

2. Add `set_pipeline_pr_url()` method:
```python
async def set_pipeline_pr_url(self, pipeline_id: str, pr_url: str) -> None:
    async with self._session_factory() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.id == pipeline_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.pr_url = pr_url
            await session.commit()
```

In `forge/core/daemon.py`:

3. Remove the DB deletion block from `run()`. Change lines 117-119 from:
```python
# Fresh DB per run to avoid stale state from previous runs
if os.path.exists(db_path):
    os.remove(db_path)
```
To:
```python
# DB is persistent — each run creates a new pipeline with a unique ID.
# No deletion needed.
```

4. Update `execute()` to pass `pipeline_id` when creating tasks. This requires the daemon to know the pipeline_id. Add a `_pipeline_id` attribute:

In `plan()`, store the pipeline_id on self after creating the pipeline.
In `execute()`, pass `pipeline_id=self._pipeline_id` to each `db.create_task()` call.

For CLI flow (`run()` method), generate a pipeline_id and call `db.create_pipeline()`:
```python
async def run(self, user_input: str) -> None:
    """Full pipeline for CLI: plan + execute. Maintains backward compat."""
    import uuid
    db_path = os.path.join(self._project_dir, ".forge", "forge.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{db_path}"

    # DB is persistent — each run creates a new pipeline with a unique ID.
    db = Database(db_url)
    await db.initialize()

    self._pipeline_id = str(uuid.uuid4())
    await db.create_pipeline(
        id=self._pipeline_id,
        description=user_input[:200],
        project_dir=self._project_dir,
        model_strategy=self._strategy,
    )

    try:
        graph = await self.plan(user_input, db)
        await self.execute(graph, db)
    finally:
        await db.close()
```

In `execute()`, change the `create_task` call:
```python
for task_def in graph.tasks:
    await db.create_task(
        id=task_def.id,
        title=task_def.title,
        description=task_def.description,
        files=task_def.files,
        depends_on=task_def.depends_on,
        complexity=task_def.complexity.value,
        pipeline_id=getattr(self, '_pipeline_id', None),
    )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest forge/storage/db_test.py forge/core/ -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py forge/core/daemon.py
git commit -m "feat: persistent DB — remove deletion, add pr_url column, pass pipeline_id to tasks"
```

---

### Task 3: Auth Persistence — Fix Cookie Path

**Files:**
- Modify: `forge/api/routes/auth.py`
- Test: `forge/api/routes/auth_test.py`

**Step 1: Write the failing test**

Add to `forge/api/routes/auth_test.py`:

```python
async def test_refresh_cookie_path_is_root(client):
    """Refresh token cookie path must be '/' so it's sent to /api/auth/refresh."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "cookie-path@example.com",
            "password": "securepass123",
            "display_name": "Cookie Test",
        },
    )
    assert resp.status_code == 201
    # Check the Set-Cookie header for path
    set_cookie = resp.headers.get("set-cookie", "")
    assert "path=/" in set_cookie.lower().replace(" ", "")
    # Ensure it's not path=/auth
    assert "path=/auth" not in set_cookie.lower().replace(" ", "")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest forge/api/routes/auth_test.py::test_refresh_cookie_path_is_root -v`
Expected: FAIL — cookie currently has `path="/auth"`.

**Step 3: Implement — Fix the cookie path**

In `forge/api/routes/auth.py`, change `_set_refresh_cookie()`:

```python
def _set_refresh_cookie(response: JSONResponse, refresh_token: str) -> None:
    """Set the refresh token as an httpOnly cookie on the response."""
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/",  # Must be "/" so cookie is sent to /api/auth/refresh
    )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest forge/api/routes/auth_test.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add forge/api/routes/auth.py forge/api/routes/auth_test.py
git commit -m "fix: set refresh token cookie path to '/' for proper refresh flow"
```

---

### Task 4: Auth Persistence — Zustand `persist` Middleware

**Files:**
- Modify: `web/src/stores/authStore.ts`

**Step 1: Update authStore with persist middleware**

Replace the contents of `web/src/stores/authStore.ts`:

```typescript
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token: string | null;
  userId: string | null;
  setAuth: (token: string, userId: string) => void;
  logout: () => void;
  refreshToken: () => Promise<boolean>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      userId: null,
      setAuth: (token, userId) => set({ token, userId }),
      logout: () => {
        set({ token: null, userId: null });
        // Clear refresh token cookie by calling logout endpoint
        fetch(`${process.env.NEXT_PUBLIC_API_URL || "/api"}/auth/logout`, {
          method: "POST",
          credentials: "include",
        }).catch(() => {});
      },
      refreshToken: async () => {
        try {
          const res = await fetch(
            `${process.env.NEXT_PUBLIC_API_URL || "/api"}/auth/refresh`,
            { method: "POST", credentials: "include" }
          );
          if (!res.ok) return false;
          const data = await res.json();
          const current = get();
          set({ token: data.access_token, userId: current.userId });
          return true;
        } catch {
          return false;
        }
      },
    }),
    {
      name: "forge-auth",
      partialize: (state) => ({ token: state.token, userId: state.userId }),
    }
  )
);
```

**Step 2: Rebuild frontend**

Run: `cd web && npm run build`
Expected: Build succeeds with no errors.

**Step 3: Verify manually**

1. Start server: `forge serve`
2. Login in browser
3. Refresh page → should remain logged in
4. Check browser localStorage → should see `forge-auth` key

**Step 4: Commit**

```bash
git add web/src/stores/authStore.ts
git commit -m "feat: persist auth state in localStorage to survive page refreshes"
```

---

### Task 5: Live Streaming Logs — Wire `on_message` Through Adapter and Runtime

**Files:**
- Modify: `forge/agents/adapter.py`
- Modify: `forge/agents/runtime.py`
- Test: `forge/agents/adapter_test.py`
- Test: `forge/agents/runtime_test.py`

**Step 1: Write the failing test for adapter**

Add to `forge/agents/adapter_test.py`:

```python
from unittest.mock import AsyncMock, patch

async def test_claude_adapter_passes_on_message_to_sdk_query():
    """ClaudeAdapter.run() should forward on_message callback to sdk_query."""
    callback = AsyncMock()

    mock_result = AsyncMock()
    mock_result.result = "Done"
    mock_result.total_cost_usd = 0.01
    mock_result.is_error = False

    with patch("forge.agents.adapter.sdk_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_result
        with patch("forge.agents.adapter._get_changed_files", return_value=["a.py"]):
            adapter = ClaudeAdapter()
            result = await adapter.run(
                task_prompt="test",
                worktree_path="/tmp/test",
                allowed_files=["a.py"],
                timeout_seconds=60,
                on_message=callback,
            )

    # Verify on_message was passed through to sdk_query
    mock_query.assert_called_once()
    call_kwargs = mock_query.call_args[1]
    assert call_kwargs["on_message"] is callback
    assert result.success is True
```

**Step 2: Write the failing test for runtime**

Add to `forge/agents/runtime_test.py`:

```python
from unittest.mock import AsyncMock

async def test_runtime_passes_on_message_to_adapter():
    """AgentRuntime.run_task() should forward on_message to adapter.run()."""
    mock_adapter = AsyncMock()
    mock_adapter.run.return_value = AgentResult(
        success=True, files_changed=["a.py"], summary="Done",
    )
    callback = AsyncMock()

    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="test",
        worktree_path="/tmp/test",
        allowed_files=["a.py"],
        on_message=callback,
    )

    mock_adapter.run.assert_called_once()
    call_kwargs = mock_adapter.run.call_args[1]
    assert call_kwargs["on_message"] is callback
    assert result.success is True
```

**Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest forge/agents/adapter_test.py::test_claude_adapter_passes_on_message_to_sdk_query forge/agents/runtime_test.py::test_runtime_passes_on_message_to_adapter -v`
Expected: FAIL — neither `run()` nor `run_task()` accept `on_message`.

**Step 4: Implement — Add `on_message` parameter**

In `forge/agents/adapter.py`:

1. Update `AgentAdapter.run()` abstract method:
```python
@abstractmethod
async def run(
    self,
    task_prompt: str,
    worktree_path: str,
    allowed_files: list[str],
    timeout_seconds: int,
    allowed_dirs: list[str] | None = None,
    model: str = "sonnet",
    on_message: Callable | None = None,
) -> AgentResult:
    """Execute a task and return the result."""
```

Add `from collections.abc import Callable` to imports.

2. Update `ClaudeAdapter.run()`:
```python
async def run(
    self,
    task_prompt: str,
    worktree_path: str,
    allowed_files: list[str],
    timeout_seconds: int,
    allowed_dirs: list[str] | None = None,
    model: str = "sonnet",
    on_message: Callable | None = None,
) -> AgentResult:
    options = self._build_options(worktree_path, allowed_dirs or [], model=model)

    result = await sdk_query(prompt=task_prompt, options=options, on_message=on_message)
    files_changed = _get_changed_files(worktree_path)
    # ... rest unchanged
```

In `forge/agents/runtime.py`:

3. Update `AgentRuntime.run_task()`:
```python
async def run_task(
    self,
    agent_id: str,
    task_prompt: str,
    worktree_path: str,
    allowed_files: list[str],
    allowed_dirs: list[str] | None = None,
    model: str = "sonnet",
    on_message=None,
) -> AgentResult:
    try:
        return await self._adapter.run(
            task_prompt=task_prompt,
            worktree_path=worktree_path,
            allowed_files=allowed_files,
            timeout_seconds=self._timeout,
            allowed_dirs=allowed_dirs,
            model=model,
            on_message=on_message,
        )
    except TimeoutError:
        # ... unchanged
    except Exception as e:
        # ... unchanged
```

**Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest forge/agents/ -v`
Expected: ALL PASS.

**Step 6: Commit**

```bash
git add forge/agents/adapter.py forge/agents/runtime.py forge/agents/adapter_test.py forge/agents/runtime_test.py
git commit -m "feat: add on_message callback to adapter and runtime for live streaming"
```

---

### Task 6: Live Streaming Logs — Wire Callback in Daemon

**Files:**
- Modify: `forge/core/daemon.py`

**Step 1: Implement the streaming callback in `_execute_task()`**

In `forge/core/daemon.py`, update `_execute_task()` to create and pass an `on_message` callback:

1. Add a helper method to extract text from SDK messages:
```python
def _extract_text(message) -> str | None:
    """Extract human-readable text from a claude-code-sdk message."""
    from claude_code_sdk import AssistantMessage, ResultMessage
    if isinstance(message, AssistantMessage):
        parts = []
        for block in (message.content or []):
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else None
    if isinstance(message, ResultMessage):
        return message.result if message.result else None
    return None
```

2. In `_execute_task()`, before the `runtime.run_task()` call, create the callback:
```python
# Create streaming callback for live logs
import time
_last_flush = [time.monotonic()]
_batch = []

async def _on_agent_message(msg):
    text = _extract_text(msg)
    if not text:
        return
    _batch.append(text)
    now = time.monotonic()
    # Batch: flush every 100ms to prevent WebSocket flooding
    if now - _last_flush[0] >= 0.1:
        for line in _batch:
            await self._events.emit("task:agent_output", {
                "task_id": task_id, "line": line,
            })
        _batch.clear()
        _last_flush[0] = now

# ... then pass it:
result = await runtime.run_task(
    agent_id, prompt, worktree_path, task.files,
    allowed_dirs=self._settings.allowed_dirs,
    model=agent_model,
    on_message=_on_agent_message,
)

# Flush any remaining batched messages
for line in _batch:
    await self._events.emit("task:agent_output", {
        "task_id": task_id, "line": line,
    })
_batch.clear()
```

3. Also emit `task:files_changed` after agent completes (already exists via `result.files_changed`):
```python
if result.files_changed:
    await self._events.emit("task:files_changed", {
        "task_id": task_id, "files": result.files_changed,
    })
```

**Step 2: Run existing tests**

Run: `.venv/bin/pytest forge/core/ -v`
Expected: ALL PASS (no daemon integration tests break since they mock the runtime).

**Step 3: Commit**

```bash
git add forge/core/daemon.py
git commit -m "feat: wire on_message callback in daemon for live streaming agent output"
```

---

### Task 7: History Routes — Wire to `forge_db`

**Files:**
- Modify: `forge/api/routes/history.py`
- Test: `forge/api/routes/history_test.py`

**Step 1: Update the existing history tests to work with `forge_db`**

The existing tests in `history_test.py` already test the right endpoints. They create pipelines via `POST /api/tasks` which stores in `forge_db`. But the history endpoint reads from the in-memory dict. After our change, the tests should pass as-is since `POST /api/tasks` already writes to `forge_db`.

Update the test fixture to pass `forge_db_url`:

```python
@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-history",
        forge_db_url="sqlite+aiosqlite:///:memory:",
    )

    async with app.state.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.async_engine.dispose()
```

But we also need to initialize the forge_db tables. The app lifespan handles this for real server, but ASGITransport doesn't trigger lifespan. Add manual init:

```python
@pytest.fixture
async def client():
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-history",
        forge_db_url="sqlite+aiosqlite:///:memory:",
    )

    # Init auth DB tables
    async with app.state.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Init forge DB tables
    if app.state.forge_db is not None:
        await app.state.forge_db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    if app.state.forge_db is not None:
        await app.state.forge_db.close()
    await app.state.async_engine.dispose()
```

**Step 2: Rewrite history routes to query `forge_db`**

Replace `forge/api/routes/history.py`:

```python
"""History endpoints: list and detail for past pipeline runs."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.routes.tasks import get_current_user

router = APIRouter(prefix="/history", tags=["history"])


def _get_forge_db(request: Request):
    return getattr(request.app.state, "forge_db", None)


@router.get("")
async def list_history(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    """Return list of past pipeline runs for the authenticated user."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        return []

    pipelines = await forge_db.list_pipelines(user_id=user_id)
    results = []
    for p in pipelines:
        duration = None
        if p.created_at and p.completed_at:
            from datetime import datetime
            try:
                start = datetime.fromisoformat(p.created_at)
                end = datetime.fromisoformat(p.completed_at)
                duration = int((end - start).total_seconds())
            except (ValueError, TypeError):
                pass

        # Count tasks belonging to this pipeline
        tasks = await forge_db.list_tasks_by_pipeline(p.id)

        results.append({
            "pipeline_id": p.id,
            "description": p.description,
            "phase": p.status,
            "created_at": p.created_at or "",
            "duration": duration,
            "task_count": len(tasks),
        })
    return results


@router.get("/{pipeline_id}")
async def get_history_detail(
    pipeline_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Return full detail for a past pipeline run."""
    forge_db = _get_forge_db(request)
    if forge_db is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline = await forge_db.get_pipeline(pipeline_id)
    if pipeline is None or pipeline.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Parse tasks from the task_graph_json
    tasks_data = []
    if pipeline.task_graph_json:
        try:
            tasks_data = json.loads(pipeline.task_graph_json).get("tasks", [])
        except (json.JSONDecodeError, AttributeError):
            pass

    return {
        "pipeline_id": pipeline.id,
        "description": pipeline.description,
        "project_path": pipeline.project_dir,
        "phase": pipeline.status,
        "tasks": tasks_data,
        "created_at": pipeline.created_at or "",
        "duration": None,
        "pr_url": pipeline.pr_url if hasattr(pipeline, "pr_url") else None,
    }
```

**Step 3: Run tests to verify they pass**

Run: `.venv/bin/pytest forge/api/routes/history_test.py -v`
Expected: ALL PASS.

**Step 4: Commit**

```bash
git add forge/api/routes/history.py forge/api/routes/history_test.py
git commit -m "feat: wire history routes to forge_db instead of in-memory dict"
```

---

### Task 8: Dashboard Stats — New API Endpoint + Frontend

**Files:**
- Modify: `forge/api/routes/tasks.py`
- Modify: `forge/api/routes/tasks_test.py` (if tests exist for stats)
- Modify: `web/src/app/page.tsx`

**Step 1: Write the failing test**

Add to `forge/api/routes/tasks_test.py` (or create inline):

```python
async def test_stats_returns_pipeline_counts(client):
    """GET /api/stats should return counts of pipelines by status."""
    token = await _register_and_get_token(client)
    headers = _auth_header(token)

    # Create some pipelines
    await client.post(
        "/api/tasks",
        json={"description": "Task A", "project_path": "/tmp"},
        headers=headers,
    )
    await client.post(
        "/api/tasks",
        json={"description": "Task B", "project_path": "/tmp"},
        headers=headers,
    )

    resp = await client.get("/api/stats", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_runs" in data
    assert "active" in data
    assert "completed" in data
    assert "failed" in data
    assert data["total_runs"] >= 2
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest forge/api/routes/tasks_test.py::test_stats_returns_pipeline_counts -v`
Expected: FAIL — endpoint doesn't exist (404).

**Step 3: Implement the stats endpoint**

Add to `forge/api/routes/tasks.py`:

```python
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
```

**IMPORTANT:** This endpoint must be registered BEFORE the `/{pipeline_id}` route in the router, otherwise FastAPI will match "stats" as a `pipeline_id`. Place it right after the `POST ""` route.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest forge/api/routes/tasks_test.py -v`
Expected: ALL PASS.

**Step 5: Update the dashboard frontend**

Replace `web/src/app/page.tsx` stats section to fetch from API:

Change the `STATS` constant to be state-driven:

```typescript
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

interface DashboardStats {
  total_runs: number;
  active: number;
  completed: number;
  failed: number;
}

interface RecentPipeline {
  pipeline_id: string;
  description: string;
  phase: string;
  created_at: string;
}

// ... keep QUICK_ACTIONS unchanged ...

export default function DashboardPage() {
  const token = useAuthStore((s) => s.token);
  const [stats, setStats] = useState<DashboardStats>({
    total_runs: 0, active: 0, completed: 0, failed: 0,
  });
  const [recent, setRecent] = useState<RecentPipeline[]>([]);

  useEffect(() => {
    if (!token) return;
    apiGet("/tasks/stats", token).then(setStats).catch(() => {});
    apiGet("/history", token).then((data) => setRecent(data.slice(0, 5))).catch(() => {});
  }, [token]);

  const STATS = [
    { label: "Total Runs", value: String(stats.total_runs) },
    { label: "Active", value: String(stats.active) },
    { label: "Completed", value: String(stats.completed) },
  ];

  return (
    // ... keep JSX structure, replace hardcoded STATS with dynamic,
    // replace "No recent tasks" with recent pipeline list
  );
}
```

**Step 6: Rebuild frontend**

Run: `cd web && npm run build`
Expected: Build succeeds.

**Step 7: Commit**

```bash
git add forge/api/routes/tasks.py web/src/app/page.tsx
git commit -m "feat: add /stats endpoint and wire dashboard to real data"
```

---

### Task 9: Smart Merge Retry — Tier 1 (Auto-Rebase Retry)

**Files:**
- Modify: `forge/core/daemon.py`
- Modify: `forge/merge/worker.py`
- Test: `forge/merge/worker_test.py`

**Step 1: Write the failing test**

Add to `forge/merge/worker_test.py`:

```python
def test_retry_merge_after_rebase_conflict(git_repo):
    """MergeWorker.retry_merge() should re-fetch main and retry rebase."""
    worker = MergeWorker(str(git_repo), main_branch="master")

    # Create a branch with changes
    subprocess.run(
        ["git", "checkout", "-b", "forge/task-1"],
        cwd=git_repo, check=True, capture_output=True,
    )
    (git_repo / "feature.py").write_text("# feature\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add feature"],
        cwd=git_repo, check=True, capture_output=True,
    )

    # Go back to master and add a non-conflicting change
    subprocess.run(
        ["git", "checkout", "master"],
        cwd=git_repo, check=True, capture_output=True,
    )
    (git_repo / "other.py").write_text("# other\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add other"],
        cwd=git_repo, check=True, capture_output=True,
    )

    # retry_merge should succeed since there's no conflict
    result = worker.retry_merge("forge/task-1")
    assert result.success is True
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest forge/merge/worker_test.py::test_retry_merge_after_rebase_conflict -v`
Expected: FAIL — `retry_merge()` doesn't exist.

**Step 3: Implement `retry_merge()` on MergeWorker**

Add to `forge/merge/worker.py`:

```python
def retry_merge(self, branch: str, worktree_path: str | None = None) -> MergeResult:
    """Retry a merge: abort any in-progress rebase, re-attempt rebase + ff merge.

    This is Tier 1 retry — no agent re-run, just git operations.
    Used when a merge failed due to a conflict that may resolve after
    another task has merged (making main advance).
    """
    # Abort any lingering rebase state
    self._abort_rebase(worktree_path)

    # Re-attempt the full merge sequence
    return self.merge(branch, worktree_path=worktree_path)
```

**Step 4: Update daemon to use Tier 1 retry for merge failures**

In `forge/core/daemon.py`, modify the merge failure block in `_execute_task()`:

Change from:
```python
else:
    console.print(f"[red]{task_id} merge failed: {merge_result.error}[/red]")
    await self._events.emit("task:merge_result", {
        "task_id": task_id, "success": False, "error": merge_result.error,
    })
    await self._handle_retry(db, task_id, worktree_mgr)
```

To:
```python
else:
    console.print(f"[red]{task_id} merge failed: {merge_result.error}[/red]")
    console.print(f"[yellow]{task_id}: trying Tier 1 merge retry (auto-rebase)...[/yellow]")
    await self._events.emit("task:merge_result", {
        "task_id": task_id, "success": False, "error": merge_result.error,
    })

    # Tier 1: retry merge only (no agent re-run)
    retry_result = merge_worker.retry_merge(branch, worktree_path=worktree_path)
    if retry_result.success:
        console.print(f"[bold green]{task_id} merged on retry![/bold green]")
        await db.update_task_state(task_id, TaskState.DONE.value)
        await self._events.emit("task:merge_result", {
            "task_id": task_id, "success": True, "error": None,
        })
        await self._events.emit("task:state_changed", {"task_id": task_id, "state": "done"})
    else:
        console.print(f"[red]{task_id} merge retry also failed: {retry_result.error}[/red]")
        await self._handle_retry(db, task_id, worktree_mgr)
```

**Step 5: Run tests**

Run: `.venv/bin/pytest forge/merge/worker_test.py forge/core/ -v`
Expected: ALL PASS.

**Step 6: Commit**

```bash
git add forge/merge/worker.py forge/merge/worker_test.py forge/core/daemon.py
git commit -m "feat: add Tier 1 smart merge retry — auto-rebase before full agent re-run"
```

---

### Task 10: Smart Merge Retry — Tier 2 (Agent Fix-Up for Conflicts)

**Files:**
- Modify: `forge/core/daemon.py`

**Step 1: Implement Tier 2 conflict resolution in daemon**

Add a `_resolve_conflicts()` method to `ForgeDaemon`:

```python
async def _resolve_conflicts(
    self, task_id: str, worktree_path: str,
    conflicting_files: list[str], agent_model: str,
) -> bool:
    """Tier 2: Use a targeted Claude call to resolve merge conflicts."""
    if not conflicting_files:
        return False

    console.print(f"[yellow]{task_id}: Tier 2 — asking Claude to resolve {len(conflicting_files)} conflicts[/yellow]")

    # Get the current conflict state
    conflict_prompt = (
        f"The following files have merge conflicts that need to be resolved:\n"
        f"{', '.join(conflicting_files)}\n\n"
        f"Instructions:\n"
        f"1. Open each conflicting file\n"
        f"2. Resolve the merge conflict markers (<<<<<<, =======, >>>>>>)\n"
        f"3. Keep the intent of BOTH changes where possible\n"
        f"4. Stage and commit the resolved files: git add -A && git commit -m 'fix: resolve merge conflicts'\n"
    )

    adapter = ClaudeAdapter()
    runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)
    result = await runtime.run_task(
        agent_id=f"resolver-{task_id}",
        task_prompt=conflict_prompt,
        worktree_path=worktree_path,
        allowed_files=conflicting_files,
        model=agent_model,
    )

    return result.success
```

Then update the merge failure flow in `_execute_task()` to escalate from Tier 1 to Tier 2:

```python
# Tier 1 failed — try Tier 2 (agent fix-up) if we have conflicting files
if retry_result.conflicting_files:
    resolved = await self._resolve_conflicts(
        task_id, worktree_path,
        retry_result.conflicting_files, agent_model,
    )
    if resolved:
        # Try merge one more time after conflict resolution
        final_result = merge_worker.merge(branch, worktree_path=worktree_path)
        if final_result.success:
            console.print(f"[bold green]{task_id} merged after conflict resolution![/bold green]")
            await db.update_task_state(task_id, TaskState.DONE.value)
            await self._events.emit("task:merge_result", {
                "task_id": task_id, "success": True, "error": None,
            })
            await self._events.emit("task:state_changed", {"task_id": task_id, "state": "done"})
            return  # Skip the full retry below

# Tier 3: full retry (existing behavior)
await self._handle_retry(db, task_id, worktree_mgr)
```

**Step 2: Run existing tests**

Run: `.venv/bin/pytest forge/core/ forge/merge/ -v`
Expected: ALL PASS.

**Step 3: Commit**

```bash
git add forge/core/daemon.py
git commit -m "feat: add Tier 2 merge retry — targeted conflict resolution via Claude"
```

---

### Task 11: Create PR Endpoint

**Files:**
- Modify: `forge/api/routes/tasks.py`
- Test: `forge/api/routes/tasks_test.py`

**Step 1: Write the failing test**

Add to `forge/api/routes/tasks_test.py`:

```python
from unittest.mock import patch, MagicMock

async def test_create_pr_returns_pr_url(client):
    """POST /api/tasks/{id}/pr should create a PR and return the URL."""
    token = await _register_and_get_token(client)
    headers = _auth_header(token)

    # Create a pipeline
    create_resp = await client.post(
        "/api/tasks",
        json={"description": "PR Test", "project_path": "/tmp/test"},
        headers=headers,
    )
    pipeline_id = create_resp.json()["pipeline_id"]

    # Mock subprocess to simulate git push and gh pr create
    mock_push = MagicMock()
    mock_push.returncode = 0
    mock_push.stdout = ""

    mock_gh = MagicMock()
    mock_gh.returncode = 0
    mock_gh.stdout = "https://github.com/user/repo/pull/42\n"

    with patch("subprocess.run", side_effect=[mock_push, mock_push, mock_gh]):
        resp = await client.post(
            f"/api/tasks/{pipeline_id}/pr",
            headers=headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "pr_url" in data
    assert "github.com" in data["pr_url"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest forge/api/routes/tasks_test.py::test_create_pr_returns_pr_url -v`
Expected: FAIL — endpoint doesn't exist (404 or 405).

**Step 3: Implement the PR creation endpoint**

Add to `forge/api/routes/tasks.py`:

```python
import subprocess

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
    if hasattr(pipeline, "pr_url") and pipeline.pr_url:
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
```

**IMPORTANT:** Register this route AFTER `/{pipeline_id}/execute` but BEFORE `/{pipeline_id}` GET route. The order matters for FastAPI path matching.

**Step 4: Run tests**

Run: `.venv/bin/pytest forge/api/routes/tasks_test.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add forge/api/routes/tasks.py forge/api/routes/tasks_test.py
git commit -m "feat: add POST /tasks/{id}/pr endpoint to create GitHub PRs"
```

---

### Task 12: Create PR — Frontend Button

**Files:**
- Modify: `web/src/components/task/CompletionSummary.tsx`

**Step 1: Replace placeholder buttons with Create PR**

Update `CompletionSummary.tsx`:

```typescript
"use client";

import { useState } from "react";
import Link from "next/link";
import type { TaskState } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";

// ... keep StatusDot unchanged ...

export default function CompletionSummary({
  tasks,
  pipelineId,
}: {
  tasks: Record<string, TaskState>;
  pipelineId: string;
}) {
  const token = useAuthStore((s) => s.token);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [prLoading, setPrLoading] = useState(false);
  const [prError, setPrError] = useState<string | null>(null);

  // ... keep existing stats logic ...

  const handleCreatePR = async () => {
    if (!token || !pipelineId) return;
    setPrLoading(true);
    setPrError(null);
    try {
      const data = await apiPost(`/tasks/${pipelineId}/pr`, {}, token);
      setPrUrl(data.pr_url);
    } catch (err: unknown) {
      setPrError(err instanceof Error ? err.message : "Failed to create PR");
    } finally {
      setPrLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
      {/* ... keep status banner, stats grid, task list unchanged ... */}

      {/* Actions */}
      <div className="flex flex-wrap gap-3">
        {prUrl ? (
          <a
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-500"
          >
            View PR on GitHub
          </a>
        ) : (
          <button
            type="button"
            disabled={prLoading}
            className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700 disabled:opacity-50"
            onClick={handleCreatePR}
          >
            {prLoading ? "Creating PR..." : "Create PR"}
          </button>
        )}
        {prError && (
          <span className="self-center text-sm text-red-400">{prError}</span>
        )}
        <Link
          href="/tasks/new"
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500"
        >
          New Task
        </Link>
      </div>
    </div>
  );
}
```

**Note:** The component now accepts `pipelineId` as a prop. The parent component that renders `CompletionSummary` needs to pass it. Check `web/src/app/tasks/[id]/page.tsx` or wherever `CompletionSummary` is used and pass the pipeline ID.

**Step 2: Rebuild frontend**

Run: `cd web && npm run build`
Expected: Build succeeds.

**Step 3: Commit**

```bash
git add web/src/components/task/CompletionSummary.tsx
git commit -m "feat: replace placeholder buttons with working Create PR button"
```

---

### Task 13: Full Test Suite + Frontend Build

**Step 1: Run all backend tests**

Run: `.venv/bin/pytest forge/ -v --tb=short`
Expected: ALL PASS (329+ tests).

**Step 2: Rebuild frontend**

Run: `cd web && npm run build`
Expected: Build succeeds with no errors.

**Step 3: Verify no regressions**

Check the following manually:
- `forge serve --port 8000` starts without errors
- Login page loads
- Login persists after refresh
- Dashboard shows real stats
- History page shows past pipelines
- New task → plan → execute → live logs visible
- Completion summary → Create PR button works

**Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address test/build regressions from v3 changes"
```
