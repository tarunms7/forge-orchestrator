# Forge v4: Resilience, Observability & Controls

**Date:** 2026-03-01
**Status:** Approved

## Problem Statement

Forge pipelines lose all observable state (agent output, review results, merge stats) on page refresh or server restart. There is no way to resume interrupted pipelines, cancel stuck tasks, or control model routing from the UI. Pre-execution validation is missing, causing wasted token runs.

## Design Decisions

### 1. Event Persistence (P0)

**Root cause of data loss:** Agent output, review gates, merge results, and cost data are emitted as ephemeral WebSocket events and never written to the database.

**Solution:** Add a `pipeline_events` table that captures every event as it happens.

```
pipeline_events
  id          VARCHAR PK (uuid)
  pipeline_id VARCHAR FK -> pipelines
  task_id     VARCHAR (nullable — pipeline-level events have no task)
  event_type  VARCHAR NOT NULL
  payload     JSON NOT NULL
  created_at  DATETIME NOT NULL DEFAULT now
```

Event types: `agent_output`, `review_update`, `merge_result`, `phase_change`, `cost_update`, `state_change`, `files_changed`, `plan_ready`.

**Write path:** Every `self._events.emit(type, data)` in `daemon.py` also writes to this table via a new `Database.log_event()` method.

**Read path:** `GET /tasks/{pipeline_id}` reconstructs full state from events. Frontend `hydrateFromRest()` replays them into the Zustand store. Agent output is stored as batched lines (not individual characters) to keep row count reasonable.

**Indexes:** `(pipeline_id, created_at)` for timeline queries, `(pipeline_id, task_id, event_type)` for task-scoped hydration.

### 2. Pre-flight Checks (P0)

Add `_preflight_checks()` at the start of `execute()`, before any agents are dispatched:

1. `git remote` — verify remote exists
2. `gh auth status` — verify GitHub auth (if `gh` is available)
3. Working directory is a valid git repo

If remote is missing: emit a clear error event and abort execution with an actionable message ("No git remote. Run `git remote add origin <url>` or `gh repo create`"). Do NOT auto-create repos — that's a destructive side effect the user should control.

Emit `pipeline:preflight_failed` event with details so the UI can show what went wrong.

### 3. Pipeline Resume (P0)

**Endpoint:** `POST /tasks/{pipeline_id}/resume`

**Logic:**
1. Reconstruct `TaskGraph` from `PipelineRow.task_graph_json` (already stored).
2. Reset interrupted tasks:
   - `done` → skip (already merged)
   - `error` → skip (max retries exceeded)
   - `in_progress` / `in_review` / `merging` → reset to `todo`, keep `retry_count`
   - `todo` → leave as-is
   - `cancelled` → leave as-is
3. Check for surviving worktrees on disk. If a worktree exists for a task being reset, keep it so the agent can continue from existing code.
4. Create a new `ForgeDaemon` and call `execute()` with the reconstructed graph and existing `pipeline_id`.
5. Pipeline status set back to `executing`.

**UI:** "Resume" button shown on pipelines where:
- `status` is NOT `complete`
- At least one task is in a non-terminal state (`todo`, `in_progress`, `in_review`, `merging`)

### 4. Cost Tracking (P1)

**Data source:** `ResultMessage.total_cost_usd` from claude-code-sdk, captured in `adapter.py`.

**Storage:**
- Add `cost_usd REAL DEFAULT 0` to `TaskRow`
- Add `cost_usd REAL DEFAULT 0` to `PipelineRow`
- After each `sdk_query()` call (agent, reviewer, conflict resolver), add the returned cost to the task total via `Database.add_task_cost(task_id, cost)`.
- Pipeline cost = sum of task costs (computed on read, not stored separately to avoid drift).

**Event:** Emit `task:cost_update` with `{task_id, cost_usd, cumulative_cost_usd}` after each SDK call. Stored in `pipeline_events` for persistence.

**Display:** Per-task cost in the agent card footer. Pipeline total in the completion summary.

