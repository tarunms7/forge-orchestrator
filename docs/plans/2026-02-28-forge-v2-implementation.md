# Forge v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Smart model routing by complexity, speed improvements, fix macOS permission popup, and wire the web UI to actually execute tasks via plan-first approval flow.

**Architecture:** The daemon gains an EventEmitter to broadcast state changes over WebSocket. `forge serve` hosts both FastAPI API and the built Next.js frontend on one port. The `/api/tasks` endpoint triggers planning only; a separate `/api/tasks/{id}/execute` triggers agent dispatch after user reviews the plan.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, claude-code-sdk, Next.js 14, Zustand, WebSocket

---

## Phase 1: Backend Core Fixes (no UI changes)

### Task 1: Smart Model Routing — Settings & Strategy

**Files:**
- Modify: `forge/config/settings.py`
- Modify: `forge/storage/db_test.py` (verify no breakage)

**Step 1: Update ForgeSettings to use model_strategy**

In `forge/config/settings.py`, replace the `model` field with `model_strategy`:

```python
"""Forge configuration. All settings in one place with sensible defaults."""

from pydantic_settings import BaseSettings


class ForgeSettings(BaseSettings):
    """Global settings. Override via environment variables prefixed FORGE_."""

    model_config = {"env_prefix": "FORGE_"}

    # Model routing strategy
    model_strategy: str = "auto"  # "auto", "fast", "quality"

    # Agent limits
    max_agents: int = 4
    agent_timeout_seconds: int = 600  # lowered from 1800
    context_rotation_tokens: int = 80_000
    max_retries: int = 3

    # Agent sandboxing
    allowed_dirs: list[str] = []  # Extra directories agents can access

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 10.0
    disk_threshold_gb: float = 5.0

    # Database
    db_url: str = "sqlite+aiosqlite:///forge.db"

    # Polling
    scheduler_poll_interval: float = 1.0
```

Note: `agent_timeout_seconds` lowered from 1800 to 600 (Task 3 speed fix).

**Step 2: Run tests**

Run: `pytest forge/ -q`
Expected: All 332 tests pass (no test uses `settings.model` directly)

**Step 3: Commit**

```bash
git add forge/config/settings.py
git commit -m "feat: replace model with model_strategy setting, lower timeout to 600s"
```

---

### Task 2: Smart Model Routing — Model Selection Logic

**Files:**
- Create: `forge/core/model_router.py`
- Create: `forge/core/model_router_test.py`

**Step 1: Write the failing test**

Create `forge/core/model_router_test.py`:

```python
"""Tests for model routing by complexity and pipeline stage."""

from forge.core.model_router import select_model


class TestSelectModel:
    def test_auto_low_agent(self):
        assert select_model("auto", "agent", "low") == "sonnet"

    def test_auto_medium_agent(self):
        assert select_model("auto", "agent", "medium") == "opus"

    def test_auto_high_agent(self):
        assert select_model("auto", "agent", "high") == "opus"

    def test_auto_planner_always_opus(self):
        assert select_model("auto", "planner", "low") == "opus"
        assert select_model("auto", "planner", "high") == "opus"

    def test_auto_reviewer_low(self):
        assert select_model("auto", "reviewer", "low") == "sonnet"

    def test_auto_reviewer_high(self):
        assert select_model("auto", "reviewer", "high") == "opus"

    def test_fast_strategy(self):
        assert select_model("fast", "agent", "high") == "haiku"
        assert select_model("fast", "planner", "high") == "sonnet"
        assert select_model("fast", "reviewer", "high") == "sonnet"

    def test_quality_strategy(self):
        assert select_model("quality", "agent", "low") == "opus"
        assert select_model("quality", "planner", "low") == "opus"
        assert select_model("quality", "reviewer", "low") == "opus"

    def test_unknown_strategy_defaults_to_auto(self):
        assert select_model("unknown", "agent", "low") == "sonnet"
```

**Step 2: Run test to verify it fails**

Run: `pytest forge/core/model_router_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'forge.core.model_router'`

**Step 3: Write the implementation**

Create `forge/core/model_router.py`:

