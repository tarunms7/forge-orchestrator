# Forge v2: Smart Model Routing, Speed, Security & Full UI Integration

**Date**: 2026-02-28
**Status**: Approved

## 1. Smart Model Routing

Route model selection by task complexity and pipeline stage instead of using one model for everything.

### Model Matrix

| Stage | Low Complexity | Medium/High Complexity |
|-------|---------------|----------------------|
| Planning | Opus | Opus |
| Agent execution | Sonnet | Opus |
| Gate 2 (LLM review) | Sonnet | Opus |

### Configuration

- New setting: `model_strategy` with options `"auto"`, `"fast"`, `"quality"`
  - `auto` = matrix above (default)
  - `fast` = Haiku for agents, Sonnet for planning/review
  - `quality` = Opus everywhere
- Env var: `FORGE_MODEL_STRATEGY` / CLI flag: `--strategy`
- Planner always gets Opus (plan quality determines everything downstream)
- Daemon reads each task's `complexity` field (already set by planner) and selects model per-task
- Remove the single `model` setting, replace with `model_strategy`

### Files to modify

- `forge/config/settings.py` — replace `model` with `model_strategy`
- `forge/core/daemon.py` — model selection logic per task/stage
- `forge/agents/adapter.py` — accept model per-call instead of per-instance
- `forge/core/claude_planner.py` — always use Opus
- `forge/review/llm_review.py` — accept model param (already does)
- `forge/cli/main.py` — replace `--model` with `--strategy`

## 2. Speed Improvements

### A. Cap agent turns

Set `max_turns=25` on `ClaudeCodeOptions` for agent execution. Prevents runaway loops where agents spend 10+ turns on simple tasks. Planning and review already have `max_turns=1`.

### B. Lower default timeout

Change `agent_timeout_seconds` from 1800 (30 min) to 600 (10 min). A stuck agent shouldn't block for half an hour.

### C. Better planner prompt for parallelism

Update the planner system prompt to explicitly instruct: "minimize dependencies between tasks — only add depends_on when a task truly needs another task's output files." This maximizes parallel dispatch.

### D. Gate 1 auto-fix (already done)

`ruff --fix` runs before lint check, auto-fixing unused imports. Previously cost a full retry cycle (~5 min) for mechanical issues.

### Files to modify

- `forge/agents/adapter.py` — add `max_turns=25` to options
- `forge/config/settings.py` — change default timeout to 600
- `forge/core/claude_planner.py` — update system prompt for parallelism

## 3. Security — Fix Permissions Popup

### Root cause

`permission_mode="bypassPermissions"` tells Claude Code CLI to skip all permission prompts, which triggers macOS security dialogs when the subprocess accesses filesystem resources.

### Fix

- Switch to `permission_mode="acceptEdits"` — auto-approves file edits within the worktree but doesn't bypass OS-level permissions
- Keep allowed_tools as-is: `["Read", "Edit", "Write", "Glob", "Grep", "Bash"]`
- Worktree isolation + system prompt boundary already restricts agents to their task directory

### Files to modify

- `forge/agents/adapter.py` — change permission_mode

## 4. Full UI Integration

### Architecture

```
Browser -> FastAPI (:8000) -> ForgeDaemon (background asyncio task)
                |  WebSocket
        Real-time updates <- Daemon emits events via EventEmitter
```

### A. Single-command serve

`forge serve` starts FastAPI on :8000 which also serves the built Next.js frontend as static files.

- Build Next.js to `web/out/` (static export)
- FastAPI mounts static files at `/`
- API routes at `/api/...`
- WebSocket at `/api/ws/{pipeline_id}`

**Files to modify:**
- `forge/cli/main.py` — update serve command to mount static files
- `forge/api/app.py` — add static file mounting, prefix API routes with `/api`
- `web/next.config.js` — configure static export with `output: 'export'`

### B. Plan-first execution flow

1. POST `/api/tasks` with `{description, project_dir, model_strategy}` -> spawns planner only
2. WebSocket pushes `pipeline:plan_ready` with task graph
3. User reviews/edits plan in UI
4. POST `/api/tasks/{id}/execute` -> daemon spawns agents
5. WebSocket streams real-time updates throughout execution
6. WebSocket pushes `pipeline:complete` with summary

**New endpoints:**
- POST `/api/tasks/{id}/execute` — start execution after plan approval
- PATCH `/api/tasks/{id}/plan` — edit task graph before execution

**Files to create/modify:**
- `forge/api/routes/tasks.py` — add execute and plan-edit endpoints
- `forge/core/daemon.py` — split `run()` into `plan()` and `execute()`

### C. Wire daemon events to WebSocket

The `EventEmitter` class exists but daemon doesn't use it. Wire up:

- `task:state_changed` — task state transitions
- `task:agent_output` — streaming agent output lines
- `task:review_update` — Gate 1/2/3 pass/fail
- `task:merge_result` — merge success/failure
- `pipeline:phase_changed` — planning -> executing -> reviewing -> complete

The frontend Zustand store already handles all these event types.

**Files to modify:**
- `forge/core/daemon.py` — emit events at each state transition
- `forge/api/routes/tasks.py` — bridge EventEmitter to WebSocket manager
- `forge/api/ws/handler.py` — forward events to connected clients

### D. Persist pipelines to database

Replace in-memory dict with SQLite table.

New `pipelines` table:
- `id` (TEXT PK)
- `description` (TEXT)
- `project_dir` (TEXT)
- `status` (TEXT: planning, planned, executing, complete, error)
- `model_strategy` (TEXT)
- `task_graph_json` (TEXT, nullable — populated after planning)
- `created_at` (DATETIME)
- `completed_at` (DATETIME, nullable)

**Files to modify:**
- `forge/storage/db.py` — add pipeline CRUD methods
- `forge/api/routes/tasks.py` — use DB instead of in-memory dict
- `forge/api/models/` — add PipelineRow model

### E. User workflow after implementation

1. Run `forge serve` (once)
2. Open `http://localhost:8000`
3. Create task -> fill form -> submit
4. Review generated plan -> edit if needed -> click Execute
5. Watch real-time progress (agent output, review gates, merge status)
6. Done — code is merged, no terminal needed