**Accuracy:** Store the raw `total_cost_usd` float from the SDK without rounding. Display as `$X.XXXX` (4 decimal places). This matches the Claude dashboard's per-session cost tracking.

### 5. Timeline View (P1)

**Free from Event Persistence.** The `pipeline_events` table IS the timeline.

**Frontend:** `TimelinePanel` component alongside the agent cards grid. Vertical list sorted by `created_at`, grouped by minute:

```
12:03  Planning started
12:03  Plan ready — 6 tasks
12:03  task-1 dispatched → agent-0
12:04  task-1: L1 passed ✅
12:04  task-1: L2 passed ✅
12:04  task-1: Merged +451/-0 ($0.0832)
```

Compact, color-coded by event type. Auto-scrolls to latest. Filter by task if needed.

### 6. Toast Notifications (P1)

**Already scaffolded:** `useNotifications` hook exists with `notify(title, body)`.

**Trigger points** in `taskStore.ts` WebSocket event handlers:
- `task:state_changed` → `done`: "Task completed: {title}"
- `task:state_changed` → `error`: "Task failed: {title}"
- `pipeline:phase_changed` → `complete`: "Pipeline complete"
- PR created: "PR created: {url}"

No backend changes needed — purely frontend wiring.

### 7. Retry/Cancel Controls (P1)

**Cancel pipeline:** `POST /tasks/{pipeline_id}/cancel`
- Sets all non-terminal tasks to `cancelled`
- Daemon's execution loop detects all-terminal state and exits
- Emits `pipeline:cancelled` event
- UI: Red "Cancel" button with confirmation dialog

**Retry single task:** `POST /tasks/{task_id}/retry`
- Resets one task to `todo`, resets `retry_count` to 0
- If pipeline is complete/cancelled, sets pipeline back to `executing`
- Re-enters execution loop for the pipeline
- UI: Retry button on task cards in `error` state

**Cancel running agent:** Track the subprocess PID from `sdk_query()`. On cancel, send SIGTERM. Requires a minor change to `sdk_helpers.py` to expose the process handle.

### 8. Task Detail Slide-out (P2)

Clicking a task card opens a right-side panel (60% viewport width) with:
- **Header:** Task title, state badge, cost
- **Agent Conversation:** Full output, scrollable, with `FormattedLine` rendering
- **Review Gates:** L1 and L2 results with full detail text
- **Merge Result:** Success/failure with stats
- **Files Changed:** List with file paths
- **Retry History:** Timeline of attempts with review feedback

Close with Escape key or clicking the overlay.

### 9. Settings Persistence & Model Controls (P2)

**Storage:** Add `settings_json TEXT` column to `UserRow`. Settings persist across server restarts.

**Model routing UI:** New "Model Routing" section on settings page:
- Planner model: dropdown (opus/sonnet/haiku), default: opus
- Agent model by complexity: 3 dropdowns, defaults: sonnet/opus/opus
- Reviewer model: dropdown, default: sonnet
- Strategy preset: radio (auto/fast/quality) that pre-fills the above

**Runtime integration:** `model_router.py`'s `select_model()` reads user settings from DB instead of hardcoded `_ROUTING_TABLE`. Falls back to defaults if no override set.

**Additional settings to persist:**
- max_agents (1-8), max_retries (0-5), agent_timeout (60-1800s), max_turns (5-50)

## NOT in scope

- Branch & diff preview with syntax highlighting (needs Figma design first)
- Email/webhook notifications (future)
- Multi-user pipeline isolation (current single-user is fine)

## Build Order

| Phase | Items | Rationale |
|-------|-------|-----------|
| P0 | Event persistence, Pre-flight checks, Pipeline resume | Foundation — everything else depends on events being in DB |
| P1 | Cost tracking, Timeline view, Toasts, Retry/Cancel | Observability and controls — all build on the events table |
| P2 | Task detail slide-out, Settings persistence | UX polish |