```python
"""Model routing by task complexity and pipeline stage."""

# Strategy -> Stage -> Complexity -> Model
_ROUTING_TABLE: dict[str, dict[str, dict[str, str]]] = {
    "auto": {
        "planner": {"low": "opus", "medium": "opus", "high": "opus"},
        "agent": {"low": "sonnet", "medium": "opus", "high": "opus"},
        "reviewer": {"low": "sonnet", "medium": "opus", "high": "opus"},
    },
    "fast": {
        "planner": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
        "agent": {"low": "haiku", "medium": "haiku", "high": "haiku"},
        "reviewer": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
    },
    "quality": {
        "planner": {"low": "opus", "medium": "opus", "high": "opus"},
        "agent": {"low": "opus", "medium": "opus", "high": "opus"},
        "reviewer": {"low": "opus", "medium": "opus", "high": "opus"},
    },
}


def select_model(strategy: str, stage: str, complexity: str) -> str:
    """Select the Claude model for a given strategy, pipeline stage, and task complexity.

    Args:
        strategy: "auto", "fast", or "quality"
        stage: "planner", "agent", or "reviewer"
        complexity: "low", "medium", or "high"

    Returns:
        Model name string: "opus", "sonnet", or "haiku"
    """
    table = _ROUTING_TABLE.get(strategy, _ROUTING_TABLE["auto"])
    stage_map = table.get(stage, table["agent"])
    return stage_map.get(complexity, "sonnet")
```

**Step 4: Run tests**

Run: `pytest forge/core/model_router_test.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add forge/core/model_router.py forge/core/model_router_test.py
git commit -m "feat: add model routing table by strategy/stage/complexity"
```

---

### Task 3: Wire Model Router into Daemon

**Files:**
- Modify: `forge/core/daemon.py`
- Modify: `forge/agents/adapter.py` (accept model per-call)
- Modify: `forge/cli/main.py` (replace --model with --strategy)

**Step 1: Update ClaudeAdapter to accept model per-call**

In `forge/agents/adapter.py`, change `__init__` to not take a model, and add model param to `run()` and `_build_options()`:

```python
class ClaudeAdapter(AgentAdapter):
    """Claude Code agent via claude-code-sdk."""

    def _build_options(
        self, worktree_path: str, allowed_dirs: list[str], model: str = "sonnet",
    ) -> ClaudeCodeOptions:
        """Build ClaudeCodeOptions with directory boundary enforcement."""
        if allowed_dirs:
            extra_dirs_clause = " and the following allowed directories: " + ", ".join(
                allowed_dirs
            )
        else:
            extra_dirs_clause = ""
        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=worktree_path, extra_dirs_clause=extra_dirs_clause
        )
        return ClaudeCodeOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
            cwd=worktree_path,
            model=model,
            max_turns=25,
        )

    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
        allowed_dirs: list[str] | None = None,
        model: str = "sonnet",
    ) -> AgentResult:
        options = self._build_options(worktree_path, allowed_dirs or [], model=model)
        # ... rest stays the same
```

Key changes:
- Remove `__init__` (no more per-instance model)
- `permission_mode="acceptEdits"` (fixes macOS popup — Task 4 security fix)
- `max_turns=25` (Task 3 speed fix)
- `model` param on `run()` and `_build_options()`

Also update the ABC `AgentAdapter.run()` signature to include `model: str = "sonnet"`.

**Step 2: Update AgentRuntime to pass model through**

In `forge/agents/runtime.py`, update `run_task` to accept and pass `model`:

```python
async def run_task(
    self, agent_id, prompt, worktree_path, allowed_files,
    allowed_dirs=None, model="sonnet",
):
    # ... pass model= to self._adapter.run(...)
```

**Step 3: Update daemon to use model_router**

In `forge/core/daemon.py` `_run_pipeline`:

```python
from forge.core.model_router import select_model

async def _run_pipeline(self, db, user_input):
    strategy = self._settings.model_strategy
    planner_model = select_model(strategy, "planner", "high")
    console.print(f"[dim]Strategy: {strategy} | Planner model: {planner_model}[/dim]")

    planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir)
    # ... rest of setup ...
    adapter = ClaudeAdapter()  # no model arg anymore
    # ... pass strategy to _execution_loop via instance var
    self._strategy = strategy
```

In `_execute_task`, select model per task:

```python
agent_model = select_model(self._strategy, "agent", task.complexity or "medium")
console.print(f"[dim]{task_id}: using {agent_model}[/dim]")
result = await runtime.run_task(
    agent_id, prompt, worktree_path, task.files,
    allowed_dirs=self._settings.allowed_dirs,
    model=agent_model,
)
```

In `_run_review`, select reviewer model:

```python
reviewer_model = select_model(self._strategy, "reviewer", task.complexity or "medium")
gate2_result = await gate2_llm_review(
    task.title, task.description, diff, worktree_path,
    model=reviewer_model,
)
```

**Step 4: Update CLI**

In `forge/cli/main.py`, replace `--model` with `--strategy`:

```python
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality (default: auto, or $FORGE_MODEL_STRATEGY)",
)
def run(task: str, project_dir: str, strategy: str | None) -> None:
    settings = ForgeSettings()
    if strategy:
        settings.model_strategy = strategy
    daemon = ForgeDaemon(project_dir, settings=settings)
```

**Step 5: Run all tests**

Run: `pytest forge/ -q`
Expected: All tests pass (adapter tests use mocks, not real SDK)

**Step 6: Commit**

```bash
git add forge/core/daemon.py forge/agents/adapter.py forge/agents/runtime.py forge/cli/main.py
git commit -m "feat: wire model router — per-task model selection by complexity"
```

---

### Task 4: Security Fix — Permission Mode (already done in Task 3)

The `permission_mode="acceptEdits"` change was included in Task 3's adapter update. Verify:

**Step 1: Confirm the change**

Read `forge/agents/adapter.py` and verify `permission_mode="acceptEdits"` is set.

**Step 2: Run tests**

Run: `pytest forge/ -q`
Expected: All pass

---

### Task 5: Better Planner Prompt for Parallelism

**Files:**
- Modify: `forge/core/claude_planner.py`

**Step 1: Update the system prompt**

Add parallelism instruction to `PLANNER_SYSTEM_PROMPT` in `forge/core/claude_planner.py`:

Add after the existing rules:
```
- MINIMIZE dependencies. Only add depends_on when a task genuinely needs another task's output files. Independent tasks should have empty depends_on so they run in parallel.
- Never make test tasks depend on implementation tasks — tests should be self-contained with mocks.
```

**Step 2: Run tests**

Run: `pytest forge/ -q`
Expected: All pass

**Step 3: Commit**

```bash
git add forge/core/claude_planner.py
git commit -m "feat: planner prompt encourages parallel task decomposition"
```

---

## Phase 2: Backend API for UI Integration

### Task 6: Add Pipeline Table to Database

**Files:**
- Modify: `forge/storage/db.py`
- Create: `forge/storage/pipeline_db_test.py`

**Step 1: Write the failing test**

Create `forge/storage/pipeline_db_test.py`:

```python
"""Tests for pipeline persistence."""

import pytest
from forge.storage.db import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


async def test_create_and_get_pipeline(db):
    await db.create_pipeline(
        id="pipe-1",
        description="Build login",
        project_dir="/tmp/test",
        model_strategy="auto",
    )
    p = await db.get_pipeline("pipe-1")
    assert p is not None
    assert p.description == "Build login"
    assert p.status == "planning"
    assert p.model_strategy == "auto"


async def test_update_pipeline_status(db):
    await db.create_pipeline(id="pipe-1", description="t", project_dir="/tmp", model_strategy="auto")
    await db.update_pipeline_status("pipe-1", "executing")
    p = await db.get_pipeline("pipe-1")
    assert p.status == "executing"


async def test_set_pipeline_plan(db):
    await db.create_pipeline(id="pipe-1", description="t", project_dir="/tmp", model_strategy="auto")
    await db.set_pipeline_plan("pipe-1", '{"tasks": []}')
    p = await db.get_pipeline("pipe-1")
    assert p.task_graph_json == '{"tasks": []}'


async def test_list_pipelines(db):
    await db.create_pipeline(id="p1", description="a", project_dir="/tmp", model_strategy="auto", user_id="u1")
    await db.create_pipeline(id="p2", description="b", project_dir="/tmp", model_strategy="auto", user_id="u2")
    all_pipes = await db.list_pipelines()
    assert len(all_pipes) == 2
    user_pipes = await db.list_pipelines(user_id="u1")
    assert len(user_pipes) == 1
    assert user_pipes[0].id == "p1"
```

**Step 2: Run test to verify it fails**

Run: `pytest forge/storage/pipeline_db_test.py -v`
Expected: FAIL — `create_pipeline` not found

**Step 3: Add PipelineRow and CRUD to db.py**

Add to `forge/storage/db.py` after AgentRow:

```python
class PipelineRow(Base):
    __tablename__ = "pipelines"

    id = Column(String, primary_key=True)
    description = Column(String, nullable=False)
    project_dir = Column(String, nullable=False)
    status = Column(String, default="planning")  # planning, planned, executing, complete, error
    model_strategy = Column(String, default="auto")
    task_graph_json = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    created_at = Column(String, nullable=True)
    completed_at = Column(String, nullable=True)
```

Add CRUD methods to `Database` class:

```python
async def create_pipeline(self, id, description, project_dir, model_strategy="auto", user_id=None):
    async with self._session() as session:
        from datetime import datetime, timezone
        row = PipelineRow(
            id=id, description=description, project_dir=project_dir,
            model_strategy=model_strategy, user_id=user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(row)
        await session.commit()

async def get_pipeline(self, pipeline_id):
    async with self._session() as session:
        result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
        return result.scalar_one_or_none()

async def update_pipeline_status(self, pipeline_id, status):
    async with self._session() as session:
        result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
        row = result.scalar_one_or_none()
        if row:
            row.status = status
            if status in ("complete", "error"):
                from datetime import datetime, timezone
                row.completed_at = datetime.now(timezone.utc).isoformat()
            await session.commit()

async def set_pipeline_plan(self, pipeline_id, task_graph_json):
    async with self._session() as session:
        result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
        row = result.scalar_one_or_none()
        if row:
            row.task_graph_json = task_graph_json
            row.status = "planned"
            await session.commit()

async def list_pipelines(self, user_id=None):
    async with self._session() as session:
        query = select(PipelineRow)
        if user_id:
            query = query.where(PipelineRow.user_id == user_id)
        result = await session.execute(query.order_by(PipelineRow.created_at.desc()))
        return list(result.scalars().all())
```

**Step 4: Run tests**

Run: `pytest forge/storage/pipeline_db_test.py -v`
Expected: All PASS

**Step 5: Run all tests**

Run: `pytest forge/ -q`
Expected: All pass

**Step 6: Commit**

```bash
git add forge/storage/db.py forge/storage/pipeline_db_test.py
git commit -m "feat: add pipelines table with CRUD for persistent pipeline tracking"
```

---

### Task 7: Split Daemon into plan() and execute()

**Files:**
- Modify: `forge/core/daemon.py`

**Step 1: Refactor ForgeDaemon**

Split the monolithic `run()` into two public methods:

```python
class ForgeDaemon:
    def __init__(self, project_dir, settings=None, event_emitter=None):
        self._project_dir = project_dir
        self._settings = settings or ForgeSettings()
        self._state_machine = TaskStateMachine()
        self._events = event_emitter or EventEmitter()
        self._strategy = self._settings.model_strategy

    async def plan(self, user_input: str, db: Database) -> TaskGraph:
        """Run planning only. Returns the TaskGraph for user approval."""
        await self._events.emit("pipeline:phase_changed", {"phase": "planning"})

        strategy = self._settings.model_strategy
        planner_model = select_model(strategy, "planner", "high")
        console.print(f"[dim]Strategy: {strategy} | Planner: {planner_model}[/dim]")

        planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir)
        planner = Planner(planner_llm, max_retries=self._settings.max_retries)

        graph = await planner.plan(user_input, context=self._gather_context())
        console.print(f"[green]Plan: {len(graph.tasks)} tasks[/green]")

        for task_def in graph.tasks:
            console.print(f"  - {task_def.id}: {task_def.title} [{task_def.complexity.value}]")

        await self._events.emit("pipeline:plan_ready", {
            "tasks": [
                {
                    "id": t.id, "title": t.title, "description": t.description,
                    "files": t.files, "depends_on": t.depends_on,
                    "complexity": t.complexity.value,
                }
                for t in graph.tasks
            ]
        })
        return graph

    async def execute(self, graph: TaskGraph, db: Database) -> None:
        """Execute a previously approved TaskGraph."""
        await self._events.emit("pipeline:phase_changed", {"phase": "executing"})

        for task_def in graph.tasks:
            await db.create_task(
                id=task_def.id, title=task_def.title,
                description=task_def.description, files=task_def.files,
                depends_on=task_def.depends_on, complexity=task_def.complexity.value,
            )

        for i in range(self._settings.max_agents):
            await db.create_agent(f"agent-{i}")

        # setup components
        monitor = ResourceMonitor(...)
        worktree_mgr = WorktreeManager(...)
        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)
        current_branch = _get_current_branch(self._project_dir)
        merge_worker = MergeWorker(self._project_dir, main_branch=current_branch)

        await self._execution_loop(db, runtime, worktree_mgr, merge_worker, monitor)
        await self._events.emit("pipeline:phase_changed", {"phase": "complete"})

    async def run(self, user_input: str) -> None:
        """Full pipeline for CLI: plan + execute. Maintains backward compat."""
        db_path = os.path.join(self._project_dir, ".forge", "forge.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        if os.path.exists(db_path):
            os.remove(db_path)
        db = Database(db_url)
        await db.initialize()
        try:
            graph = await self.plan(user_input, db)
            await self.execute(graph, db)
        finally:
            await db.close()
```

Also add event emissions throughout `_execute_task` and `_run_review`:

```python
# In _execute_task, after state changes:
await self._events.emit("task:state_changed", {"task_id": task_id, "state": "in_progress"})

# After agent completes:
await self._events.emit("task:state_changed", {"task_id": task_id, "state": "in_review"})

# In _run_review, after each gate:
await self._events.emit("task:review_update", {
    "task_id": task.id, "gate": "gate1", "passed": gate1_result.passed,
    "details": gate1_result.details,
})

# After merge:
await self._events.emit("task:merge_result", {
    "task_id": task_id, "success": merge_result.success,
    "error": merge_result.error,
})
await self._events.emit("task:state_changed", {"task_id": task_id, "state": "done"})
```

**Step 2: Run all tests**

Run: `pytest forge/ -q`
Expected: All pass (CLI `run` still calls `daemon.run()` which chains plan+execute)

**Step 3: Commit**

```bash
git add forge/core/daemon.py
git commit -m "feat: split daemon into plan() + execute(), add event emissions"
```

---

### Task 8: New API Endpoints — Execute & Plan Edit

**Files:**
- Modify: `forge/api/routes/tasks.py`
- Modify: `forge/api/app.py`
- Modify: `forge/api/models/schemas.py`

**Step 1: Update schemas**

Add to `forge/api/models/schemas.py`:

```python
class CreateTaskRequest(BaseModel):
    description: str
    project_path: str
    extra_dirs: list[str] = []
    model_strategy: str = "auto"  # new field

class ExecuteRequest(BaseModel):
    """Optional: edited task graph to execute instead of the planned one."""
    tasks: list[dict] | None = None  # if provided, overrides planned graph
```

**Step 2: Rewrite tasks.py to use DB + daemon**

Replace in-memory dict with DB-backed pipeline management. The POST `/tasks` endpoint starts planning in a background asyncio task. The POST `/tasks/{id}/execute` starts execution.

Key changes:
- `POST /tasks` → creates pipeline in DB, spawns `daemon.plan()` as background task, returns pipeline_id
- `POST /tasks/{id}/execute` → spawns `daemon.execute()` as background task
- `GET /tasks/{id}` → reads from DB
- `GET /tasks` → lists from DB

The daemon's EventEmitter is bridged to the WebSocket ConnectionManager so events flow to the browser.

**Step 3: Update app.py**

Prefix all API routes with `/api`:
```python
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])
# ... etc
```

Move WebSocket to `/api/ws/{pipeline_id}`.

**Step 4: Run tests**

Run: `pytest forge/ -q`
Expected: All pass (API tests use httpx TestClient)

**Step 5: Commit**

```bash
git add forge/api/routes/tasks.py forge/api/app.py forge/api/models/schemas.py
git commit -m "feat: API endpoints for plan-first execution flow with DB persistence"
```

---

### Task 9: Bridge EventEmitter to WebSocket

**Files:**
- Modify: `forge/api/routes/tasks.py` (event bridging setup)
- Modify: `forge/api/ws/handler.py` (no change needed, already forwards)

**Step 1: Create event bridge**

When a pipeline is created, register an event handler that broadcasts to the WebSocket manager:

```python
# In the POST /tasks endpoint, after creating daemon:
async def bridge_event(event_data):
    await ws_manager.broadcast(pipeline_id, event_data)

daemon._events.on("pipeline:phase_changed", lambda data: bridge_event({"type": "pipeline:phase_changed", **data}))
daemon._events.on("pipeline:plan_ready", lambda data: bridge_event({"type": "pipeline:plan_ready", **data}))
daemon._events.on("task:state_changed", lambda data: bridge_event({"type": "task:state_changed", **data}))
daemon._events.on("task:agent_output", lambda data: bridge_event({"type": "task:agent_output", **data}))
daemon._events.on("task:review_update", lambda data: bridge_event({"type": "task:review_update", **data}))
daemon._events.on("task:merge_result", lambda data: bridge_event({"type": "task:merge_result", **data}))
daemon._events.on("planner:output", lambda data: bridge_event({"type": "planner:output", **data}))
```

**Step 2: Run tests**

Run: `pytest forge/ -q`
Expected: All pass

**Step 3: Commit**

```bash
git add forge/api/routes/tasks.py
git commit -m "feat: bridge daemon events to WebSocket for real-time UI updates"
```

---

## Phase 3: Frontend Wiring

### Task 10: Configure Next.js Static Export

**Files:**
- Modify: `web/next.config.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/ws.ts`

**Step 1: Configure static export**

Update `web/next.config.ts`:

```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
```

**Step 2: Update API base URL**

In `web/src/lib/api.ts`, make the base URL configurable:

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api';

export async function fetchWithAuth(path: string, token: string, options: RequestInit = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...options.headers,
      Authorization: `Bearer ${token}`,
    },
  });
  // ...
}
```

**Step 3: Update WebSocket URL**

In `web/src/lib/ws.ts`:

```typescript
export function createWebSocket(pipelineId: string): WebSocket {
  const wsBase = process.env.NEXT_PUBLIC_WS_URL || `ws://${window.location.host}/api`;
  return new WebSocket(`${wsBase}/ws/${pipelineId}`);
}
```

**Step 4: Build and verify**

Run: `cd web && npm run build`
Expected: Static export to `web/out/` directory

**Step 5: Commit**

```bash
git add web/next.config.ts web/src/lib/api.ts web/src/lib/ws.ts
git commit -m "feat: configure Next.js static export with relative API paths"
```

---

### Task 11: Serve Static Frontend from FastAPI

**Files:**
- Modify: `forge/api/app.py`
- Modify: `forge/cli/main.py`

**Step 1: Mount static files in app.py**

Add at the end of `create_app()`, after all API routes:

```python
import os
from fastapi.staticfiles import StaticFiles

# Serve built frontend (must be LAST — catch-all)
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web", "out")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
```

**Step 2: Update forge serve**

In `forge/cli/main.py`, add a build step hint:

```python
@cli.command()
@click.option("--port", default=8000)
@click.option("--host", default="127.0.0.1")
@click.option("--db-url", default="sqlite+aiosqlite:///forge.db")
@click.option("--jwt-secret", default=None, envvar="FORGE_JWT_SECRET")
@click.option("--build-frontend/--no-build-frontend", default=True, help="Build Next.js before serving")
def serve(port, host, db_url, jwt_secret, build_frontend):
    """Start the Forge web server."""
    if build_frontend:
        _build_frontend()
    import uvicorn
    from forge.api.app import create_app
    app = create_app(db_url=db_url, jwt_secret=jwt_secret)
    click.echo(f"Forge UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


def _build_frontend():
    """Build the Next.js frontend if web/ directory exists."""
    import subprocess
    web_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web")
    if not os.path.isdir(web_dir):
        return
    out_dir = os.path.join(web_dir, "out")
    if os.path.isdir(out_dir):
        return  # already built
    click.echo("Building frontend...")
    subprocess.run(["npm", "run", "build"], cwd=web_dir, check=True)
```

**Step 3: Run tests**

Run: `pytest forge/ -q`
Expected: All pass

**Step 4: Commit**

```bash
git add forge/api/app.py forge/cli/main.py
git commit -m "feat: forge serve builds and hosts Next.js frontend on same port"
```

---

### Task 12: Update Frontend Task Creation to Use New API

**Files:**
- Modify: `web/src/app/tasks/new/page.tsx`
- Modify: `web/src/stores/taskStore.ts`

**Step 1: Update handleSubmit in task creation page**

The existing `handleSubmit` in `web/src/app/tasks/new/page.tsx` already POSTs to `/tasks`. Update it to include `model_strategy` and navigate to the task page which now shows the plan for approval:

```typescript
const handleSubmit = async () => {
  setSubmitting(true);
  try {
    const res = await apiPost('/tasks', token, {
      description: formState.task.description,
      project_path: formState.project.path,
      extra_dirs: [],
      model_strategy: 'auto',
    });
    const { pipeline_id } = res;
    // Navigate to task page — plan will arrive via WebSocket
    router.push(`/tasks/${pipeline_id}`);
  } catch (err) {
    setError(err instanceof Error ? err.message : 'Failed to create task');
  } finally {
    setSubmitting(false);
  }
};
```

**Step 2: Update task execution page to show plan approval**

In `web/src/app/tasks/[id]/page.tsx`, add a plan review state:

When phase is `"planning"` — show spinner.
When phase is `"planned"` (after `pipeline:plan_ready`) — show task list with Edit/Execute buttons.
When phase is `"executing"` — show the existing agent cards + progress.

Add an "Execute" button that calls `POST /api/tasks/{id}/execute`:

```typescript
const handleExecute = async () => {
  await apiPost(`/tasks/${pipelineId}/execute`, token, {});
};
```

**Step 3: Commit**

```bash
git add web/src/app/tasks/new/page.tsx web/src/app/tasks/\\[id\\]/page.tsx web/src/stores/taskStore.ts
git commit -m "feat: frontend plan-first flow — review plan then execute"
```

---

### Task 13: Update Frontend for /api Prefix

**Files:**
- Modify: `web/src/lib/api.ts` (already done in Task 10)
- Modify: `web/src/stores/authStore.ts`
- Modify: `web/src/app/login/page.tsx` (if it calls API directly)
- Modify: `web/src/app/register/page.tsx` (if it calls API directly)

**Step 1: Audit all API calls**

Search for any hardcoded `/auth`, `/tasks`, `/templates` paths in frontend code and ensure they all go through the `apiPost`/`apiGet` helpers which use the `/api` prefix.

**Step 2: Update auth store**

In `web/src/stores/authStore.ts`, ensure login/register calls use `/auth/login` and `/auth/register` (which will resolve to `/api/auth/login` via the API base).

**Step 3: Build and verify**

Run: `cd web && npm run build`
Expected: Clean build, no errors

**Step 4: Commit**

```bash
git add web/src/
git commit -m "fix: update all frontend API calls to use /api prefix"
```

---

## Phase 4: Integration Testing & Polish

### Task 14: End-to-End Verification

**Step 1: Build frontend**

```bash
cd web && npm install && npm run build
```

**Step 2: Start forge serve**

```bash
cd /path/to/repo && forge serve --port 8000
```

**Step 3: Manual verification checklist**

- [ ] `http://localhost:8000` loads the dashboard
- [ ] Register a new user
- [ ] Login
- [ ] Navigate to "New Task"
- [ ] Fill in task description and project path
- [ ] Submit — see planning spinner
- [ ] Plan appears with task cards (via WebSocket)
- [ ] Click "Execute" — agents start
- [ ] See real-time agent status updates
- [ ] Gates pass/fail shown in UI
- [ ] Completion summary displayed

**Step 4: Fix any issues found**

**Step 5: Final commit**

```bash
git add -A
git commit -m "fix: integration polish from e2e testing"
```

---

### Task 15: Create PR

```bash
git push origin HEAD
gh pr create --title "feat: Forge v2 — smart models, speed, security, full UI" --body "..."
```

---

## File Change Summary

| File | Action | Task |
|------|--------|------|
| `forge/config/settings.py` | Modify | 1 |
| `forge/core/model_router.py` | Create | 2 |
| `forge/core/model_router_test.py` | Create | 2 |
| `forge/core/daemon.py` | Modify | 3, 7 |
| `forge/agents/adapter.py` | Modify | 3 |
| `forge/agents/runtime.py` | Modify | 3 |
| `forge/cli/main.py` | Modify | 3, 11 |
| `forge/core/claude_planner.py` | Modify | 5 |
| `forge/storage/db.py` | Modify | 6 |
| `forge/storage/pipeline_db_test.py` | Create | 6 |
| `forge/api/routes/tasks.py` | Modify | 8, 9 |
| `forge/api/app.py` | Modify | 8, 11 |
| `forge/api/models/schemas.py` | Modify | 8 |
| `web/next.config.ts` | Modify | 10 |
| `web/src/lib/api.ts` | Modify | 10, 13 |
| `web/src/lib/ws.ts` | Modify | 10 |
| `web/src/app/tasks/new/page.tsx` | Modify | 12 |
| `web/src/app/tasks/[id]/page.tsx` | Modify | 12 |
| `web/src/stores/taskStore.ts` | Modify | 12 |
| `web/src/stores/authStore.ts` | Modify | 13 |
